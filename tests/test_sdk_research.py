"""Actual SDK/client round trips against offline HTTP responses; no live endpoint."""
import argparse
import dataclasses
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from openai import AsyncOpenAI

from a_share_claw.agent import InvestmentAgent
from a_share_claw.harness.cli import run_harness
from a_share_claw.harness.contracts import RunStatus, Scope, canonical
from a_share_claw.harness.research import RoleRequest
from a_share_claw.harness.trace import TraceRepository
from test_harness import DAY, ROOT
from test_harness_hosts import host
from test_harness_research import research_packet, spec, role_reply, fixture_review


class OfflineEndpoint:
    def __init__(self, mutation=None):
        self.requests = []
        self.mutation = mutation
        self.clients = []
        self.bodies = []

    def response(self, request):
        body = json.loads(request.content)
        self.bodies.append(body)
        assert str(request.url) == "https://fixture-endpoint.invalid/v1/chat/completions"
        assert body["model"] == "synthetic-sdk-model"
        assert not body.get("tools")
        content = body["messages"][-1]["content"]
        if isinstance(content, list):
            content = "".join(item["text"] for item in content)
        entry = json.loads(content)
        self.requests.append(entry)
        payload = RoleRequest(content, 30)
        is_review = "candidate" in entry
        reply = fixture_review(payload) if is_review else role_reply(payload)
        if self.mutation == "invalid_json":
            rendered = "Unsupported free prose with an immediate BUY."
        else:
            if self.mutation == "invalid_reference" and not is_review:
                reply["answers"][0]["fact_ids"] = ["invented_fact"]
            if self.mutation == "failed_review" and is_review:
                reply["passed"] = False
                reply["findings"] = ["Unsupported interpretation"]
            rendered = canonical(reply)
        return httpx.Response(200, json={"id": "offline-response", "object": "chat.completion", "created": 1,
             "model": "synthetic-sdk-model", "choices": [{"index": 0, "message": {"role": "assistant", "content": rendered}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})

    def client(self, config):
        client = AsyncOpenAI(base_url=config.model_base_url, api_key="offline-only-not-a-credential", max_retries=0,
                             http_client=httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(self.response)))
        self.clients.append(client)
        return client


def configured(config):
    return dataclasses.replace(config, model_provider="tencent", model_base_url="https://fixture-endpoint.invalid/v1",
                               model_api_key="offline-only-not-a-credential", model_name="synthetic-sdk-model")


@pytest.mark.parametrize('provider', ['tencent', 'deepseek'])
def test_real_sdk_roles_review_and_report_share_core_run(host, provider):
    config, storage, context, _ = host
    scope = Scope.from_context(ROOT, context)
    endpoint = OfflineEndpoint()
    agent = InvestmentAgent(dataclasses.replace(configured(config), model_provider=provider), storage)
    with patch("a_share_claw.agent.build_model_client", side_effect=endpoint.client):
        outcome = agent.run_core_result(context, "Synthetic industry acceptance", as_of_date=DAY,
                    packet=research_packet(scope), research_spec=spec(technical=True, debate=True), workflow="industry")
    assert outcome.status == RunStatus.SUCCEEDED, outcome.output
    assert outcome.action == "NO_ACTION" and not outcome.official_output_allowed
    assert len(endpoint.requests) == 8 and all(client.is_closed() for client in endpoint.clients)
    expected='json_object' if provider=='deepseek' else 'json_schema'
    assert all(body['response_format']['type']==expected for body in endpoint.bodies)
    if provider=='deepseek':assert all(body['response_format']=={'type':'json_object'} for body in endpoint.bodies)
    roles = endpoint.requests[:-1]
    assert len({entry["packet_id"] for entry in roles}) == 1
    trace = TraceRepository(storage).read(outcome.run_id, scope)
    assert len(TraceRepository(storage).list_runs(scope)) == 1
    assert not trace["tool_calls"] and len(trace["model_calls"]) == 8
    assert [row["detail"]["operation"] for row in trace["model_calls"]] == [r["role"]+":"+r["phase"] for r in roles]+["research_semantic_review"]
    assert all(row["status"] == "ok" and row["detail"]["input_tokens"] == 20 for row in trace["model_calls"])
    assert "Synthetic industry acceptance" not in canonical(trace)
    report = json.loads(Path(json.loads(outcome.output)["report"]["path"]).read_text())
    assert report["run_id"] == outcome.run_id and report["data"]["semantic_review"]["passed"]


@pytest.mark.parametrize("mutation,code,calls", [("invalid_json","invalid_model_output",1),
                     ("invalid_reference","invalid_evidence_reference",1), ("failed_review","semantic_review_failed",3)])
@pytest.mark.parametrize('provider', ['tencent', 'deepseek'])
def test_actual_sdk_bad_output_or_review_cannot_publish(host, mutation, code, calls, provider):
    config, storage, context, _ = host
    scope = Scope.from_context(ROOT, context)
    endpoint = OfflineEndpoint(mutation)
    with patch("a_share_claw.agent.build_model_client", side_effect=endpoint.client):
        outcome = InvestmentAgent(dataclasses.replace(configured(config), model_provider=provider), storage).run_core_result(context, "Synthetic company", as_of_date=DAY,
                  packet=research_packet(scope), research_spec=spec(), workflow="company", mode="official")
    assert outcome.status == RunStatus.BLOCKED and not outcome.official_output_allowed
    assert outcome.action == "NO_ACTION" and len(endpoint.requests) == calls
    result = json.loads(outcome.output)
    assert result["error_code"] == code and result["data"] is None and "report" not in result
    assert TraceRepository(storage).read_state(scope, "company") is None
    assert all(client.is_closed() for client in endpoint.clients)


def test_missing_facts_or_endpoint_stops_before_any_model_request(host):
    config, storage, context, _ = host
    scope = Scope.from_context(ROOT, context)
    with patch("a_share_claw.agent.build_model_client") as client:
        for facts, expected in ((None, "missing_required_data"), (research_packet(scope), "model_configuration_required")):
            outcome = InvestmentAgent(config, storage).run_core_result(context, "Synthetic company", as_of_date=DAY,
                            packet=facts, research_spec=spec(), workflow="company")
            assert outcome.status == RunStatus.BLOCKED and json.loads(outcome.output)["error_code"] == expected
            assert not TraceRepository(storage).read(outcome.run_id, scope)["model_calls"]
        client.assert_not_called()


def test_cli_research_spec_model_execution_and_plan_without_model(host, capsys):
    config, storage, context, path = host
    config = configured(config)
    scope = Scope.from_context(ROOT, context)
    facts, specification = path / "facts.json", path / "spec.json"
    facts.write_text(canonical(research_packet(scope)))
    specification.write_text(canonical(spec()))
    common = {"platform":"local", "user":"local-user", "chat":"local-chat", "agent_key":"default"}
    endpoint = OfflineEndpoint()
    args = argparse.Namespace(command="harness", harness_command="plan", workflow="company", date=DAY,
                              question="Synthetic framework", research_spec=specification, **common)
    with patch("a_share_claw.agent.build_model_client", side_effect=endpoint.client):
        assert run_harness(args, config, storage) == 0
        plan = json.loads(capsys.readouterr().out)
        assert plan["plan"]["parameters"]["research_spec"] == spec() and not endpoint.requests
        args = argparse.Namespace(command="harness", harness_command="run", workflow="company", date=DAY,
                   question="Synthetic framework", research_spec=specification, packet_file=facts, mode="research",
                   model_executor="configured", **common)
        assert run_harness(args, config, storage) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(endpoint.requests) == 3 and result["action"] == "NO_ACTION"
    trace = TraceRepository(storage).read(result["report"]["run_id"], scope)
    assert len(trace["model_calls"]) == 3 and not trace["tool_calls"]


def test_actual_model_exception_is_redacted_and_terminal(host):
    config, storage, context, _ = host
    scope = Scope.from_context(ROOT, context)
    endpoint = OfflineEndpoint()
    def failed(request):
        raise httpx.ConnectError("api_key=private-marker", request=request)
    endpoint.response = failed
    with patch("a_share_claw.agent.build_model_client", side_effect=endpoint.client):
        outcome = InvestmentAgent(configured(config), storage).run_core_result(context, "Synthetic company", as_of_date=DAY,
                   packet=research_packet(scope), research_spec=spec(), workflow="company")
    assert outcome.status == RunStatus.FAILED
    trace = TraceRepository(storage).read(outcome.run_id, scope)
    assert len(trace["model_calls"]) == 1 and trace["model_calls"][0]["status"] == "interrupted"
    assert "private-marker" not in canonical(trace) and "private-marker" not in outcome.output
    assert all(client.is_closed() for client in endpoint.clients)


def test_configured_model_cannot_run_in_offline_replay_mode(host):
    config, storage, context, _ = host
    scope = Scope.from_context(ROOT, context)
    with patch("a_share_claw.agent.build_model_client") as client:
        outcome = InvestmentAgent(configured(config), storage).run_core_result(context, "Synthetic replay", as_of_date=DAY,
                  packet=research_packet(scope), research_spec=spec(), workflow="company", mode="replay")
    assert outcome.status == RunStatus.BLOCKED and json.loads(outcome.output)["error_code"] == "model_replay_not_supported"
    client.assert_not_called()


def test_sdk_transmits_required_role_identity_and_question_schema(host):
    config,storage,context,_=host;endpoint=OfflineEndpoint()
    checked=[];original=endpoint.response
    def response(request):
        body=json.loads(request.content);schema=body["response_format"]["json_schema"]["schema"]
        assert schema["additionalProperties"] is False
        entry=json.loads(body["messages"][-1]["content"])
        if "role" in entry:
            answer=schema["properties"]["answers"]["items"]
            assert set(answer["required"])=={"question_id","fact_ids","inference"}
            assert schema["properties"]["packet_id"]["enum"]==[entry["packet_id"]]
        else:assert schema["properties"]["candidate_hash"]["enum"]==[entry["candidate_hash"]]
        checked.append(True);return original(request)
    endpoint.response=response
    scope=Scope.from_context(ROOT,context)
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client):
        out=InvestmentAgent(configured(config),storage).run_core_result(context,"Synthetic schema-bound research",as_of_date=DAY,
            packet=research_packet(scope),research_spec=spec(),workflow="industry")
    assert out.status==RunStatus.SUCCEEDED and len(checked)==3
