"""Mixed acceptance after implementation: synthetic facts, no live market case."""
import asyncio
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_claw.agent import InvestmentAgent
from a_share_claw.harness.contracts import RunRequest, RunStatus, Scope, canonical
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.harness.cli import run_harness
from test_harness import DAY, ROOT, environment, packet
from test_harness_hosts import host
from test_harness_research import research_packet, spec, role_reply, fixture_review
from test_sdk_research import OfflineEndpoint, configured


def mixed_spec():
    return {"slices": [{"slice_id": "macro", "workflow": "macro", "question": "Daily macro score", "parameters": {}},
                       {"slice_id": "supplier", "workflow": "industry", "question": "Synthetic supplier value capture", "parameters": {"research_spec": spec()}}]}


def mixed_packet(scope):
    research = research_packet(scope)
    research["facts"][0]["provenance"]["source_timestamp"] = DAY + "T05:00:00Z"
    return {"as_of_date": DAY, "slices": [{"slice_id": "macro", "packet": packet(scope)},
                                         {"slice_id": "supplier", "packet": research}]}


def run(environment, *, mode="official", facts=True, **kwargs):
    _, scope, engine, _ = environment
    req = RunRequest(scope, "Score macro and analyze this synthetic supplier", DAY, mode, "mixed")
    return engine.run(req, mixed_packet(scope) if facts else None, mixed_spec=mixed_spec(),
                      role_runner=role_reply, semantic_reviewer=fixture_review, **kwargs)


def assert_no_states(storage, scope):
    repo = TraceRepository(storage)
    assert all(repo.read_state(scope, flow) is None for flow in ("mixed", "macro", "industry"))


def test_waiting_macro_keeps_reviewed_independent_research(environment):
    storage, scope, _, _ = environment
    out = run(environment, clock=datetime(2026,7,13,6,tzinfo=timezone.utc))
    result = json.loads(out.output)
    assert out.status == RunStatus.BLOCKED and result["error_code"] == "mixed_incomplete"
    assert [v["disposition"] for v in result["slice_status"]] == ["waiting", "succeeded"]
    assert result["data"] is None and "report" not in result and out.action == "NO_ACTION"
    partial = result["partial_reports"][0]
    assert partial["slice_id"] == "supplier" and Path(partial["report"]["path"]).is_file()
    assert_no_states(storage, scope)


def test_parent_alone_publishes_after_all_slices_and_original_request_review(environment):
    storage, scope, engine, _ = environment
    seen = []
    def reviewer(payload):
        entry = payload.json()
        assert "Score macro" in entry["user_request"]
        assert "original_request_satisfied" in entry["criteria"]
        seen.append(entry["candidate"]["schema_version"])
        return fixture_review(payload)
    req = RunRequest(scope,"Score macro and analyze this synthetic supplier",DAY,"official","mixed")
    out = engine.run(req,mixed_packet(scope),mixed_spec=mixed_spec(),role_runner=role_reply,semantic_reviewer=reviewer)
    assert out.status == RunStatus.SUCCEEDED and out.official_output_allowed and out.action == "NO_ACTION", out.output
    assert seen == ["research-output-v1", "mixed-output-v1"]
    result = json.loads(out.output)
    repo = TraceRepository(storage)
    assert repo.read_state(scope,"mixed")["run_id"] == out.run_id
    assert repo.read_state(scope,"macro") is None and repo.read_state(scope,"industry") is None
    for child in result["slice_status"]:
        trace = repo.read(child["run_id"],scope)
        assert trace["request"]["mode"] == "research" and not trace["outcome"]["official_output_allowed"]
        assert any(s["stage"] == "parent_run" and s["detail"]["run_id"] == out.run_id for s in trace["run_steps"])
    trace = repo.read(out.run_id,scope)
    assert "Score macro and analyze this synthetic supplier" not in canonical(trace)
    assert "Synthetic supplier value capture" not in canonical(trace)
    assert "Explain the evidence in this role." not in canonical(trace)


@pytest.mark.parametrize("broken",["missing_macro","bad_macro","scope_macro"])
def test_bad_macro_does_not_suppress_valid_research(environment,broken):
    storage, scope, engine, _ = environment
    source=mixed_packet(scope)
    if broken == "missing_macro": source["slices"].pop(0)
    if broken == "bad_macro": source["slices"][0]["packet"]["facts"][0]["provenance"]["sha256"]="wrong"
    if broken == "scope_macro": source["slices"][0]["packet"]["facts"][0]["scope_key"]="other"
    out=engine.run(RunRequest(scope,"Synthetic mixed",DAY,"official","mixed"),source,mixed_spec=mixed_spec(),role_runner=role_reply,semantic_reviewer=fixture_review)
    assert out.status == RunStatus.BLOCKED
    assert [v["status"] for v in json.loads(out.output)["slice_status"]] == ["blocked","succeeded"]
    assert_no_states(storage,scope)


@pytest.mark.parametrize("failure",["review","report","output"])
def test_combined_failure_leaves_no_partial_official_state(environment,failure):
    storage,scope,engine,_=environment
    archive=engine._archive
    def fail(session,name,obj):
        if session.request.workflow=="mixed" and name=={"report":"report","output":"computed_output"}.get(failure):
            raise OSError("synthetic final archive failure")
        return archive(session,name,obj)
    def reviewer(payload):
        value=fixture_review(payload)
        if failure=="review" and payload.json()["candidate"]["schema_version"]=="mixed-output-v1":
            value.update(passed=False,findings=["Original request is not resolved"])
        return value
    with patch.object(engine,"_archive",side_effect=fail):
        out=engine.run(RunRequest(scope,"Synthetic mixed",DAY,"official","mixed"),mixed_packet(scope),mixed_spec=mixed_spec(),role_runner=role_reply,semantic_reviewer=reviewer)
    assert out.status == (RunStatus.BLOCKED if failure=="review" else RunStatus.FAILED)
    result=json.loads(out.output)
    assert result["data"] is None and "report" not in result and len(result["partial_reports"])==2
    assert not out.official_output_allowed
    assert_no_states(storage,scope)


def test_plan_missing_spec_and_recursive_slice_are_explicit_gaps(environment):
    storage,scope,engine,_=environment
    out=engine.run(RunRequest(scope,"Synthetic mixed",DAY,"plan","mixed"))
    assert json.loads(out.output)["plan"]["unresolved_constraints"] == ["mixed_spec"]
    assert len(TraceRepository(storage).list_runs(scope))==1
    invalid=mixed_spec();invalid["slices"][0]["workflow"]="mixed"
    out=engine.run(RunRequest(scope,"Synthetic mixed",DAY,"official","mixed"),mixed_packet(scope),mixed_spec=invalid)
    assert json.loads(out.output)["error_code"]=="invalid_mixed_spec"
    assert len(TraceRepository(storage).list_runs(scope))==2
    assert_no_states(storage,scope)


def test_cancellation_stops_parent_and_no_child_publishes(environment):
    storage,scope,engine,_=environment
    def cancelled(payload): raise asyncio.CancelledError()
    out=engine.run(RunRequest(scope,"Synthetic mixed",DAY,"official","mixed"),mixed_packet(scope),mixed_spec=mixed_spec(),role_runner=cancelled,semantic_reviewer=fixture_review)
    assert out.status == RunStatus.CANCELLED
    assert json.loads(out.output)["slice_status"][-1]["status"]=="cancelled"
    assert_no_states(storage,scope)


def test_actual_sdk_mixed_roles_child_review_and_parent_review(host):
    config,storage,context,_=host
    scope=Scope.from_context(ROOT,context)
    endpoint=OfflineEndpoint()
    agent=InvestmentAgent(configured(config),storage)
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client):
        out=agent.run_core_result(context,"Score macro and analyze this synthetic supplier",as_of_date=DAY,workflow="mixed",packet=mixed_packet(scope),mixed_spec=mixed_spec())
    assert out.status == RunStatus.SUCCEEDED, out.output
    assert len(endpoint.requests)==4 and all(client.is_closed() for client in endpoint.clients)
    repo=TraceRepository(storage)
    parent=repo.read(out.run_id,scope)
    assert not parent["tool_calls"] and len(parent["model_calls"])==1
    result=json.loads(out.output)
    child=repo.read(result["slice_status"][1]["run_id"],scope)
    assert not child["tool_calls"] and len(child["model_calls"])==3
    assert len(repo.list_runs(scope))==3
    assert endpoint.requests[-1]["candidate"]["schema_version"]=="mixed-output-v1"
    assert_no_states(storage,scope)


def test_mixed_quant_requirements_follow_slices_instead_of_macro_defaults(environment):
    from test_harness_quant import quant_spec
    storage,scope,engine,_=environment
    specification=mixed_spec()
    specification["slices"][1]={"slice_id":"prices","workflow":"quant","question":"Declared price statistics","parameters":{"quant_spec":quant_spec()}}
    out=engine.run(RunRequest(scope,"Score and compute price statistics",DAY,"plan","mixed"),mixed_spec=specification)
    plan=json.loads(out.output)["plan"]
    assert set(plan["required_capabilities"])=={"cn_macro","us_macro","market_history","price_history"}
    assert "primary_documents" not in plan["required_capabilities"]
    assert len(TraceRepository(storage).list_runs(scope))==1


def test_cli_mixed_plan_executes_no_models_and_replay_is_explicitly_unavailable(host,capsys):
    config,storage,context,path=host
    scope=Scope.from_context(ROOT,context)
    facts,specification=path/"mixed.json",path/"mixed-spec.json"
    facts.write_text(canonical(mixed_packet(scope)))
    specification.write_text(canonical(mixed_spec()))
    common={"platform":"local","user":"local-user","chat":"local-chat","agent_key":"default"}
    endpoint=OfflineEndpoint()
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client):
        args=argparse.Namespace(command="harness",harness_command="plan",workflow="mixed",date=DAY,question="Synthetic mixed CLI",mixed_spec=specification,**common)
        assert run_harness(args,configured(config),storage)==0
        plan=json.loads(capsys.readouterr().out)
        assert plan["plan"]["parameters"]["mixed_spec"]==mixed_spec() and not endpoint.requests
        args=argparse.Namespace(command="harness",harness_command="run",workflow="mixed",date=DAY,question="Synthetic mixed CLI",mixed_spec=specification,packet_file=facts,mode="research",model_executor="configured",**common)
        assert run_harness(args,configured(config),storage)==0
        result=json.loads(capsys.readouterr().out)
        count=len(endpoint.requests)
        args=argparse.Namespace(command="harness",harness_command="replay",run_id=result["run_id"],**common)
        assert run_harness(args,configured(config),storage)==2
        assert json.loads(capsys.readouterr().out)["error_code"]=="replay_unavailable"
        assert len(endpoint.requests)==count


def test_late_timed_out_slice_cannot_publish_parent_or_child(environment):
    from threading import Event
    from a_share_claw.harness.contracts import FailureCategory
    storage,scope,engine,_=environment
    started,release,finished=Event(),Event(),Event()
    def slow(payload):
        started.set();release.wait(timeout=2)
        reply=role_reply(payload);finished.set();return reply
    req=RunRequest(scope,"Synthetic mixed timeout",DAY,"official","mixed",wall_clock_seconds=.3)
    out=engine.run(req,mixed_packet(scope),mixed_spec=mixed_spec(),role_runner=slow,semantic_reviewer=fixture_review)
    assert out.status==RunStatus.FAILED and out.category==FailureCategory.TIMEOUT_OR_BUDGET_FAILURE
    assert started.is_set()
    release.set();assert finished.wait(timeout=1)
    repo=TraceRepository(storage)
    assert repo.read(out.run_id,scope)["status"]=="failed"
    assert all(repo.read(v["run_id"],scope)["status"]!="running" for v in json.loads(out.output)["slice_status"])
    assert_no_states(storage,scope)
