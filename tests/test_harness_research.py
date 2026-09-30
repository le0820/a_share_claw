"""Core business acceptance uses declared synthetic evidence, never live providers."""
import copy
import json
from threading import Event
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_claw.harness.contracts import FailureCategory, RunRequest, RunStatus, Scope, digest
from a_share_claw.harness.trace import TraceRepository
from test_harness import DAY, environment, packet, request

VIEWS = {
    "ge_yan": "Capacity is a technical constraint that should be monitored.",
    "jia_zhi": "Capacity and cost jointly constrain value capture.",
    "qian_zhan": "Added capacity could improve value capture if demand absorbs it.",
    "shen_du": "Added capacity could weaken pricing if demand fails to absorb it.",
    "ping_heng": "This evidence supports a bounded research interpretation; monitor capacity and cost.",
}


def spec(technical=False, debate=False):
    roles = (["ge_yan"] if technical else []) + ["jia_zhi"] + (["qian_zhan", "shen_du"] if debate else []) + ["ping_heng"]
    return {"subject": "Synthetic supplier value-chain study", "technical_required": technical,
            "debate_required": debate, "debate_reason": "Demand absorption can improve or weaken value capture." if debate else "",
            "required_facts": [{"fact_id": key, "entity": "synthetic_supplier", "metric": key, "unit": unit, "data_period": "fixed synthetic quarter", "value_type": "number", "observation_start": DAY, "observation_end": DAY}
                               for key, unit in (("capacity", "units_per_quarter"), ("cost", "currency_per_unit"))],
            "questions": [{"question_id": role, "question": "Explain the evidence in this role.", "role": role,
                           "required_fact_ids": ["capacity", "cost"]} for role in roles]}


def research_packet(scope):
    facts = [{"fact_id": key, "entity": "synthetic_supplier", "metric": key, "value": value, "unit": unit,
              "source": "synthetic reviewed fixture", "source_file": "fixture:" + key,
              "publication_date": DAY, "observation_date": DAY, "data_period": "fixed synthetic quarter",
              "fallback_status": "none"} for key, value, unit in (("capacity", 10, "units_per_quarter"), ("cost", 7, "currency_per_unit"))]
    data = {"schema_version": "research-facts-v1", "facts": facts}
    return {"as_of_date": DAY, "facts": [{"capability": "primary_documents", "scope_key": scope.key, "data": data,
             "provenance": {"source": "synthetic normalized fixture", "source_file": "fixture:manifest",
                            "source_timestamp": DAY + "T15:30:00Z", "publication_date": DAY, "observation_date": DAY,
                            "data_period": "fixed synthetic quarter", "sha256": digest(data)}, "fallback_status": "none"}]}


def role_reply(payload):
    entry = payload.json()
    role, phase = entry["role"], entry["phase"]
    responds = []
    if phase == "rebuttal":
        responds = ["shen_du:initial" if role == "qian_zhan" else "qian_zhan:initial"]
    return {"role": role, "phase": phase, "packet_id": entry["packet_id"], "packet_version": entry["packet_version"],
            "answers": [{"question_id": q["question_id"], "fact_ids": q["required_fact_ids"], "inference": VIEWS[role]}
                        for q in entry["plan"]["parameters"]["research_spec"]["questions"] if q["role"] == role],
            "responds_to": responds, "unknowns": [],
            "monitoring_triggers": [{"condition": "Reassess if capacity or cost changes.", "fact_ids": ["capacity", "cost"]}] if role == "ping_heng" else []}


def fixture_review(payload):
    entry = payload.json()
    candidate = entry["candidate"]
    if candidate["schema_version"] == "mixed-output-v1":
        supported = not candidate["combined_gaps"] and all(item["status"] == "succeeded" for item in candidate["slices"])
    else:
        supported = all(a["inference"] == VIEWS[output["role"]] for output in candidate["role_outputs"].values() for a in output["answers"])
    return {"candidate_hash": entry["candidate_hash"], "passed": supported, "findings": [] if supported else ["Unsupported interpretation"],
            "reviewer": "scripted_fixture_review", "version": "fixture-review-v1"}


def run_research(environment, *, technical=False, debate=False, runner=role_reply, reviewer=fixture_review, mode="research"):
    _, scope, engine, _ = environment
    req = RunRequest(scope, "Analyze the synthetic supplier value chain", DAY, mode, "industry")
    return engine.run(req, research_packet(scope), research_spec=spec(technical, debate), role_runner=runner, semantic_reviewer=reviewer)


def test_company_research_without_uncertainty_does_not_force_debate(environment):
    storage, scope, engine, _ = environment
    seen = []
    def runner(payload):
        seen.append(payload.json()["role"])
        return role_reply(payload)
    req = RunRequest(scope, "Analyze synthetic company fundamentals", DAY, "research", "company")
    outcome = engine.run(req, research_packet(scope), research_spec=spec(), role_runner=runner, semantic_reviewer=fixture_review)
    assert outcome.status == RunStatus.SUCCEEDED and outcome.action == "NO_ACTION"
    assert seen == ["jia_zhi", "ping_heng"]
    result = json.loads(outcome.output)
    report = json.loads(Path(result["report"]["path"]).read_text())
    assert report["source_table"] and report["as_of_date"] == DAY and report["fallback_status"] == "none"
    assert not report["data"]["debate_used"] and report["data"]["confirmed_facts"][0]["value"] == 10
    assert TraceRepository(storage).read_state(scope, "company") is None


def test_industry_roles_and_rebuttals_share_immutable_packet_and_checked_report(environment):
    storage, scope, _, _ = environment
    seen = []
    def runner(payload):
        entry = payload.json()
        seen.append((entry["role"], entry["phase"], entry["packet_id"]))
        entry["packet"]["facts"]["capacity"]["value"] = "tampered copy"
        return role_reply(payload)
    outcome = run_research(environment, technical=True, debate=True, runner=runner)
    assert outcome.status == RunStatus.SUCCEEDED, outcome.output
    assert [(r,p) for r,p,_ in seen] == [("ge_yan","initial"),("jia_zhi","initial"),("qian_zhan","initial"),("shen_du","initial"),("qian_zhan","rebuttal"),("shen_du","rebuttal"),("ping_heng","final")]
    assert len({packet_id for _,_,packet_id in seen}) == 1
    result = json.loads(outcome.output)
    assert result["data"]["confirmed_facts"][0]["value"] == 10
    trace = TraceRepository(storage).read(outcome.run_id, scope)
    assert any(e["detail"]["evaluator"] == "research_semantic_review" and e["passed"] and not e["hard_gate"] for e in trace["evaluations"])
    assert all(e["passed"] for e in trace["evaluations"]) and not outcome.official_output_allowed


@pytest.mark.parametrize("mutation,code", [("packet","role_packet_mismatch"),("citation","invalid_evidence_reference"),
    ("answer","insufficient_coverage:role_questions"),("rebuttal","missing_rebuttal"),("unsupported_view","semantic_review_failed")])
def test_invalid_role_content_cannot_publish(environment, mutation, code):
    storage, scope, _, _ = environment
    def runner(payload):
        reply = role_reply(payload)
        if mutation == "packet": reply["packet_id"] = "another-scope-packet"
        if mutation == "citation": reply["answers"][0]["fact_ids"] = ["invented_fact"]
        if mutation == "answer": reply["answers"] = []
        if mutation == "rebuttal": reply["responds_to"] = []
        if mutation == "unsupported_view": reply["answers"][0]["inference"] = "Immediately buy an unsupported allocation."
        return reply
    outcome = run_research(environment, debate=mutation == "rebuttal", runner=runner, mode="official")
    assert outcome.status == RunStatus.BLOCKED and outcome.action == "NO_ACTION"
    result = json.loads(outcome.output)
    assert result["error_code"] == code and result["data"] is None and "report" not in result
    assert TraceRepository(storage).read_state(scope, "industry") is None


def test_missing_or_mismatched_semantic_review_blocks_delivery(environment):
    for reviewer, code in ((None, "semantic_review_required"),
                          (lambda payload: {**fixture_review(payload), "candidate_hash": "stale-review"}, "invalid_semantic_review")):
        outcome = run_research(environment, reviewer=reviewer)
        assert outcome.status == RunStatus.BLOCKED and json.loads(outcome.output)["error_code"] == code
        assert json.loads(outcome.output)["data"] is None


def test_role_deadline_and_late_result_cannot_publish(environment):
    storage, scope, engine, _ = environment
    started, release, finished = Event(), Event(), Event()
    def slow(payload):
        started.set()
        release.wait(timeout=2)
        result = role_reply(payload)
        finished.set()
        return result
    req = RunRequest(scope, "Synthetic study", DAY, "official", "industry", wall_clock_seconds=.2)
    outcome = engine.run(req, research_packet(scope), research_spec=spec(), role_runner=slow, semantic_reviewer=fixture_review)
    assert outcome.status == RunStatus.FAILED and outcome.category == FailureCategory.TIMEOUT_OR_BUDGET_FAILURE
    assert started.is_set()
    release.set()
    assert finished.wait(timeout=1)  # finish the issued callback; no new work is dispatched
    trace = TraceRepository(storage).read(outcome.run_id, scope)
    assert trace["status"] == "failed" and TraceRepository(storage).read_state(scope, "industry") is None
    assert not any(a["detail"]["path"].endswith("report.json") for a in trace["artifacts"])


def test_report_archive_failure_preserves_prior_official_state(environment):
    storage, scope, engine, _ = environment
    good = engine.run(request(scope, "official"), packet(scope))
    assert good.status == RunStatus.SUCCEEDED
    previous = TraceRepository(storage).read_state(scope, "macro")
    original = engine._archive
    def fail_report(session, name, obj):
        if name == "report": raise OSError("synthetic archive failure")
        return original(session, name, obj)
    with patch.object(engine, "_archive", side_effect=fail_report):
        bad = engine.run(request(scope, "official"), packet(scope))
    assert bad.status == RunStatus.FAILED and bad.category == FailureCategory.TOOL_RETURN_FAILURE
    assert TraceRepository(storage).read_state(scope, "macro") == previous
    assert "report" not in json.loads(bad.output)


def test_report_identity_cannot_be_forged_by_renderer(environment):
    storage, scope, engine, _ = environment
    from a_share_claw.harness.reports import build_report
    def forged(*args):
        return {**build_report(*args), "run_id": "wrong-run"}
    with patch("a_share_claw.harness.engine.build_report", side_effect=forged):
        outcome = engine.run(request(scope, "official"), packet(scope))
    assert outcome.status == RunStatus.BLOCKED
    assert json.loads(outcome.output)["error_code"] == "report_contract_failure"
    assert TraceRepository(storage).read_state(scope, "macro") is None


def test_official_research_requires_report_and_remains_no_action(environment):
    storage, scope, _, _ = environment
    outcome = run_research(environment, mode="official")
    assert outcome.status == RunStatus.SUCCEEDED and outcome.official_output_allowed and outcome.action == "NO_ACTION"
    state = TraceRepository(storage).read_state(scope, "industry", as_of_date=DAY)
    assert state["run_id"] == outcome.run_id and state["report"]["run_id"] == outcome.run_id
    assert Path(state["report"]["path"]).is_file()


def next_day_packet(scope):
    result = copy.deepcopy(packet(scope))
    day = "2026-07-14"
    result["as_of_date"] = day
    for item in result["facts"]:
        p = item["provenance"]
        p.update(source_timestamp=day + "T15:30:00Z", publication_date=day, observation_date=day)
        if item["capability"] == "market_history":
            for rows in item["data"].values():
                rows.append({"trade_date": day, "close": rows[-1]["close"] * 1.01})
        p["sha256"] = digest(item["data"])
    return result


def test_state_history_preserves_cutoff_scope_and_does_not_rewind_publication(environment):
    storage, scope, engine, _ = environment
    first = engine.run(request(scope, "official"), packet(scope))
    second = engine.run(request(scope, "official", day="2026-07-14"), next_day_packet(scope))
    assert first.status == second.status == RunStatus.SUCCEEDED
    repo = TraceRepository(storage)
    assert repo.read_state(scope, "macro", as_of_date=DAY)["run_id"] == first.run_id
    assert repo.read_state(scope, "macro")["run_id"] == second.run_id
    assert repo.read_state(scope, "macro", as_of_date="2026-07-12") is None
    other = Scope(scope.workspace, "other-principal", scope.session)
    assert repo.read_state(other, "macro", as_of_date=DAY) is None
    report = json.loads(Path(json.loads(second.output)["report"]["path"]).read_text())
    assert report["prior_comparison"]["run_id"] == first.run_id and report["prior_comparison"]["status"] == "available"
    denied = engine.run(request(scope, "official"), packet(scope))
    assert denied.status == RunStatus.BLOCKED and json.loads(denied.output)["error_code"] == "promotion_denied"
    trace = repo.read(denied.run_id, scope)
    staged = json.loads(Path(next(a["detail"]["path"] for a in trace["artifacts"] if a["detail"]["path"].endswith("computed_output.json"))).read_text())
    assert not staged["official_output_allowed"] and staged["action"] == "NO_ACTION"
    assert repo.read_state(scope, "macro")["run_id"] == second.run_id
    assert storage._conn.execute("SELECT count(*) FROM official_state_history").fetchone()[0] == 2


def test_v1_database_migrates_current_official_state_without_losing_it(environment):
    storage, scope, engine, _ = environment
    outcome = engine.run(request(scope, "official"), packet(scope))
    repo = TraceRepository(storage)
    previous = repo.read_state(scope, "macro")
    with storage._conn:
        storage._conn.execute("DROP TABLE official_state_history")
        storage._conn.execute("DELETE FROM schema_migrations WHERE version=2")
    storage.init()
    assert repo.read_state(scope, "macro", as_of_date=DAY) == previous
    assert repo.read_state(scope, "macro")["run_id"] == outcome.run_id
    assert storage._conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 2
    storage.init()
    assert storage._conn.execute("SELECT count(*) FROM official_state_history").fetchone()[0] == 1


def test_role_cancellation_is_terminal_and_no_private_question_text_enters_trace(environment):
    import asyncio
    storage, scope, _, _ = environment
    def cancelled(payload):
        raise asyncio.CancelledError()
    outcome = run_research(environment, runner=cancelled)
    assert outcome.status == RunStatus.CANCELLED and outcome.action == "NO_ACTION"
    trace = TraceRepository(storage).read(outcome.run_id, scope)
    text = json.dumps(trace)
    assert "Synthetic supplier value-chain study" not in text and "Explain the evidence in this role." not in text
    assert any(row["stage"] == "context" for row in trace["run_steps"])
    assert trace["status"] == "cancelled"


@pytest.mark.parametrize("field,value", [("unit", "provider-selected-proxy"), ("entity", "wrong-supplier"), ("value", "numeric-looking-string")])
def test_research_fields_are_frozen_by_core_before_role_execution(environment, field, value):
    storage, scope, engine, _ = environment
    source = research_packet(scope)
    source["facts"][0]["data"]["facts"][0][field] = value
    source["facts"][0]["provenance"]["sha256"] = digest(source["facts"][0]["data"])
    calls = []
    def runner(payload):
        calls.append(payload)
        return role_reply(payload)
    req = RunRequest(scope, "Analyze synthetic supplier", DAY, "official", "industry")
    outcome = engine.run(req, source, research_spec=spec(), role_runner=runner, semantic_reviewer=fixture_review)
    assert outcome.status == RunStatus.BLOCKED and json.loads(outcome.output)["error_code"] == "research_fact_contract_mismatch"
    assert not calls and TraceRepository(storage).read_state(scope, "industry") is None


def test_rejected_role_is_private_staged_evidence_not_delivered(environment):
    storage,scope,_,_=environment
    def bad(payload):
        reply=role_reply(payload);reply["unexpected"]="PRIVATE_REJECTED_CANDIDATE";return reply
    out=run_research(environment,runner=bad,mode="official")
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="invalid_role_output"
    trace=TraceRepository(storage).read(out.run_id,scope)
    artifact=next(v["detail"] for v in trace["artifacts"] if Path(v["detail"]["path"]).name=="rejected_role_jia_zhi_initial.json")
    assert artifact["publication_status"]=="staged"
    assert "PRIVATE_REJECTED_CANDIDATE" in Path(artifact["path"]).read_text()
    assert "PRIVATE_REJECTED_CANDIDATE" not in json.dumps(trace)
    assert json.loads(out.output)["data"] is None and "report" not in json.loads(out.output)
    assert TraceRepository(storage).read_state(scope,"industry") is None
