"""E0 acceptance: real core, fixed facts, no provider or model credentials."""
import asyncio
import copy
import csv
import dataclasses
import json
import math
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from a_share_claw.config import AppConfig
from a_share_claw.db import Storage
from a_share_claw.harness.contracts import EvalResult, FailureCategory, RunOutcome, RunRequest, RunStatus, Scope, ToolResult, canonical, digest
from a_share_claw.harness.engine import Harness, MACRO_UNITS
from a_share_claw.harness.policy import PolicyBundle
from a_share_claw.harness.runtime import RunSession
from a_share_claw.harness.trace import TraceRepository, now

ROOT = Path(__file__).resolve().parents[1]
DAY = "2026-07-13"


@pytest.fixture
def environment(tmp_path):
    storage = Storage(tmp_path / "trace.sqlite")
    storage.init()
    scope = Scope(str(ROOT), "local:test", "chat:session")
    yield storage, scope, Harness(ROOT, storage, tmp_path / "artifacts"), tmp_path
    storage.close()


def packet(scope):
    us = dict(vix=18, ust_10y=4.4, ust_30y=4.8, brent=75, cpi_us_yoy=2.6, core_cpi_yoy=2.8,
              ppi_us_yoy=2.5, fed_rate=4, nfp_actual=120, unemployment=4.2, credit_spread=1.5,
              credit_spread_change_20d=-.12, jp_jgb_10y=1.5, usdjpy=150)
    cn = dict(pmi_mfg=50.2, pmi_non_mfg=50.5, retail_sales_yoy=3, m2_yoy=8.5, m1_yoy=5)
    histories = {}
    for index, symbol in enumerate(("159682.SZ", "QQQ.US")):
        rows, day, price, count = [], datetime(2024, 1, 2), 100 + index * 5, 0
        while day <= datetime(2026, 7, 13):
            if day.weekday() < 5:
                price *= 1 + .0003 + math.sin(count / (9 + index)) * .004 + index * .00003
                rows.append({"trade_date": day.date().isoformat(), "close": price})
                count += 1
            day += timedelta(days=1)
        histories[symbol] = rows
    facts = []
    for capability, data in (("us_macro", us), ("cn_macro", cn), ("market_history", histories)):
        p = {"source": "synthetic acceptance fixture", "source_file": "fixture:" + capability,
             "source_timestamp": DAY + "T15:30:00Z", "publication_date": DAY, "observation_date": DAY,
             "data_period": "fixed acceptance window", "sha256": digest(data)}
        if capability in MACRO_UNITS:
            p["units"] = MACRO_UNITS[capability]
        else:
            p.update(frequency="daily", adjustments={"159682.SZ": "QFQ", "QQQ.US": "NONE"},
                     currencies={"159682.SZ": "CNY", "QQQ.US": "USD"})
        facts.append({"capability": capability, "scope_key": scope.key, "data": data,
                      "provenance": p, "fallback_status": "none"})
    return {"as_of_date": DAY, "facts": facts}


def request(scope, mode="replay", workflow="macro", day=DAY):
    return RunRequest(scope, "Fixed macro acceptance", day, mode, workflow)


def ai_packet(scope):
    market = packet(scope)["facts"][2]["data"]
    dates = [r["trade_date"] for r in market["159682.SZ"]]
    growth = {"aggregate": {
        "capex": {"yoy": .30, "companies_observed": 5},
        "operating_cash_flow": {"yoy": .20, "companies_observed": 5},
        "revenue": {"yoy": .15, "companies_observed": 5}, "operating_income": {"yoy": .25},
        "funding": {"companies_observed": 5, "capex_to_ocf": .70, "capex_growth_minus_ocf_growth": .10}},
        "data_audit": {"company_count": 5, "requested_company_count": 5}}
    bases = {"vix": 18, "ust_2y": 4, "ust_10y": 4.4, "ust_30y": 4.8, "tips_10y": 2,
             "broad_dollar": 120, "credit_spread": 1.5, "nfci": -.4, "core_cpi": 300,
             "ppi_final_demand": 120, "core_pce": 110, "china_cpi_yoy": 1, "china_ppi_yoy": 1, "brent": 70}
    macro = {"histories": {name: {"observations": [{"observation_date": day, "value": base + math.sin(i/17)*.02}
             for i, day in enumerate(dates)]} for name, base in bases.items()}}
    cn = {"margin_history": [{"trade_date": day, "financing_balance_yuan": 1e10*(1+i/1000),
                              "financing_buy_yuan": 1e8*(1+math.sin(i/11)*.01)} for i, day in enumerate(dates)],
          "option_history": [{"trade_date": day, "growth_put_call_volume": 1+math.sin(i/9)*.1} for i, day in enumerate(dates)]}
    result = {"as_of_date": DAY, "facts": []}
    for capability, data in (("ai_growth", growth), ("ai_market", {"159819.SZ": market["159682.SZ"], "510300.SH": market["QQQ.US"]}),
                             ("ai_macro", macro), ("ai_cn", cn)):
        result["facts"].append({"capability": capability, "scope_key": scope.key, "data": data, "fallback_status": "none",
            "provenance": {"source": "synthetic fixture", "source_file": "fixture:"+capability,
                "source_timestamp": DAY+"T15:30:00Z", "publication_date": DAY, "observation_date": DAY,
                "data_period": "fixed fixture", "sha256": digest(data), "schema_version": "ai-inputs-v1"}})
    return result


def test_migration_preserves_existing_history_and_is_idempotent(environment):
    storage, scope, _, _ = environment
    context = storage.get_or_create_context("local", "1", "chat")
    storage.add_message(context.conversation_id, "user", "existing history")
    storage.init()
    assert storage._conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 2
    assert storage._conn.execute("SELECT content FROM messages").fetchone()[0] == "existing history"
    assert {r[0] for r in storage._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {
        "runs", "run_steps", "model_calls", "tool_calls", "artifacts", "evaluations", "improvement_proposals", "official_states"}


def test_zero_provider_plan_succeeds_and_cannot_publish(environment):
    storage, scope, engine, _ = environment
    outcome = engine.run(request(scope, "plan"))
    assert outcome.status == RunStatus.SUCCEEDED and not outcome.official_output_allowed
    result = json.loads(outcome.output)
    assert set(result["gaps"]) == {"cn_macro", "us_macro", "market_history"}
    assert result["plan"]["disabled_layers"] == ["L2"]
    assert storage._conn.execute("SELECT count(*) FROM official_states").fetchone()[0] == 0


def test_complete_fact_packet_computes_without_network_and_traces_all_stages(environment):
    storage, scope, engine, _ = environment
    with patch("urllib.request.build_opener", side_effect=AssertionError("core must not fetch")):
        outcome = engine.run(request(scope), packet(scope))
    assert outcome.status == RunStatus.SUCCEEDED
    assert outcome.action == "NO_ACTION" and not outcome.official_output_allowed
    data = json.loads(outcome.output)["data"]
    assert data["L2"] is None and data["L2_status"] == "disabled"
    assert data["composite"] == round(data["L1"] * 4/7 + data["L3"] * 3/7, 2)
    trace = TraceRepository(storage).read(outcome.run_id, scope)
    stages = {row["stage"] for row in trace["run_steps"]}
    assert stages >= {"request", "route", "context", "plan", "policy_snapshot", "evidence", "gap_report", "compute", "publish_gate", "final_output"}
    assert len(trace["artifacts"]) == 7 and all(row["passed"] for row in trace["evaluations"])
    context = next(row["detail"] for row in trace["run_steps"] if row["stage"] == "context")
    assert context["kind"] == "policy_snapshot"
    assert set(context["loaded_files"]) == set(PolicyBundle(ROOT).hashes)
    assert context["missing_files"] == [] and context["state_injected"] is False


@pytest.mark.parametrize("missing_file", ["IDENTITY.md", "src/compiled/weight_matrix.json", "src/a_share_claw/RESEARCH_OPERATIONS.md"])
def test_missing_core_policy_preserves_route_and_context_without_publication(environment, missing_file):
    storage, scope, engine, tmp = environment
    checkout = tmp / "incomplete-checkout"
    for name in PolicyBundle(ROOT).hashes:
        destination = checkout / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / name).read_bytes())
    (checkout / missing_file).unlink()
    engine.root = checkout
    outcome = engine.run(request(scope, "official"), packet(scope))
    assert outcome.status == RunStatus.BLOCKED
    assert outcome.category == FailureCategory.CONTEXT_TRUNCATION_FAILURE
    assert outcome.action == "NO_ACTION" and not outcome.official_output_allowed
    assert json.loads(outcome.output)["error_code"] == "policy_context_missing"
    repository = TraceRepository(storage)
    trace = repository.read(outcome.run_id, scope)
    assert any(row["stage"] == "route" for row in trace["run_steps"])
    context = next(row["detail"] for row in trace["run_steps"] if row["stage"] == "context")
    assert context["missing_files"] == [missing_file]
    assert missing_file not in context["loaded_files"]
    assert context["state_scope"] == scope.key and context["state_injected"] is False
    assert not trace["artifacts"] and repository.read_state(scope, "macro") is None


@pytest.mark.parametrize("mutation,code", [
    ("missing", "missing_required_data"), ("unverified", "unverified_evidence"),
    ("scope", "scope_mismatch"), ("hash", "hash_mismatch"),
    ("future_publication", "future_data"), ("future_row", "future_data"),
    ("missing_provenance", "missing_provenance"), ("missing_metric", "missing_required_field:us_macro.vix"),
    ("units", "invalid_schema"), ("adjustment", "invalid_schema"),
    ("sparse", "insufficient_coverage:market_history.159682.SZ"),
    ("boolean", "invalid_schema"), ("nan", "invalid_schema"),
])
def test_hard_gates_never_publish(environment, mutation, code):
    storage, scope, engine, _ = environment
    p = packet(scope)
    if mutation == "missing": p["facts"].pop()
    elif mutation == "unverified": p["facts"][0]["fallback_status"] = "unverified"
    elif mutation == "scope": p["facts"][0]["scope_key"] = "another-user"
    elif mutation == "hash": p["facts"][0]["provenance"]["sha256"] = "bad"
    elif mutation == "future_publication": p["facts"][0]["provenance"]["publication_date"] = "2026-07-14"
    elif mutation == "missing_provenance": del p["facts"][0]["provenance"]["source_timestamp"]
    elif mutation == "units": p["facts"][0]["provenance"]["units"] = {"nfp_actual": "persons"}
    elif mutation == "adjustment": p["facts"][2]["provenance"]["adjustments"] = {}
    else:
        item = p["facts"][0] if mutation in {"missing_metric", "boolean", "nan"} else p["facts"][2]
        if mutation == "missing_metric": del item["data"]["vix"]
        elif mutation == "boolean": item["data"]["vix"] = True
        elif mutation == "nan": item["data"]["vix"] = "NaN"
        elif mutation == "sparse": item["data"]["159682.SZ"] = item["data"]["159682.SZ"][-20:]
        elif mutation == "future_row": item["data"]["159682.SZ"][-1]["trade_date"] = "2026-07-14"
        item["provenance"]["sha256"] = digest(item["data"])
    outcome = engine.run(request(scope, "official"), p)
    assert outcome.status == RunStatus.BLOCKED and outcome.action == "NO_ACTION"
    assert json.loads(outcome.output)["error_code"] == code
    assert storage._conn.execute("SELECT count(*) FROM official_states").fetchone()[0] == 0


def test_official_publish_is_scoped_and_cannot_rewind(environment):
    storage, scope, engine, _ = environment
    outcome = engine.run(request(scope, "official"), packet(scope))
    assert outcome.status == RunStatus.SUCCEEDED and outcome.official_output_allowed
    row = storage._conn.execute("SELECT * FROM official_states").fetchone()
    assert row["scope_key"] == scope.key and row["run_id"] == outcome.run_id
    storage._conn.execute("UPDATE official_states SET as_of_date='2026-07-14'")
    storage._conn.commit()
    rejected = engine.run(request(scope, "official"), packet(scope))
    assert rejected.status == RunStatus.BLOCKED
    assert storage._conn.execute("SELECT as_of_date FROM official_states").fetchone()[0] == "2026-07-14"


def test_same_day_preclose_and_future_requests_are_blocked(environment):
    _, scope, engine, _ = environment
    before_close = datetime(2026, 7, 13, 6, tzinfo=timezone.utc)
    result = engine.run(request(scope, "official"), packet(scope), clock=before_close)
    assert json.loads(result.output)["error_code"] == "WAIT_FOR_CLOSE"
    result = engine.run(request(scope, "official", day="2099-01-01"), clock=before_close)
    assert json.loads(result.output)["error_code"] == "future_data"
    result = engine.run(request(scope, "official", day="2026-07-12"), clock=before_close)
    assert json.loads(result.output)["error_code"] == "WAIT_FOR_TRADING_DAY"


def test_scope_read_and_write_denials_do_not_disclose_run(environment):
    storage, scope, engine, _ = environment
    result = engine.run(request(scope, "plan"))
    other = dataclasses.replace(scope, principal="local:other")
    repo = TraceRepository(storage)
    assert repo.list_runs(other) == []
    with pytest.raises(LookupError): repo.read(result.run_id, other)
    with pytest.raises(LookupError): repo.step(result.run_id, other, "injected", {})
    with pytest.raises(ValueError): repo.step(result.run_id, scope, "late_write", {})


def test_structured_tool_permission_timeout_budget_and_redaction(environment):
    storage, scope, _, _ = environment
    async def exercise():
        session = RunSession(storage, RunRequest(scope, "secret request not saved", max_tool_calls=2, wall_clock_seconds=.03))
        async def forbidden(): raise AssertionError("must not execute")
        denied = await session.tool("bash", {}, forbidden, {"fetch"})
        assert denied["error_code"] == "expected_denial"
        async def timeout(): await asyncio.sleep(1)
        timed = await session.tool("fetch", {"api_key": "private-marker"}, timeout, {"fetch"})
        assert timed["error_code"] == "timeout"
        result = await session.tool("fetch", {}, forbidden, {"fetch"})
        assert result["error_code"] == "budget_exceeded"
        outcome = session.finish("NO_ACTION")
        assert outcome.status == RunStatus.FAILED and outcome.category == FailureCategory.TIMEOUT_OR_BUDGET_FAILURE
        trace = TraceRepository(storage).read(outcome.run_id, scope)
        assert trace["tool_calls"][0]["permission"] == "expected_denial"
        assert "private-marker" not in canonical(trace) and "secret request not saved" not in canonical(trace)
        assert trace["evaluations"][0]["passed"]
    asyncio.run(exercise())


def test_contradictory_envelope_is_rejected():
    with pytest.raises(ValueError): ToolResult(True, "error")
    result = ToolResult.from_legacy({"ok": False, "status": "gap", "error_code": "not_configured"}).json()
    assert set(result) >= {"ok", "status", "error_code", "retryable", "data", "provenance", "fallback_status", "truncated", "usage"}
    assert isinstance(result["provenance"], list)


def test_legal_requirement_names_cannot_collide(tmp_path):
    from a_share_claw.data_plugins import DataRun
    async def exercise():
        run = DataRun({}, tmp_path, "scope")
        run.plan({"framework": "Test names", "requirements": [dict(requirement_id=name, provider="fred", capability="macro.series", as_of_date=DAY) for name in ("summary", "plan-2")]})
        await run.fetch("summary")
        await run.fetch("plan-2")
        run._save("summary.json", run.summary())
        assert (run.directory / "results/summary.json").is_file()
        assert (run.directory / "results/plan-2.json").is_file()
        assert run.summary()["gaps"][0]["error_code"] == "provider_unavailable"
    asyncio.run(exercise())


def test_ai_raw_inputs_execute_policy_and_keep_macro_state_separate(environment):
    storage, scope, engine, _ = environment
    macro = engine.run(request(scope, "official"), packet(scope))
    ai = engine.run(request(scope, "official", "ai"), ai_packet(scope), current_ai_pct=63)
    assert ai.status == RunStatus.SUCCEEDED, ai.output
    data = json.loads(ai.output)["data"]
    assert data["factors"]["growth"]["regime"] == "expansion"
    assert data["decision"]["action"] != "NO_ACTION"
    repo = TraceRepository(storage)
    assert repo.read_state(scope, "macro")["data"] == json.loads(macro.output)["data"]
    assert repo.read_state(scope, "ai")["data"] == data
    assert storage._conn.execute("SELECT count(*) FROM official_states").fetchone()[0] == 2


def test_ai_insufficient_coverage_does_not_become_neutral(environment):
    storage, scope, engine, _ = environment
    facts = ai_packet(scope)
    item = facts["facts"][0]
    item["data"]["aggregate"] = {}
    item["provenance"]["sha256"] = digest(item["data"])
    outcome = engine.run(request(scope, "official", "ai"), facts)
    assert outcome.status == RunStatus.BLOCKED and outcome.action == "NO_ACTION"
    assert json.loads(outcome.output)["error_code"] == "insufficient_coverage"
    assert TraceRepository(storage).read_state(scope, "ai") is None


def test_archive_failure_revokes_successful_computation(environment):
    storage, scope, engine, _ = environment
    archive = engine._archive
    def fail_on_output(session, name, obj):
        if name == "computed_output": raise OSError("disk failure")
        return archive(session, name, obj)
    with patch.object(engine, "_archive", side_effect=fail_on_output):
        outcome = engine.run(request(scope, "official"), packet(scope))
    assert outcome.status == RunStatus.FAILED
    output = json.loads(outcome.output)
    assert output["official_output_allowed"] is False and output["action"] == "NO_ACTION"
    assert TraceRepository(storage).read_state(scope, "macro") is None


def test_migration_failure_rolls_back_and_modified_version_is_rejected(environment, monkeypatch):
    from a_share_claw.harness import trace
    storage, _, _, _ = environment
    monkeypatch.setattr(trace, "MIGRATIONS", {3: ("CREATE TABLE partial_upgrade (id INTEGER)", "INVALID SQL")})
    with pytest.raises(sqlite3.OperationalError): trace.migrate(storage._conn)
    assert not storage._conn.execute("SELECT name FROM sqlite_master WHERE name='partial_upgrade'").fetchall()
    assert storage._conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 2
    monkeypatch.setattr(trace, "MIGRATIONS", {1: ("changed historical migration",)})
    with pytest.raises(RuntimeError, match="checksum"): trace.migrate(storage._conn)


def test_evaluator_blocks_atomic_official_state_publication(environment):
    storage, scope, _, _ = environment
    session = RunSession(storage, request(scope, "official"))
    session.evaluate(EvalResult("failed_gate", False))
    with pytest.raises(ValueError, match="promotion"):
        session.finish("candidate", official=True, action="POSITION_BAND", state={"workflow": "macro"})
    assert TraceRepository(storage).read_state(scope, "macro") is None
    assert TraceRepository(storage).read(session.run_id, scope)["status"] == "running"
    session.finish("NO_ACTION", RunStatus.BLOCKED)


def test_nested_unverified_ai_audit_cannot_be_promoted(environment):
    storage, scope, engine, _ = environment
    facts = ai_packet(scope)
    item = facts["facts"][0]
    item["data"]["data_audit"]["fallback_status"] = "unverified"
    item["provenance"]["sha256"] = digest(item["data"])
    outcome = engine.run(request(scope, "official", "ai"), facts)
    assert json.loads(outcome.output)["error_code"] == "unverified_evidence"
    assert outcome.status == RunStatus.BLOCKED and TraceRepository(storage).read_state(scope, "ai") is None


def test_stale_a_share_close_cannot_be_silently_used(environment):
    storage, scope, engine, _ = environment
    facts = packet(scope)
    item = facts["facts"][2]
    item["data"]["159682.SZ"].pop()
    item["provenance"]["sha256"] = digest(item["data"])
    outcome = engine.run(request(scope, "official"), facts)
    assert outcome.status == RunStatus.BLOCKED and outcome.action == "NO_ACTION"
    assert TraceRepository(storage).read_state(scope, "macro") is None


def test_frozen_plan_pins_identity_and_archives_before_evidence(environment):
    from a_share_claw.harness.planning import freeze_plan
    storage, scope, engine, _ = environment
    req = request(scope, "plan", "industry")
    frozen = freeze_plan(req, PolicyBundle(ROOT).plan("industry"), {"research_spec": None})
    copy = frozen.json()
    copy["requirements"].clear()
    assert frozen.json()["requirements"]
    assert frozen.json()["scope_key"] == scope.key and frozen.json()["as_of_date"] == DAY
    assert frozen.json()["mode"] == "plan" and frozen.json()["debate_policy"] == "conditional_after_shared_evidence"
    assert all(r["provider"] is None for r in frozen.json()["requirements"])
    outcome = engine.run(req)
    assert outcome.status == RunStatus.SUCCEEDED and outcome.action == "NO_ACTION"
    trace = TraceRepository(storage).read(outcome.run_id, scope)
    plan = json.loads(outcome.output)["plan"]
    assert plan["plan_id"] == frozen.plan_id
    archived = [json.loads(Path(a["detail"]["path"]).read_text()) for a in trace["artifacts"]]
    assert archived == [plan] and not trace["tool_calls"]
    assert "workflow_executed" in plan["completion_criteria"]


def test_quant_plan_declares_missing_spec_instead_of_assuming_macro_universe(environment):
    _, scope, engine, _ = environment
    outcome = engine.run(request(scope, "plan", "quant"))
    plan = json.loads(outcome.output)["plan"]
    assert set(plan["unresolved_constraints"]) == {"universe", "window", "adjustment", "benchmark", "metric_definitions"}
    assert plan["debate_policy"] == "disabled" and not outcome.official_output_allowed


def test_mutated_frozen_plan_cannot_publish_after_valid_computation(environment):
    storage, scope, engine, _ = environment
    original = engine._evidence
    def tamper(session, policy, plan, packet, cutoff):
        result = original(session, policy, plan, packet, cutoff)
        plan["report_sections"].clear()
        return result
    with patch.object(engine, "_evidence", side_effect=tamper):
        outcome = engine.run(request(scope, "official"), packet(scope))
    assert outcome.status == RunStatus.BLOCKED and outcome.action == "NO_ACTION"
    assert json.loads(outcome.output)["error_code"] == "plan_changed"
    assert TraceRepository(storage).read_state(scope, "macro") is None
