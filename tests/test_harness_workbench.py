"""Scoped UI and fine-grained action boundaries; fixed synthetic facts only."""
import argparse
import asyncio
import json
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from a_share_claw.agent import InvestmentAgent
from a_share_claw.harness.cli import run_harness
from a_share_claw.harness.contracts import RunRequest, RunStatus, Scope, canonical
from a_share_claw.harness.runtime import RunSession
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.harness.workbench import export_workbench
from test_harness import DAY, ROOT, environment, packet
from test_harness_delivery import compute
from test_harness_hosts import host
from test_harness_research import spec, research_packet, role_reply, fixture_review
from test_sdk_research import OfflineEndpoint, configured


def actions(trace):
    return [r for r in trace['run_steps'] if r['stage']=='react_action']


def assert_closed(trace):
    rows=actions(trace);opened={r['detail']['span_id']:r for r in rows if r['detail']['boundary']=='start'}
    ended={r['detail']['span_id']:r for r in rows if r['detail']['boundary']=='end'}
    assert opened.keys()==ended.keys()
    assert len(rows)==len(opened)*2
    for sid,start in opened.items():
        end=ended[sid]
        assert end['id']>start['id'] and end['detail']['operation']==start['detail']['operation']
        assert end['detail']['duration_ms']>=0
    return rows


def test_actual_sdk_models_roles_evaluators_and_artifacts_have_boundaries(host):
    config,storage,context,tmp=host;endpoint=OfflineEndpoint()
    scope=Scope.from_context(ROOT,context)
    with patch('a_share_claw.agent.build_model_client',side_effect=endpoint.client):
        out=InvestmentAgent(configured(config),storage).run_core_result(context,'Synthetic nested action spans',as_of_date=DAY,
            packet=research_packet(scope),research_spec=spec(),workflow='industry')
    assert out.status==RunStatus.SUCCEEDED,out.output
    trace=TraceRepository(storage).read(out.run_id,scope);rows=assert_closed(trace)
    starts=[r['detail'] for r in rows if r['detail']['boundary']=='start']
    models=[r for r in starts if r['kind']=='model']
    assert len(models)==3
    by_id={r['span_id']:r for r in starts}
    assert all(by_id[m['parent_span_id']]['kind'] in {'role','evaluation'} for m in models)
    assert len([r for r in starts if r['kind']=='role'])==2
    assert any(r['operation']=='report.html' for r in starts)
    assert all('decision_code' in r and 'input_hash' in r for r in starts)
    assert 'Synthetic interpretation' not in canonical(trace) and 'offline-only-not-a-credential' not in canonical(trace)


def test_late_nested_callback_span_closes_interrupted_and_cannot_append(environment):
    storage,scope,engine,_=environment;started,release,done=Event(),Event(),Event();sessions=[]
    original=RunSession.action
    def capture(self,*args,**kwargs):
        if self not in sessions:sessions.append(self)
        return original(self,*args,**kwargs)
    def role(payload):
        with sessions[0].action('model','synthetic_late','bounded_model',{}) as span:
            started.set();release.wait(5);span.observe(output_hash='synthetic')
        done.set();return role_reply(payload)
    async def exercise():
        with patch.object(RunSession,'action',new=capture):
            task=asyncio.create_task(engine.run_async(RunRequest(scope,'Synthetic nested cancellation',DAY,'research','industry'),
                research_packet(scope),research_spec=spec(),role_runner=role,semantic_reviewer=fixture_review))
            assert await asyncio.to_thread(started.wait,2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            repo=TraceRepository(storage);rid=repo.list_runs(scope)[0]['run_id'];before=repo.read(rid,scope)
            rows=assert_closed(before)
            assert any(r['status']=='interrupted' and r['detail']['operation']=='synthetic_late' for r in rows)
            release.set();assert await asyncio.to_thread(done.wait,2)
            assert repo.read(rid,scope)==before
    asyncio.run(exercise())


def test_scoped_workbench_contains_reports_plans_and_gaps_but_never_failed_candidates(environment):
    storage,scope,engine,tmp=environment;repo=TraceRepository(storage)
    good=compute(environment,'quant')
    plan=engine.run(RunRequest(scope,'Synthetic plan',DAY,'plan','macro'))
    blocked=engine.run(RunRequest(scope,'Synthetic missing facts',DAY,'research','macro'))
    result=export_workbench(repo,scope,engine.artifact_root,tmp/'ui')
    root=Path(result['index']).parent;index=(root/'index.html').read_text()
    assert all(r.run_id in index for r in (good,plan,blocked))
    view=(root/(good.run_id+'.html')).read_text()
    assert 'ReAct 执行时间轴' in view and 'input_hash' not in view and '输入哈希' in view
    assert '<script' not in view and (root/(good.run_id+'.report.html')).exists()
    gap=(root/(blocked.run_id+'.html')).read_text()
    assert 'NO_ACTION' in gap and 'missing_required_data' in gap and 'us_macro' in gap
    assert not (root/(blocked.run_id+'.report.html')).exists()
    assert '冻结框架' in (root/(plan.run_id+'.html')).read_text()
    other=Scope(scope.workspace,'other',scope.session)
    with pytest.raises(LookupError):export_workbench(repo,other,engine.artifact_root,tmp/'other',run_id=good.run_id)
    with pytest.raises(LookupError):export_workbench(repo,scope,engine.artifact_root,tmp/'cutoff',run_id=good.run_id,as_of_date='2026-07-01')
    assert not (tmp/'other').exists() and not (tmp/'cutoff').exists()


def test_workbench_rejects_tampered_report_and_output_scope_symlink(environment):
    storage,scope,engine,tmp=environment;out=compute(environment)
    root=tmp/'ui';root.mkdir();outside=tmp/'outside';outside.mkdir();(root/scope.key).symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError):export_workbench(TraceRepository(storage),scope,engine.artifact_root,root)
    assert not list(outside.iterdir())
    Path(json.loads(out.output)['report_html']['path']).write_text('tampered')
    with pytest.raises(ValueError):export_workbench(TraceRepository(storage),scope,engine.artifact_root,tmp/'bad')
    assert not (tmp/'bad').exists()


def test_ui_cli_and_html_host_read_share_scope_gate(host,capsys):
    config,storage,context,tmp=host;scope=Scope.from_context(ROOT,context)
    from a_share_claw.harness.engine import Harness
    out=Harness(ROOT,storage,tmp/'harness_runs').run(RunRequest(scope,'Synthetic UI CLI',DAY,'research','macro'),packet(scope))
    args=argparse.Namespace(command='harness',harness_command='ui',run_id=out.run_id,date=DAY,limit=20,
        platform='local',user='local-user',chat='local-chat',agent_key='default')
    assert run_harness(args,config,storage)==0
    result=json.loads(capsys.readouterr().out)
    assert Path(result['index']).exists()
    assert InvestmentAgent(config,storage).read_core_report(context,out.run_id,format='html').startswith('<!doctype html>')
    args.user='other'
    assert run_harness(args,config,storage)==2
    assert json.loads(capsys.readouterr().out)['error_code']=='view_not_found'
