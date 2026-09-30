"""Async core acceptance after cancellation/publication implementation."""
import asyncio
import json
from threading import Event
from unittest.mock import patch

import httpx
import pytest
from openai import AsyncOpenAI

from a_share_claw.agent import InvestmentAgent
from a_share_claw.harness.contracts import RunRequest, RunStatus, canonical
from a_share_claw.harness.runtime import RunControl
from a_share_claw.harness.trace import TraceRepository
from test_harness import DAY, environment
from test_harness_hosts import host
from test_harness_research import spec, research_packet, role_reply, fixture_review
from test_harness_framework import proposal, review, FrameworkEndpoint
from test_harness_mixed import mixed_spec, mixed_packet
from test_sdk_research import configured


def test_async_core_success_and_event_loop_remains_available(environment):
    storage,scope,engine,_=environment
    started,release=Event(),Event()
    def runner(payload):
        started.set();assert release.wait(timeout=2);return role_reply(payload)
    async def exercise():
        task=asyncio.create_task(engine.run_async(RunRequest(scope,"Synthetic async industry",DAY,"official","industry"),
            research_packet(scope),research_spec=spec(),role_runner=runner,semantic_reviewer=fixture_review))
        assert await asyncio.to_thread(started.wait,1)
        # This coroutine runs while the synchronous role is still waiting.
        await asyncio.sleep(0);assert not task.done();release.set()
        return await task
    out=asyncio.run(exercise())
    assert out.status==RunStatus.SUCCEEDED and out.official_output_allowed
    assert TraceRepository(storage).read_state(scope,"industry")["run_id"]==out.run_id


@pytest.mark.parametrize("stage",["framework","role","report_archive"])
def test_host_cancellation_finishes_core_before_return_and_late_callback_cannot_publish(environment,stage):
    storage,scope,engine,_=environment
    started,release,late=Event(),Event(),Event()
    original=engine._archive
    def pause(value):
        started.set();release.wait(timeout=2);late.set();return value
    def runner(payload):return pause(role_reply(payload)) if stage=="role" else role_reply(payload)
    def proposer(payload):return pause(proposal()) if stage=="framework" else proposal()
    def archive(session,name,value):
        if stage=="report_archive" and name=="report":pause(None)
        return original(session,name,value)
    async def exercise():
        with patch.object(engine,"_archive",side_effect=archive):
            task=asyncio.create_task(engine.run_async(RunRequest(scope,"Synthetic cancellation",DAY,"official","industry"),
                research_packet(scope),framework_proposer=proposer,framework_reviewer=review,
                role_runner=runner,semantic_reviewer=fixture_review))
            assert await asyncio.to_thread(started.wait,1)
            task.cancel()
            if stage=="report_archive":
                # Local archive I/O is not preempted; drain it before finishing the trace.
                await asyncio.sleep(.03);assert not task.done();task.cancel();release.set()
            with pytest.raises(asyncio.CancelledError):await task
        trace=TraceRepository(storage).list_runs(scope)[0]
        assert trace["status"]=="cancelled"
        assert TraceRepository(storage).read_state(scope,"industry") is None
        release.set();assert await asyncio.to_thread(late.wait,1)
        await asyncio.sleep(.06)
        assert TraceRepository(storage).read(trace["run_id"],scope)["status"]=="cancelled"
        assert TraceRepository(storage).read_state(scope,"industry") is None
    asyncio.run(exercise())


def test_pre_cancelled_control_records_terminal_run_without_models(environment):
    storage,scope,engine,_=environment;control=RunControl();control.cancel()
    async def exercise():
        return await engine.run_async(RunRequest(scope,"Synthetic pre-cancel",DAY,"plan","industry"),control=control,
            framework_proposer=lambda payload:pytest.fail("cancelled request started model"),framework_reviewer=review)
    out=asyncio.run(exercise());assert out.status==RunStatus.CANCELLED
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert not trace["model_calls"] and not any(v["stage"]=="framework_start" for v in trace["run_steps"])


def test_async_mixed_cancellation_propagates_to_current_child(environment):
    storage,scope,engine,_=environment;started,release=Event(),Event()
    def runner(payload):started.set();release.wait(timeout=2);return role_reply(payload)
    async def exercise():
        task=asyncio.create_task(engine.run_async(RunRequest(scope,"Synthetic mixed cancellation",DAY,"official","mixed"),
            mixed_packet(scope),mixed_spec=mixed_spec(),role_runner=runner,semantic_reviewer=fixture_review))
        assert await asyncio.to_thread(started.wait,1);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        release.set()
    asyncio.run(exercise())
    runs=TraceRepository(storage).list_runs(scope)
    assert len(runs)==3 and not any(v["status"]=="running" for v in runs)
    assert sum(v["status"]=="cancelled" for v in runs)==2
    assert all(TraceRepository(storage).read_state(scope,w) is None for w in ("mixed","industry","macro"))


def test_async_budget_exhaustion_cannot_publish_late_role(environment):
    storage,scope,engine,_=environment;release,finished=Event(),Event()
    def runner(payload):release.wait(timeout=2);finished.set();return role_reply(payload)
    async def exercise():
        out=await engine.run_async(RunRequest(scope,"Synthetic async deadline",DAY,"official","industry",wall_clock_seconds=.3),
            research_packet(scope),research_spec=spec(),role_runner=runner,semantic_reviewer=fixture_review)
        assert out.status==RunStatus.FAILED and json.loads(out.output)["error_code"]=="budget_exceeded"
        release.set();assert await asyncio.to_thread(finished.wait,1)
        return out
    out=asyncio.run(exercise())
    assert TraceRepository(storage).read_state(scope,"industry") is None
    assert TraceRepository(storage).read(out.run_id,scope)["status"]=="failed"


def test_async_inputs_are_snapshotted_before_dispatch(environment):
    _,scope,engine,_=environment;started,release=Event(),Event();facts=research_packet(scope)
    def proposer(payload):started.set();release.wait(timeout=2);return proposal()
    async def exercise():
        task=asyncio.create_task(engine.run_async(RunRequest(scope,"Synthetic snapshot",DAY,"research","industry"),facts,
            framework_proposer=proposer,framework_reviewer=review,role_runner=role_reply,semantic_reviewer=fixture_review))
        assert await asyncio.to_thread(started.wait,1)
        facts["facts"].clear();release.set();return await task
    assert asyncio.run(exercise()).status==RunStatus.SUCCEEDED


@pytest.mark.parametrize("entry",["trusted","chat"])
def test_actual_sdk_async_host_cancels_request_and_closes_client(host,entry):
    config,storage,context,_=host;endpoint=FrameworkEndpoint();started,closed=Event(),Event()
    async def response(request):
        started.set()
        try:await asyncio.sleep(5)
        finally:closed.set()
        return endpoint.response(request)
    def client(config):
        c=AsyncOpenAI(base_url=config.model_base_url,api_key="offline-only-not-a-credential",max_retries=0,
            http_client=httpx.AsyncClient(trust_env=False,transport=httpx.MockTransport(response)))
        endpoint.clients.append(c);return c
    async def exercise():
        with patch("a_share_claw.agent.build_model_client",side_effect=client):
            agent=InvestmentAgent(configured(config),storage)
            pending=(agent.run_core_result_async(context,"Synthetic async SDK framework",as_of_date=DAY,workflow="industry")
                     if entry=="trusted" else agent.run_result(context,DAY+" 分析合成产业链"))
            task=asyncio.create_task(pending)
            assert await asyncio.to_thread(started.wait,1);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert await asyncio.to_thread(closed.wait,1)
            for _ in range(100):
                if all(c.is_closed() for c in endpoint.clients):break
                await asyncio.sleep(.01)
            assert all(c.is_closed() for c in endpoint.clients)
    asyncio.run(exercise())
    from a_share_claw.harness.contracts import Scope
    runs=TraceRepository(storage).list_runs(Scope.from_context(config.root_dir,context))
    assert len(runs)==1 and runs[0]["status"]=="cancelled"
    trace=TraceRepository(storage).read(runs[0]["run_id"],Scope.from_context(config.root_dir,context))
    assert len(trace["model_calls"])==1 and trace["model_calls"][0]["status"]=="interrupted"
    assert not trace["tool_calls"]


def test_actual_sdk_async_host_compiles_and_executes_one_run(host):
    config,storage,context,_=host;endpoint=FrameworkEndpoint();agent=InvestmentAgent(configured(config),storage)
    from a_share_claw.harness.contracts import Scope
    scope=Scope.from_context(config.root_dir,context)
    async def exercise():
        with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client):
            return await agent.run_core_result_async(context,"Synthetic async supplier",as_of_date=DAY,
                workflow="industry",packet=research_packet(scope))
    out=asyncio.run(exercise());assert out.status==RunStatus.SUCCEEDED and not out.official_output_allowed
    assert len(endpoint.requests)==5 and all(c.is_closed() for c in endpoint.clients)
    assert len(TraceRepository(storage).list_runs(scope))==1
    assert "synthetic" in agent.read_core_report(context,out.run_id).lower()


def test_cancellation_between_publish_gate_and_commit_is_denied(environment):
    from a_share_claw.harness.runtime import RunSession
    from a_share_claw.harness.delivery import read_report
    storage,scope,engine,tmp=environment;original=RunSession.finish
    def finish(session,output,status=RunStatus.SUCCEEDED,**kwargs):
        if status==RunStatus.SUCCEEDED:session.control.cancel()
        return original(session,output,status,**kwargs)
    with patch.object(RunSession,"finish",new=finish):
        out=engine.run(RunRequest(scope,"Synthetic final cancellation race",DAY,"official","industry"),
            research_packet(scope),research_spec=spec(),role_runner=role_reply,semantic_reviewer=fixture_review)
    assert out.status==RunStatus.CANCELLED and out.action=="NO_ACTION" and not out.official_output_allowed
    result=json.loads(out.output);assert result["data"] is None and "report" not in result
    assert TraceRepository(storage).read_state(scope,"industry") is None
    with pytest.raises(LookupError,match="report_not_found"):read_report(TraceRepository(storage),scope,out.run_id,tmp/"artifacts")


def test_cancellation_after_committed_run_preserves_successful_history(environment):
    storage,scope,engine,_=environment;control=RunControl()
    out=engine.run(RunRequest(scope,"Synthetic completed before cancellation",DAY,"official","industry"),
        research_packet(scope),research_spec=spec(),role_runner=role_reply,semantic_reviewer=fixture_review,control=control)
    assert out.status==RunStatus.SUCCEEDED;control.cancel()
    repo=TraceRepository(storage)
    assert repo.read(out.run_id,scope)["status"]=="succeeded"
    assert repo.read_state(scope,"industry")["run_id"]==out.run_id
