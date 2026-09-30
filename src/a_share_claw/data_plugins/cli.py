"""Model-free JSON interface for external hosts and reproducible diagnostics."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from . import DataRun, default_registry
from ..db import Storage
from ..harness.contracts import FailureCategory, RunRequest, RunStatus, Scope, canonical
from ..harness.runtime import RunSession
from ..harness.trace import now


def add_data_parser(sub):
    parser = sub.add_parser("data", help="Use the five data plugins without a model or Telegram")
    commands = parser.add_subparsers(dest="data_command", required=True)
    commands.add_parser("plugins", help="List plugin capabilities and credential readiness")
    for name in ("plan", "fetch"):
        command = commands.add_parser(name, help="Validate a plan" if name == "plan" else "Fetch only the declared requirements")
        command.add_argument("plan_file", type=Path)
    return parser


async def run_data(args, config) -> int:
    storage = Storage(config.database_path)
    storage.init()
    context = storage.get_or_create_context("local", "local-user", "local-chat")
    trace = RunSession(storage, RunRequest(Scope.from_context(config.root_dir, context), "Data CLI " + args.data_command, host="cli"))
    run = DataRun(default_registry().snapshot(), config.data_dir / "plugin_runs", canonical(trace.request.scope.__dict__), run_id=trace.run_id)
    trace.step("route", {"workflow": "data_acquisition"})
    trace.step("context", {"loaded_files": [], "missing_files": [], "state_injected": False})
    try:
        if args.data_command == "plugins":
            async def providers():
                return {"ok": True, "data": run.list_providers()}
            envelope = await trace.tool("list_data_plugins", {}, providers, {"list_data_plugins"})
            outcome = trace.finish(canonical({"run_id": trace.run_id, "plugins": envelope["data"]}))
            print(outcome.output)
            return 0
        run.plan(json.loads(args.plan_file.read_text(encoding="utf-8")))
        trace.step("data_plan", {"requirements": [r.json() for r in run.requirements.values()]})
        results = {}
        if args.data_command == "fetch":
            for requirement_id in run.requirements:
                async def fetch():
                    return await run.fetch(requirement_id)
                results[requirement_id] = await trace.tool("fetch_data", {"requirement_id": requirement_id}, fetch, {"fetch_data"})
        summary = run.summary()
        run._save("summary.json", summary)
        for path in sorted(run.directory.rglob("*.json")):
            trace.repository.append("artifacts", trace.run_id, trace.request.scope,
                detail_json=canonical({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                       "mode": "research", "scope_key": trace.request.scope.key}), recorded_at=now())
        trace.step("gap_report", summary)
        status = RunStatus.SUCCEEDED if args.data_command == "plan" or summary["required_data_complete"] else RunStatus.BLOCKED
        outcome = trace.finish(canonical({**summary, "artifact_directory": str(run.directory), "results": results}), status)
        print(outcome.output)
        return 0 if outcome.status == RunStatus.SUCCEEDED else 2
    except (OSError, ValueError, TypeError):
        trace.failure = FailureCategory.TOOL_RETURN_FAILURE
        outcome = trace.finish(canonical({"ok": False, "run_id": trace.run_id, "error_code": "invalid_plan"}), RunStatus.FAILED)
        print(outcome.output)
        return 2
    except asyncio.CancelledError:
        trace.finish("NO_ACTION", RunStatus.CANCELLED)
        raise
    except Exception:
        trace.failure = FailureCategory.SYNTHESIS_OR_UNKNOWN_FAILURE
        outcome = trace.finish(canonical({"ok": False, "run_id": trace.run_id, "error_code": "execution_error"}))
        print(outcome.output)
        return 2
    finally:
        storage.close()
