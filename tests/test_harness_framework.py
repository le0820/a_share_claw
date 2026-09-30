"""Framework compiler acceptance after implementation, no live model/source."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from a_share_claw.agent import InvestmentAgent
from a_share_claw.harness.cli import run_harness
from a_share_claw.harness.contracts import RunRequest, RunStatus, Scope, canonical
from a_share_claw.harness.trace import TraceRepository
from test_harness import DAY, ROOT, environment
from test_harness_hosts import host
from test_harness_research import spec, research_packet, role_reply, fixture_review
from test_harness_quant import AS_OF, quant_spec, outlook_spec, outlook_packet, outlook_reply, outlook_review
from test_sdk_research import OfflineEndpoint, configured


def proposal(parameters=None):
    return {"framework":"Analyze synthetic supplier value capture and risk; acquire declared facts before conclusions.",
            "parameters":parameters if parameters is not None else {"research_spec":spec()},"unresolved_constraints":[]}


def review(payload):
    entry=payload.json()
    if entry["candidate"]["schema_version"] != "framework-proposal-v1":return fixture_review(payload)
    assert "original_request_satisfied" in entry["criteria"]
    assert entry["user_request"]
    return {"candidate_hash":entry["candidate_hash"],"passed":True,"findings":[],"reviewer":"scripted_framework_fixture","version":"framework-fixture-v1"}


def test_compiled_framework_and_research_share_one_run_and_freeze_before_facts(environment):
    storage,scope,engine,_=environment
    req=RunRequest(scope,"Analyze synthetic supplier value capture",DAY,"research","industry")
    out=engine.run(req,research_packet(scope),framework_proposer=lambda payload:proposal(),framework_reviewer=review,
                   role_runner=role_reply,semantic_reviewer=fixture_review)
    assert out.status==RunStatus.SUCCEEDED,out.output
    result=json.loads(out.output);trace=TraceRepository(storage).read(out.run_id,scope)
    assert result["plan"]["parameters"]["research_spec"]==spec()
    assert result["plan"]["framework"].startswith("Analyze synthetic") and not result["plan"]["unresolved_constraints"]
    stages=[v["stage"] for v in trace["run_steps"]]
    assert stages.index("framework_end") < stages.index("plan") < stages.index("evidence") < stages.index("role_start")
    assert {v["detail"]["evaluator"] for v in trace["evaluations"]} >= {"framework_contract","framework_semantic_review","research_semantic_review","report_contract"}
    assert len(TraceRepository(storage).list_runs(scope))==1 and not trace["tool_calls"]
    assert "Analyze synthetic supplier value capture" not in canonical(trace)
    assert "acquire declared facts before conclusions" not in canonical(trace)
    assert TraceRepository(storage).read_state(scope,"industry") is None


@pytest.mark.parametrize("workflow,gap",[("ai","current_ai_pct"),("quant","quant_spec"),("outlook","outlook_spec"),("mixed","mixed_spec")])
def test_absent_host_constraints_remain_gaps_and_block_execution(environment,workflow,gap):
    _,scope,engine,_=environment
    missing=lambda payload:proposal({})
    out=engine.run(RunRequest(scope,"Synthetic incomplete framework",DAY,"plan",workflow),framework_proposer=missing,framework_reviewer=review)
    assert out.status==RunStatus.SUCCEEDED and not out.official_output_allowed
    value=json.loads(out.output)
    assert value["completion"]=="framework_only" and gap in value["plan"]["unresolved_constraints"]
    assert value["plan"]["parameters"]=={}
    calls=[]
    out=engine.run(RunRequest(scope,"Synthetic incomplete execution",DAY,"official",workflow),framework_proposer=missing,framework_reviewer=review,
                   role_runner=lambda payload:calls.append(payload))
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="planning_constraints_required"
    assert not calls and out.action=="NO_ACTION"


@pytest.mark.parametrize("mutation,code",[("provider","invalid_framework_spec"),("scope","invalid_framework_spec"),
    ("weights","invalid_framework_spec"),("future","future_data"),("review","semantic_review_failed"),("review_hash","invalid_semantic_review")])
def test_untrusted_framework_cannot_widen_policy_or_publish(environment,mutation,code):
    storage,scope,engine,_=environment
    value=proposal()
    if mutation=="provider":value["parameters"]["provider"]="web"
    if mutation=="scope":value["scope_key"]="another user"
    if mutation=="weights":value["parameters"]["weights"]={"L2":1}
    if mutation=="future":value["parameters"]["research_spec"]["required_facts"][0]["observation_end"]="2026-07-14"
    def reviewer(payload):
        result=review(payload)
        if mutation=="review":result.update(passed=False,findings=["Original question not covered"])
        if mutation=="review_hash":result["candidate_hash"]="stale review"
        return result
    out=engine.run(RunRequest(scope,"Synthetic protected framework",DAY,"official","industry"),research_packet(scope),
                   framework_proposer=lambda payload:value,framework_reviewer=reviewer,role_runner=role_reply,semantic_reviewer=fixture_review)
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]==code
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert not any(v["stage"]=="evidence" for v in trace["run_steps"])
    assert TraceRepository(storage).read_state(scope,"industry") is None


def test_numeric_specs_and_actual_position_cannot_be_invented_or_changed(environment):
    _,scope,engine,_=environment
    q=quant_spec()
    for workflow,parameters,constraints in [("quant",{"quant_spec":q},{}),("ai",{"current_ai_pct":57.5},{}),
                                           ("ai",{"current_ai_pct":70},{"current_ai_pct":55})]:
        out=engine.run(RunRequest(scope,"Synthetic constraint protection",AS_OF,"plan",workflow),planning_constraints=constraints,
                       framework_proposer=lambda payload,p=parameters:proposal(p),framework_reviewer=review)
        assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="planning_constraint_changed"
    protected={"quant_spec":q}
    out=engine.run(RunRequest(scope,"Synthetic fixed price specification",AS_OF,"plan","quant"),planning_constraints=protected,
                   framework_proposer=lambda payload:proposal(protected),framework_reviewer=review)
    assert out.status==RunStatus.SUCCEEDED and json.loads(out.output)["plan"]["parameters"]["quant_spec"]==q


def test_outlook_compilation_binds_host_prices_horizon_and_derived_fact_units(environment):
    storage,scope,engine,_=environment
    specification=outlook_spec()
    constraints={k:v for k,v in specification.items() if k!="research_spec"}
    out=engine.run(RunRequest(scope,"Synthetic outlook framework",AS_OF,"research","outlook"),outlook_packet(scope),planning_constraints=constraints,
                   framework_proposer=lambda payload:proposal({"outlook_spec":specification}),framework_reviewer=review,
                   role_runner=outlook_reply,semantic_reviewer=outlook_review)
    assert out.status==RunStatus.SUCCEEDED,out.output
    bad=copy.deepcopy(specification)
    next(f for f in bad["research_spec"]["required_facts"] if f["fact_id"].startswith("price."))["unit"]="percent"
    out=engine.run(RunRequest(scope,"Synthetic invalid derived contract",AS_OF,"plan","outlook"),planning_constraints=constraints,
                   framework_proposer=lambda payload:proposal({"outlook_spec":bad}),framework_reviewer=review)
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="invalid_framework_spec"
    assert TraceRepository(storage).read_state(scope,"outlook") is None


def test_framework_cancel_replay_and_missing_reviewer_have_terminal_trace(environment):
    storage,scope,engine,_=environment
    def cancelled(payload):raise asyncio.CancelledError()
    out=engine.run(RunRequest(scope,"Synthetic framework cancellation",DAY,"plan","industry"),framework_proposer=cancelled,framework_reviewer=review)
    assert out.status==RunStatus.CANCELLED
    out=engine.run(RunRequest(scope,"Synthetic offline replay",DAY,"replay","industry"),framework_proposer=lambda payload:proposal(),framework_reviewer=review)
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="model_replay_not_supported"
    out=engine.run(RunRequest(scope,"Synthetic absent review",DAY,"plan","industry"),framework_proposer=lambda payload:proposal())
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="semantic_review_required"
    assert all(v["status"]!="running" for v in TraceRepository(storage).list_runs(scope))


class FrameworkEndpoint(OfflineEndpoint):
    def response(self,request):
        body=json.loads(request.content)
        assert not body.get("tools")
        entry=json.loads(body["messages"][-1]["content"])
        self.requests.append(entry)
        if "candidate" in entry:
            from a_share_claw.harness.research import RoleRequest
            reply=review(RoleRequest(canonical(entry),30))
        elif "role" in entry:
            from a_share_claw.harness.research import RoleRequest
            reply=role_reply(RoleRequest(canonical(entry),30))
        else:reply=proposal()
        return httpx.Response(200,json={"id":"framework-fixture","object":"chat.completion","created":1,
              "model":"synthetic-sdk-model","choices":[{"index":0,"message":{"role":"assistant","content":canonical(reply)},"finish_reason":"stop"}],
              "usage":{"prompt_tokens":20,"completion_tokens":10,"total_tokens":30}})


def test_actual_sdk_host_and_cli_compile_framework_with_zero_plugins(host,capsys):
    config,storage,context,path=host
    agent=InvestmentAgent(configured(config),storage);endpoint=FrameworkEndpoint()
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client), \
         patch("a_share_claw.data_plugins.core.Registry.snapshot",side_effect=AssertionError("planner must not load plugins")):
        out=agent.plan_core_result(context,"Analyze synthetic supplier value capture",as_of_date=DAY,workflow="industry")
    assert out.status==RunStatus.SUCCEEDED and len(endpoint.requests)==2 and all(c.is_closed() for c in endpoint.clients)
    scope=Scope.from_context(ROOT,context);trace=TraceRepository(storage).read(out.run_id,scope)
    assert not trace["tool_calls"] and [v["detail"]["operation"] for v in trace["model_calls"]]==["framework_proposal","framework_semantic_review"]
    common={"platform":"local","user":"local-user","chat":"local-chat","agent_key":"default"}
    args=argparse.Namespace(command="harness",harness_command="plan",workflow="industry",date=DAY,question="Synthetic compiler CLI",model_executor="configured",**common)
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client):assert run_harness(args,configured(config),storage)==0
    result=json.loads(capsys.readouterr().out)
    assert result["completion"]=="framework_only" and result["plan"]["parameters"]["research_spec"]==spec()
    assert len(endpoint.requests)==4


def test_late_framework_result_after_budget_cannot_freeze_or_execute(environment):
    from threading import Event
    from a_share_claw.harness.contracts import FailureCategory
    storage,scope,engine,_=environment
    started,release,finished=Event(),Event(),Event()
    def slow(payload):
        started.set();release.wait(timeout=2)
        result=proposal();finished.set();return result
    out=engine.run(RunRequest(scope,"Synthetic framework deadline",DAY,"official","industry",wall_clock_seconds=.3),
                   research_packet(scope),framework_proposer=slow,framework_reviewer=review,role_runner=role_reply,semantic_reviewer=fixture_review)
    assert out.status==RunStatus.FAILED and out.category==FailureCategory.TIMEOUT_OR_BUDGET_FAILURE
    assert started.is_set();release.set();assert finished.wait(timeout=1)
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert trace["status"]=="failed" and not any(v["stage"] in {"plan","evidence","role_start"} for v in trace["run_steps"])
    assert TraceRepository(storage).read_state(scope,"industry") is None


def test_actual_sdk_compiler_then_roles_share_the_same_core_run(host):
    config,storage,context,_=host
    endpoint=FrameworkEndpoint();agent=InvestmentAgent(configured(config),storage)
    scope=Scope.from_context(ROOT,context)
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client):
        out=agent.run_core_result(context,"Analyze synthetic supplier value capture",as_of_date=DAY,workflow="industry",packet=research_packet(scope),compile_framework=True)
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert len(endpoint.requests)==5 and all(c.is_closed() for c in endpoint.clients)
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert len(TraceRepository(storage).list_runs(scope))==1 and not trace["tool_calls"]
    assert [v["detail"]["operation"] for v in trace["model_calls"]]==["framework_proposal","framework_semantic_review","jia_zhi:initial","ping_heng:final","research_semantic_review"]
    assert not out.official_output_allowed and out.action=="NO_ACTION"
