"""Versioned SQLite trace, scoped reads and atomic official publication."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .contracts import EvalResult, RunOutcome, RunRequest, RunStatus, Scope, canonical, digest, redact


def now():
    return datetime.now(timezone.utc).isoformat()


MIGRATIONS = {
    1: (
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, scope_json TEXT NOT NULL, request_json TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, outcome_json TEXT)",
        "CREATE INDEX idx_runs_scope ON runs(scope_key, started_at)",
        "CREATE TABLE run_steps (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), stage TEXT NOT NULL, status TEXT NOT NULL, recorded_at TEXT NOT NULL, detail_json TEXT NOT NULL)",
        "CREATE TABLE tool_calls (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), tool_name TEXT NOT NULL, arguments_hash TEXT NOT NULL, permission TEXT NOT NULL, envelope_json TEXT NOT NULL, recorded_at TEXT NOT NULL)",
        "CREATE TABLE model_calls (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), status TEXT NOT NULL, detail_json TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT)",
        "CREATE TABLE artifacts (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), detail_json TEXT NOT NULL, recorded_at TEXT NOT NULL)",
        "CREATE TABLE evaluations (id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), passed INTEGER NOT NULL, hard_gate INTEGER NOT NULL, detail_json TEXT NOT NULL, recorded_at TEXT NOT NULL)",
        "CREATE TABLE improvement_proposals (id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id), status TEXT NOT NULL DEFAULT 'proposed', detail_json TEXT NOT NULL)",
        "CREATE TABLE official_states (scope_key TEXT NOT NULL, workflow TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id), as_of_date TEXT NOT NULL, state_json TEXT NOT NULL, PRIMARY KEY(scope_key, workflow))",
    ),
    2: (
        "CREATE TABLE official_state_history (scope_key TEXT NOT NULL, workflow TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id), as_of_date TEXT NOT NULL, state_json TEXT NOT NULL, PRIMARY KEY(scope_key,workflow,run_id))",
        "CREATE INDEX idx_official_history_cutoff ON official_state_history(scope_key,workflow,as_of_date)",
        "INSERT INTO official_state_history SELECT scope_key,workflow,run_id,as_of_date,state_json FROM official_states",
    ),
}


def migrate(connection):
    connection.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
    for version, statements in MIGRATIONS.items():
        checksum = digest(statements)
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT checksum FROM schema_migrations WHERE version=?", (version,)).fetchone()
            if existing:
                if existing[0] != checksum:
                    raise RuntimeError("Migration checksum mismatch")
                connection.commit()
                continue
            for statement in statements:
                connection.execute(statement)
            connection.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (version, checksum, now()))
            connection.commit()
        except Exception:
            connection.rollback()
            raise


class TraceRepository:
    def __init__(self, storage):
        self.storage = storage

    def _authorized(self, run_id, scope, running=False):
        row = self.storage._conn.execute("SELECT * FROM runs WHERE run_id=? AND scope_key=?", (run_id, scope.key)).fetchone()
        if row is None:
            raise LookupError("Run not found in the authorized scope")
        if running and row["status"] != RunStatus.RUNNING.value:
            raise ValueError("Run is already terminal")
        return row

    def begin(self, request: RunRequest) -> str:
        run_id = uuid4().hex
        metadata = {"message_hash": digest(request.message), "message_chars": len(request.message),
                    "as_of_date": request.as_of_date, "mode": request.mode, "workflow": request.workflow,
                    "host": request.host, "budget": {"wall_clock_seconds": request.wall_clock_seconds,
                                                    "max_tool_calls": request.max_tool_calls}}
        with self.storage._lock, self.storage._conn:
            self.storage._conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,NULL,NULL)",
                (run_id, request.scope.key, canonical(redact(request.scope.__dict__)),
                 canonical(metadata), RunStatus.RUNNING.value, now()))
        return run_id

    def append(self, table, run_id, scope, **fields):
        if table not in {"run_steps", "tool_calls", "model_calls", "artifacts", "evaluations"}:
            raise ValueError("Invalid trace table")
        allowed = {"run_steps": {"stage", "status", "detail_json", "recorded_at"},
                   "tool_calls": {"tool_name", "arguments_hash", "permission", "envelope_json", "recorded_at"},
                   "model_calls": {"status", "detail_json", "started_at", "finished_at"},
                   "artifacts": {"detail_json", "recorded_at"},
                   "evaluations": {"passed", "hard_gate", "detail_json", "recorded_at"}}
        if set(fields) - allowed[table]:
            raise ValueError("Invalid trace fields")
        fields = {key: canonical(redact(json.loads(value))) if key.endswith("_json") else value
                  for key, value in fields.items()}
        with self.storage._lock, self.storage._conn:
            self._authorized(run_id, scope, running=True)
            values = {"run_id": run_id, **fields}
            columns = ",".join(values)
            row = self.storage._conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))
            return row.lastrowid

    def step(self, run_id, scope, stage, detail, status="ok"):
        return self.append("run_steps", run_id, scope, stage=stage, status=status,
                           detail_json=canonical(redact(detail)), recorded_at=now())

    def evaluate(self, run_id, scope, result: EvalResult):
        return self.append("evaluations", run_id, scope, passed=int(result.passed), hard_gate=int(result.hard_gate),
                           detail_json=canonical(redact(result.json())), recorded_at=now())

    def model_end(self, run_id, scope, call_id, detail, status="ok"):
        with self.storage._lock, self.storage._conn:
            self._authorized(run_id, scope, running=True)
            cursor = self.storage._conn.execute("UPDATE model_calls SET status=?,detail_json=?,finished_at=? WHERE id=? AND run_id=? AND finished_at IS NULL",
                            (status, canonical(redact(detail)), now(), call_id, run_id))
            if cursor.rowcount != 1:
                raise ValueError("Model call is not open in this run")

    def finish(self, outcome: RunOutcome, scope: Scope, state=None, as_of_date=None, *, terminal_boundary=None):
        if outcome.status == RunStatus.RUNNING:
            raise ValueError("Terminal status required")
        if outcome.status != RunStatus.SUCCEEDED and (outcome.official_output_allowed or outcome.action != "NO_ACTION"):
            raise ValueError("Unsuccessful runs cannot publish an action")
        if outcome.official_output_allowed and state is None:
            raise ValueError("Official output requires an atomic state publication")
        # Do not persist free model prose as trace; its hash can audit delivery.
        record = {**outcome.json(), "output": {"sha256": digest(outcome.output), "chars": len(outcome.output)}}
        with self.storage._lock, self.storage._conn:
            row = self._authorized(outcome.run_id, scope, running=True)
            if state is not None:
                request = json.loads(row["request_json"])
                checks = self.storage._conn.execute("SELECT passed,detail_json FROM evaluations WHERE run_id=? AND hard_gate=1", (outcome.run_id,)).fetchall()
                required_checks = {"frozen_plan", "policy_snapshot", "required_evidence", "scope_and_date", "report_contract", "report_markdown_contract"}
                completed_checks = {json.loads(check["detail_json"])["evaluator"] for check in checks if check["passed"]}
                if (outcome.status != RunStatus.SUCCEEDED or not outcome.official_output_allowed or
                        request["mode"] != "official" or request["as_of_date"] != as_of_date or
                        not required_checks <= completed_checks or any(not check["passed"] for check in checks)):
                    raise ValueError("Official promotion denied")
                if state.get("as_of_date") != as_of_date or state.get("run_id") != outcome.run_id:
                    raise ValueError("Official state identity mismatch")
                report = state.get("report", {})
                if report.get("scope_key") != scope.key or report.get("run_id") != outcome.run_id:
                    raise ValueError("Official state requires its own scoped report")
                artifacts = self.storage._conn.execute("SELECT detail_json FROM artifacts WHERE run_id=?", (outcome.run_id,)).fetchall()
                if not any(json.loads(item[0]) == report for item in artifacts):
                    raise ValueError("Official report is not archived in this run")
                markdown = state.get("report_markdown", {})
                if (markdown.get("scope_key") != scope.key or markdown.get("run_id") != outcome.run_id or
                        not any(json.loads(item[0]) == markdown for item in artifacts)):
                    raise ValueError("Official Markdown report is not archived in this run")
                html_report = state.get("report_html")
                if html_report is not None and ("report_html_contract" not in completed_checks or
                        html_report.get("scope_key") != scope.key or html_report.get("run_id") != outcome.run_id or
                        not any(json.loads(item[0]) == html_report for item in artifacts)):
                    raise ValueError("Official HTML report is not evaluated and archived in this run")
                workflow = state["workflow"]
                route = self.storage._conn.execute("SELECT detail_json FROM run_steps WHERE run_id=? AND stage='route' ORDER BY id DESC LIMIT 1", (outcome.run_id,)).fetchone()
                if route is None or json.loads(route[0]).get("workflow") != workflow:
                    raise ValueError("Official workflow does not match the run route")
                previous = self.storage._conn.execute("SELECT as_of_date FROM official_states WHERE scope_key=? AND workflow=?", (scope.key, workflow)).fetchone()
                if previous and previous[0] > as_of_date:
                    raise ValueError("Cannot replace a newer official state")
                self.storage._conn.execute("INSERT INTO official_state_history VALUES (?,?,?,?,?)", (scope.key, workflow, outcome.run_id, as_of_date, canonical(redact(state))))
                self.storage._conn.execute("INSERT INTO official_states VALUES (?,?,?,?,?) ON CONFLICT(scope_key,workflow) DO UPDATE SET run_id=excluded.run_id,as_of_date=excluded.as_of_date,state_json=excluded.state_json",
                                            (scope.key, workflow, outcome.run_id, as_of_date, canonical(redact(state))))
            if terminal_boundary is not None:
                self.storage._conn.execute("INSERT INTO run_steps (run_id,stage,status,recorded_at,detail_json) VALUES (?,?,?,?,?)",
                    (outcome.run_id, "react_phase", outcome.status.value, now(), canonical(redact(terminal_boundary))))
            self.storage._conn.execute("UPDATE runs SET status=?,finished_at=?,outcome_json=? WHERE run_id=?",
                                        (outcome.status.value, now(), canonical(record), outcome.run_id))
            self.storage._conn.execute("UPDATE model_calls SET status='interrupted',finished_at=? WHERE run_id=? AND finished_at IS NULL", (now(), outcome.run_id))

    def read(self, run_id, scope):
        with self.storage._lock:
            row = dict(self._authorized(run_id, scope))
            for key in ("scope_json", "request_json", "outcome_json"):
                value = row.pop(key)
                row[key[:-5]] = json.loads(value) if value is not None else None
            for table in ("run_steps", "tool_calls", "model_calls", "artifacts", "evaluations"):
                rows = [dict(r) for r in self.storage._conn.execute(f"SELECT * FROM {table} WHERE run_id=? ORDER BY id", (run_id,))]
                for item in rows:
                    for key in list(item):
                        if key.endswith("_json"):
                            item[key[:-5]] = json.loads(item.pop(key))
                row[table] = rows
            return row

    def list_runs(self, scope, limit=20):
        with self.storage._lock:
            return [dict(row) for row in self.storage._conn.execute("SELECT run_id,status,started_at,finished_at FROM runs WHERE scope_key=? ORDER BY started_at DESC LIMIT ?", (scope.key, min(max(int(limit), 1), 100)))]

    def read_state(self, scope, workflow, as_of_date=None):
        with self.storage._lock:
            if as_of_date is not None:
                from .contracts import validate_date
                validate_date(as_of_date)
            row = self.storage._conn.execute("SELECT state_json FROM official_state_history WHERE scope_key=? AND workflow=? AND (? IS NULL OR as_of_date<=?) ORDER BY as_of_date DESC,rowid DESC LIMIT 1", (scope.key, workflow, as_of_date, as_of_date)).fetchone()
            return json.loads(row[0]) if row else None

    def published_state(self, run_id, scope):
        """Read the exact publication, including an older run on the same date."""
        with self.storage._lock:
            self._authorized(run_id, scope)
            row = self.storage._conn.execute("SELECT state_json FROM official_state_history WHERE scope_key=? AND run_id=?", (scope.key,run_id)).fetchone()
            return json.loads(row[0]) if row else None
