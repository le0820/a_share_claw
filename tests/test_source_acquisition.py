"""Implemented host acquisition checks; synthetic sources/models only, no quarterly case."""
import asyncio
import copy
import json
from dataclasses import replace
from datetime import datetime,timezone
from threading import Event
from pathlib import Path

import pytest

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.adapter import PluginEvidenceAdapter
from a_share_claw.data_plugins.providers import FRED,SEC
from a_share_claw.data_plugins.tdx import EasyTDX
from a_share_claw.harness.acquisition import EvidenceBatch
from a_share_claw.harness.contracts import RunRequest,RunStatus,canonical,digest
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.harness.outlook import derived_requirements
from test_harness import environment
from test_harness_framework import review
from test_source_core_handoff import clock,Clock,DAY,planned,fetch_all,reply
from test_tdx_price_handoff import IndexTransport,index_spec,price_plan,price_bindings
from test_fred_core_handoff import NativeTransport as FredTransport,fred_spec,fred_plan,fred_bindings
from test_sec_core_handoff import NativeTransport as SecTransport,spec,plan as sec_plan,bind,company_reply
from test_harness_quant import outlook_review

REFERENCE=datetime(2026,7,14,2,tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def source_clock(clock,monkeypatch):monkeypatch.setattr('a_share_claw.data_plugins.tdx.datetime',Clock)


def configuration():
    p=fred_plan();p['requirements']+=sec_plan()['requirements']+price_plan()['requirements']
    return {'source_plan':p,'bindings':fred_bindings()+bind(),'price_bindings':price_bindings(),'cutoff_timestamp':DAY+'T01:00:00Z'}


def specification():
    s=fred_spec();s['quant_spec']=index_spec()
    s['research_spec']['required_facts']=[f for f in s['research_spec']['required_facts'] if not f['fact_id'].startswith('price.')]+derived_requirements(index_spec())+spec()['required_facts']
    next(q for q in s['research_spec']['questions'] if q['question_id']=='market_comparison')['required_fact_ids']=[f['fact_id'] for f in derived_requirements(index_spec())]
    s['research_spec']['questions'][0]['required_fact_ids'].append('sec.revenue')
    return s


def setup(environment,*,fred_configured=True,index_transport=None,configuration_override=None):
    _,scope,_,tmp=environment;base,_=planned(tmp/'base',scope);ps=dict(base.providers);transports=[p.transport for p in ps.values()]
    ft=FredTransport();st=SecTransport();it=index_transport or IndexTransport()
    ps.update(fred=FRED({'FRED_API_KEY':'synthetic-key'} if fred_configured else {},ft),sec=SEC({'SEC_USER_AGENT':'synthetic contact@example.com'},st),easytdx=EasyTDX({},it))
    transports.extend([ft,st,it]);adapter=PluginEvidenceAdapter(tmp/'sources',configuration_override or configuration(),providers=ps)
    return adapter,transports,ps


def run(environment,adapter,packet=None,**kwargs):
    _,scope,engine,_=environment
    return engine.run(RunRequest(scope,'Synthetic frozen host source handoff',DAY,'research','outlook'),packet,outlook_spec=specification(),
        evidence_adapter=adapter,role_runner=reply,semantic_reviewer=outlook_review,clock=REFERENCE,**kwargs)


def test_framework_source_compute_report_share_single_core_run(environment):
    storage,scope,engine,tmp=environment;adapter,transports,_=setup(environment);s=specification();events=[]
    def proposer(payload):
        assert not any(t.calls for t in transports);events.append('framework')
        return {'framework':'Synthetic five-source contract before acquisition','parameters':{'outlook_spec':s},'unresolved_constraints':[]}
    def reviewer(payload):assert not any(t.calls for t in transports);events.append('review');return review(payload)
    out=engine.run(RunRequest(scope,'Synthetic frozen five-source host',DAY,'research','outlook'),framework_proposer=proposer,framework_reviewer=reviewer,
        planning_constraints={'outlook_spec':s},evidence_adapter=adapter,role_runner=reply,semantic_reviewer=outlook_review,clock=REFERENCE)
    assert out.status==RunStatus.SUCCEEDED,out.output
    result=json.loads(out.output);trace=TraceRepository(storage).read(out.run_id,scope);stages=[r['stage'] for r in trace['run_steps']]
    assert events==['framework','review'] and stages.index('framework_end')<stages.index('plan')<stages.index('source_prepare')<stages.index('source_handoff')<stages.index('role_start')
    assert len(trace['tool_calls'])==11 and result['source_acquisition']['completed_calls']==11 and len(TraceRepository(storage).list_runs(scope))==1
    link=json.loads(next((tmp/'sources').rglob('host-core-link.json')).read_text());assert link['core_run_id']==out.run_id and link['plan_id']==result['plan']['plan_id']
    assert all(t['permission']=='trusted_host_source' and set(t['envelope']['data'])=={'sha256','chars'} for t in trace['tool_calls'])
    assert 'synthetic-key' not in canonical(trace) and 'contact@example.com' not in canonical(trace)
    assert len(result['data']['market_statistics']['metrics'])==3 and not out.official_output_allowed and out.action=='NO_ACTION'
    assert TraceRepository(storage).read_state(scope,'outlook') is None


def test_existing_macro_is_validated_then_only_missing_prices_fetched(environment):
    _,scope,_,tmp=environment;adapter,transports,ps=setup(environment);c=configuration();previous=DataRun(ps,tmp/'existing',scope)
    previous.plan_core_outlook(c['source_plan'],specification(),c['bindings'],price_bindings=c['price_bindings']);fetch_all(previous)
    packet={'as_of_date':DAY,'facts':[previous.core_macro_evidence()]};before=[len(t.calls) for t in transports]
    out=run(environment,adapter,packet);assert out.status==RunStatus.SUCCEEDED,out.output
    assert [len(t.calls)-n for t,n in zip(transports,before)]==[0,0,0,0,3]
    assert json.loads(out.output)['source_acquisition']['planned_calls']==3


def test_complete_existing_packet_causes_zero_new_sources(environment):
    _,scope,_,tmp=environment;adapter,transports,ps=setup(environment);c=configuration();previous=DataRun(ps,tmp/'existing',scope)
    previous.plan_core_outlook(c['source_plan'],specification(),c['bindings'],price_bindings=c['price_bindings']);fetch_all(previous)
    packet={'as_of_date':DAY,'facts':[previous.core_macro_evidence(),previous.core_price_evidence()]};before=[len(t.calls) for t in transports]
    out=run(environment,adapter,packet);assert out.status==RunStatus.SUCCEEDED,out.output
    assert [len(t.calls) for t in transports]==before and json.loads(out.output)['source_acquisition']['planned_calls']==0
    assert not (tmp/'sources').exists()


@pytest.mark.parametrize('change',['scope','hash','missing_fact','unit'])
def test_invalid_existing_evidence_blocks_before_source_prepare(environment,change):
    _,scope,_,tmp=environment;adapter,transports,ps=setup(environment);c=configuration();previous=DataRun(ps,tmp/'existing',scope)
    previous.plan_core_outlook(c['source_plan'],specification(),c['bindings'],price_bindings=c['price_bindings']);fetch_all(previous)
    env=previous.core_macro_evidence();before=[len(t.calls) for t in transports]
    if change=='scope':env['scope_key']='other'
    elif change=='hash':env['provenance']['sha256']='0'*64
    elif change=='missing_fact':env['data']['facts'].pop()
    else:env['data']['facts'][0]['unit']='ratio'
    if change in {'missing_fact','unit'}:env['provenance']['sha256']=digest(env['data'])
    out=run(environment,adapter,{'as_of_date':DAY,'facts':[env]})
    assert out.status!=RunStatus.SUCCEEDED and [len(t.calls) for t in transports]==before and not (tmp/'sources').exists()


def test_missing_fred_credential_remains_explicit_source_gap(environment):
    storage,scope,_,_=environment;adapter,transports,_=setup(environment,fred_configured=False)
    out=run(environment,adapter);r=json.loads(out.output)
    assert out.status!=RunStatus.SUCCEEDED and r['error_code']=='missing_required_data'
    gaps=r['source_acquisition']['gaps'];assert len(gaps)==4 and {g['error_code'] for g in gaps}=={'not_configured'} and not transports[2].calls
    assert not r['data'] and 'report' not in r and TraceRepository(storage).read_state(scope,'outlook') is None


@pytest.mark.parametrize('mode',['official','replay'])
def test_sources_not_authorized_for_publication_or_replay(environment,mode):
    _,scope,engine,_=environment;adapter,ts,_=setup(environment)
    out=engine.run(RunRequest(scope,'Synthetic restricted mode',DAY,mode,'outlook'),outlook_spec=specification(),evidence_adapter=adapter,clock=REFERENCE)
    assert json.loads(out.output)['error_code']=='source_acquisition_not_authorized' and not any(t.calls for t in ts)


def test_plan_mode_does_not_prepare_or_load_sources(environment):
    _,scope,engine,tmp=environment;adapter,ts,_=setup(environment)
    out=engine.run(RunRequest(scope,'Synthetic plan only',DAY,'plan','outlook'),outlook_spec=specification(),evidence_adapter=adapter)
    assert out.status==RunStatus.SUCCEEDED and not any(t.calls for t in ts) and not (tmp/'sources').exists()


def test_source_budget_rejects_entire_batch_before_first_fetch(environment):
    storage,scope,engine,_=environment;adapter,ts,_=setup(environment)
    out=engine.run(RunRequest(scope,'Synthetic bounded source budget',DAY,'research','outlook',max_tool_calls=10),outlook_spec=specification(),evidence_adapter=adapter,clock=REFERENCE)
    assert out.status==RunStatus.FAILED and json.loads(out.output)['error_code']=='budget_exceeded' and not any(t.calls for t in ts)
    assert not TraceRepository(storage).read(out.run_id,scope)['tool_calls']


@pytest.mark.parametrize('change',['wrong_binding','extra_requirement','wrong_window'])
def test_source_contract_mismatch_never_starts_network(environment,change):
    c=configuration()
    if change=='wrong_binding':c['bindings'][0]['selector']['metric']='m2_yoy'
    elif change=='extra_requirement':c['source_plan']['requirements'].append({**c['source_plan']['requirements'][0],'requirement_id':'unused'})
    else:c['source_plan']['requirements'][-1]['params']['start_date']='2026-07-08'
    adapter,ts,_=setup(environment,configuration_override=c);out=run(environment,adapter)
    assert json.loads(out.output)['error_code']=='source_contract_mismatch' and not any(t.calls for t in ts)


@pytest.mark.parametrize('field,value',[('scope_key','other'),('plan_id','other')])
def test_core_rejects_changed_batch_identity_before_fetch(environment,field,value):
    adapter,ts,_=setup(environment)
    class Changed:
        def prepare(self,r):
            b=adapter.prepare(r);ticket=b.json();ticket[field]=value
            return replace(b,document=canonical(ticket))
    out=run(environment,Changed());assert json.loads(out.output)['error_code']=='invalid_source_batch' and not any(t.calls for t in ts)


def test_constructor_pins_host_configuration_against_later_mutation(environment):
    c=configuration();adapter,_,_=setup(environment,configuration_override=c);c['bindings'][0]['selector']['metric']='m2_yoy'
    assert run(environment,adapter).status==RunStatus.SUCCEEDED


def test_source_async_cancellation_closes_core_and_prevents_later_fetches(environment):
    storage,scope,engine,_=environment;started,release,finished=Event(),Event(),Event()
    class Paused(IndexTransport):
        def request(self,p):started.set();release.wait(30);return super().request(p)
    transport=Paused();adapter,_,_=setup(environment,index_transport=transport)
    class Wrap:
        def prepare(self,r):
            b=adapter.prepare(r)
            def fetch(call):
                try:return b.fetch(call)
                finally:
                    if call.json()['requirement_id'].startswith('price'):finished.set()
            return replace(b,fetch=fetch)
    async def exercise():
        task=asyncio.create_task(engine.run_async(RunRequest(scope,'Synthetic cancellable acquisition',DAY,'research','outlook'),outlook_spec=specification(),
            evidence_adapter=Wrap(),role_runner=reply,semantic_reviewer=outlook_review,clock=REFERENCE))
        assert await asyncio.to_thread(started.wait,30);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        trace=TraceRepository(storage).list_runs(scope)[0];assert trace['status']=='cancelled'
        release.set();assert await asyncio.to_thread(finished.wait,30)
        trace=TraceRepository(storage).read(trace['run_id'],scope)
        assert len(transport.calls)==1 and trace['status']=='cancelled' and TraceRepository(storage).read_state(scope,'outlook') is None
        assert not any(r['stage']=='source_handoff' for r in trace['run_steps'])
    try:asyncio.run(exercise())
    finally:release.set()


def test_company_host_acquisition_uses_only_sec(environment):
    _,scope,engine,tmp=environment;t=SecTransport();c={'source_plan':sec_plan(),'bindings':bind(),'price_bindings':[],'cutoff_timestamp':DAY+'T01:00:00Z'}
    adapter=PluginEvidenceAdapter(tmp/'source',c,providers={'sec':SEC({'SEC_USER_AGENT':'synthetic contact@example.com'},t)})
    out=engine.run(RunRequest(scope,'Synthetic explicit company host',DAY,'research','company'),research_spec=spec(),evidence_adapter=adapter,
        role_runner=company_reply,semantic_reviewer=outlook_review,clock=REFERENCE)
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert json.loads(out.output)['source_acquisition']['completed_calls']==2 and len(t.calls)==2


@pytest.mark.parametrize('fixed_clock',[False,True])
def test_actual_capture_clock_advances_only_when_host_clock_not_fixed(environment,monkeypatch,fixed_clock):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    monkeypatch.setattr('a_share_claw.data_plugins.core.datetime',datetime)
    monkeypatch.setattr('a_share_claw.data_plugins.tdx.datetime',datetime)
    _,scope,engine,tmp=environment;now=datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    q=index_spec();q['cutoff_timestamp']=(now+timedelta(seconds=.4)).isoformat();p=price_plan()
    for r in p['requirements']:r['as_of_date']=day
    c={'source_plan':p,'bindings':[],'price_bindings':price_bindings(),'cutoff_timestamp':None}
    adapter=PluginEvidenceAdapter(tmp/'current-clock',c,providers={'easytdx':EasyTDX({},IndexTransport())})
    out=engine.run(RunRequest(scope,'Synthetic actual acquisition clock',day,'research','quant'),quant_spec=q,evidence_adapter=adapter,clock=now if fixed_clock else None)
    if fixed_clock:assert out.status!=RunStatus.SUCCEEDED and json.loads(out.output)['error_code']=='future_data'
    else:
        assert out.status==RunStatus.SUCCEEDED,out.output
        assert datetime.now(timezone.utc)>=datetime.fromisoformat(q['cutoff_timestamp'])


def test_cutoff_beyond_remaining_budget_waits_without_source_calls(environment):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    _,scope,engine,_=environment;adapter,ts,_=setup(environment);s=specification();now=datetime.now(timezone.utc)
    s['quant_spec']['cutoff_timestamp']=(now+timedelta(minutes=5)).isoformat();day=now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    out=engine.run(RunRequest(scope,'Synthetic future execution cutoff',day,'research','outlook',wall_clock_seconds=1),outlook_spec=s,evidence_adapter=adapter)
    assert json.loads(out.output)['error_code']=='WAIT_FOR_CUTOFF' and not any(t.calls for t in ts)


def test_missing_frozen_price_session_is_not_hidden_by_successful_fetches(environment):
    t=IndexTransport();t.omit='2026-07-10';adapter,_,_=setup(environment,index_transport=t)
    out=run(environment,adapter);r=json.loads(out.output)
    assert out.status!=RunStatus.SUCCEEDED and r['error_code'].startswith('insufficient_coverage:source_handoff.')
    assert r['source_acquisition']['completed_calls']==11 and not r['source_acquisition']['gaps'] and 'report' not in r


def test_registry_loads_only_bound_missing_capabilities(environment):
    from a_share_claw.data_plugins.core import Registry
    _,scope,_,tmp=environment;_,ts,ps=setup(environment);c=configuration();existing=DataRun(ps,tmp/'existing',scope)
    existing.plan_core_outlook(c['source_plan'],specification(),c['bindings'],price_bindings=c['price_bindings']);fetch_all(existing)
    packet={'as_of_date':DAY,'facts':[existing.core_macro_evidence()]};loaded=[];registry=Registry()
    for key,provider in ps.items():
        def factory(settings,transport,p=provider,k=key):loaded.append(k);return p
        factory.manifest=provider.manifest;registry.register(factory)
    adapter=PluginEvidenceAdapter(tmp/'registry-sources',c,registry=registry,settings={})
    out=run(environment,adapter,packet);assert out.status==RunStatus.SUCCEEDED,out.output
    assert loaded==['easytdx']


def test_actual_cli_source_contract_without_packet_reports_disabled_provider(tmp_path):
    import os,subprocess,sys
    from zoneinfo import ZoneInfo
    from datetime import timedelta
    archive=Path(__file__).resolve().parents[1];now=datetime.now(timezone.utc);day=now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    p=price_plan()
    for r in p['requirements']:r['as_of_date']=day
    q=index_spec();q['cutoff_timestamp']=(now-timedelta(seconds=1)).isoformat()
    c={'source_plan':p,'bindings':[],'price_bindings':price_bindings(),'cutoff_timestamp':None}
    contract=tmp_path/'contract.json';qs=tmp_path/'quant.json';contract.write_text(json.dumps(c));qs.write_text(json.dumps(q))
    env=os.environ.copy();env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(archive/'src'),ASCLAW_DATA_DIR=str(tmp_path/'data'),ASCLAW_DATA_PROVIDERS='')
    proc=subprocess.run([sys.executable,'-m','a_share_claw','harness','run','--workflow','quant','--date',day,'--mode','research',
        '--quant-spec',str(qs),'--source-contract',str(contract)],cwd=archive,env=env,capture_output=True,text=True,timeout=20)
    assert proc.returncode==2,proc.stderr
    result=json.loads(proc.stdout);assert result['error_code']=='missing_required_data'
    assert len(result['source_acquisition']['gaps'])==3 and {g['error_code'] for g in result['source_acquisition']['gaps']}=={'provider_unavailable'}
    assert not result['official_output_allowed'] and 'report' not in result
