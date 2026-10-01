"""Implemented current-snapshot handoff checks; all pages, clocks and prices are synthetic."""
import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.providers import NBS,PBC
from a_share_claw.harness.contracts import RunRequest,RunStatus,digest
from a_share_claw.harness.trace import TraceRepository
from test_data_plugins import FakeTransport,requirement
from test_harness import environment
from test_harness_quant import outlook_spec,quant_packet,outlook_review
from test_macro_mapping import NBS_TEXT,PBC_TEXT

DAY='2026-07-14'
CAPTURE=datetime(2026,7,14,0,tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return CAPTURE.astimezone(tz) if tz else CAPTURE.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    import a_share_claw.data_plugins.core as core
    import a_share_claw.data_plugins.providers as providers
    monkeypatch.setattr(core,'datetime',Clock);monkeypatch.setattr(providers,'datetime',Clock)


def specification():
    spec=outlook_spec();spec['quant_spec']['cutoff_timestamp']=DAY+'T01:00:00Z'
    derived=[f for f in spec['research_spec']['required_facts'] if f['fact_id'].startswith('price.')]
    source=[{'fact_id':fid,'entity':'CN','metric':metric,'unit':'percent','value_type':'number','data_period':'2026-06-01/2026-06-30',
        'observation_start':'2026-06-30','observation_end':'2026-06-30'}
        for fid,metric in [('cn.industry','industrial_value_added_yoy'),('cn.m2','m2_yoy')]]
    spec['research_spec']['required_facts']=source+derived
    for q in spec['research_spec']['questions']:
        if q['question_id']!='market_comparison':q['required_fact_ids']=['cn.industry','cn.m2']
    return spec


def bindings():
    return [{'fact_id':fid,'requirement_id':rid,'selector':{'metric':metric,'year':2026,'month':6,'period_kind':'month'}}
        for fid,rid,metric in [('cn.industry','nbs','industrial_value_added_yoy'),('cn.m2','pbc','m2_yoy')]]


def source_plan(capability='macro.release_snapshot',day=DAY):
    return {'framework':'Synthetic core outlook and explicit native source requirements before acquisition','requirements':[
        requirement(key,capability,{'url':f'https://{provider.manifest.hosts[0]}/synthetic.html'},day=day,rid=key).json()
        for key,provider in [('nbs',NBS),('pbc',PBC)]]}


def planned(tmp_path,scope,spec=None,plan=None,bind=None):
    providers={};transports=[]
    for key,provider,text in [('nbs',NBS,NBS_TEXT),('pbc',PBC,PBC_TEXT)]:
        raw=('<html><meta name="PubDate" content="2026/07/13 10:00">'+text.replace('8月','6月').replace('—8','—6').replace('前八','前六')+'</html>').encode()
        transport=FakeTransport(raw);transports.append(transport);providers[key]=provider({},transport)
    run=DataRun(providers,tmp_path,scope)
    run.plan_core_outlook(plan or source_plan(),spec or specification(),bind or bindings())
    return run,transports


def fetch_all(run):
    for key in run.requirements:asyncio.run(run.fetch(key))


def reply(payload):
    e=payload.json();qs=[q for q in e['plan']['parameters']['research_spec']['questions'] if q['role']==e['role']]
    return {'role':e['role'],'phase':e['phase'],'packet_id':e['packet_id'],'packet_version':e['packet_version'],
        'answers':[{'question_id':q['question_id'],'fact_ids':q['required_fact_ids'],'inference':'Conditional synthetic snapshot outlook only.'} for q in qs],
        'responds_to':[],'unknowns':[],'monitoring_triggers':[{'condition':'Reassess on a new dated source capture.','fact_ids':['cn.industry','cn.m2']}] if e['role']=='ping_heng' else []}


def test_source_pages_to_core_roles_and_dual_reports_keep_both_clocks(environment,clock):
    storage,scope,engine,tmp=environment
    run,transports=planned(tmp/'source',scope);fetch_all(run)
    envelope=run.core_macro_evidence();assert run.core_macro_evidence()==envelope
    assert all(len(t.calls)==1 for t in transports)
    assert envelope['scope_key']==scope.key and envelope['data']['schema_version']=='macro-release-facts-v2'
    assert {f['value'] for f in envelope['data']['facts']}=={5.1,7.6}
    for fact in envelope['data']['facts']:
        assert fact['publication_date']=='2026-07-13' and fact['available_at']==CAPTURE.isoformat()
        assert fact['availability']['publisher_available_at']=='2026-07-13T10:00:00+08:00'
        assert not fact['availability']['historical_vintage_certified'] and fact['fallback_status']=='none'
        assert isinstance(fact['availability']['source_notes'],list)
    packet=quant_packet(scope);packet['facts'].append(envelope);seen=[]
    def runner(payload):seen.append(payload.json());return reply(payload)
    out=engine.run(RunRequest(scope,'Synthetic source/core snapshot protocol',DAY,'research','outlook'),packet,
        outlook_spec=specification(),role_runner=runner,semantic_reviewer=outlook_review,
        clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert out.action=='NO_ACTION' and not out.official_output_allowed
    result=json.loads(out.output);confirmed=result['data']['confirmed_facts']
    assert {f['fact_id'] for f in confirmed if 'availability' in f}=={'cn.industry','cn.m2'}
    assert seen and all(any('availability' in f for f in call['packet']['facts'].values()) for call in seen)
    markdown=Path(result['report_markdown']['path']).read_text()
    assert '当前快照口径' in markdown and '不证明抓取前的历史页面版本' in markdown
    assert TraceRepository(storage).read_state(scope,'macro') is None


@pytest.mark.parametrize('day',['2026-07-13','2026-07-12'])
def test_current_capture_capability_refuses_historical_request_before_network(tmp_path,clock,day):
    transport=FakeTransport(b'<html></html>');provider=NBS({},transport)
    run=DataRun({'nbs':provider},tmp_path,'legacy')
    plan=source_plan(day=day);plan['requirements']=plan['requirements'][:1];run.plan(plan)
    result=asyncio.run(run.fetch('nbs'))
    assert result['error_code']=='historical_unavailable' and not transport.calls


def test_legacy_unverified_page_cannot_be_promoted_by_core_binding(environment,clock):
    _,scope,_,tmp=environment
    with pytest.raises(DataError) as error:planned(tmp,scope,plan=source_plan(capability='macro.release'))
    assert error.value.code=='mapping_unavailable' and not list(tmp.rglob('*.raw'))


@pytest.mark.parametrize('change',[{'entity':'US'},{'metric':'cpi_yoy'},{'unit':'ratio'},{'data_period':'2026-01-01/2026-06-30'}])
def test_pre_acquisition_binding_rejects_entity_metric_unit_or_period_relabel(environment,clock,change):
    _,scope,_,tmp=environment;spec=specification();spec['research_spec']['required_facts'][0].update(change)
    with pytest.raises(DataError) as error:planned(tmp,scope,spec=spec)
    assert error.value.code=='research_fact_contract_mismatch' and not list(tmp.rglob('*.raw'))


def test_bridge_requires_trusted_scope_and_contract_before_any_fetch(environment,clock):
    _,scope,_,tmp=environment
    with pytest.raises(DataError) as error:planned(tmp,'untrusted-string')
    assert error.value.code=='scope_mismatch'
    run=DataRun({},tmp/'unbound',scope)
    with pytest.raises(DataError) as error:run.core_macro_evidence()
    assert error.value.code=='plan_required'


@pytest.mark.parametrize('target',['raw','result','core_contract'])
def test_handoff_independently_checks_source_and_contract_archives(environment,clock,target):
    _,scope,_,tmp=environment;run,_=planned(tmp,scope);fetch_all(run)
    if target=='raw':(run.directory/run.results['nbs']['provenance']['artifact']).write_bytes(b'modified')
    elif target=='result':(run.directory/'results/nbs.json').write_text('{}')
    else:(run.directory/'core-contract.json').write_text('{}')
    with pytest.raises(DataError) as error:run.core_macro_evidence()
    assert error.value.code=='hash_mismatch' and not (run.directory/'core-macro-evidence.json').exists()


def test_frozen_cutoff_before_capture_is_rejected(environment,clock):
    _,scope,_,tmp=environment;spec=specification();spec['quant_spec']['cutoff_timestamp']='2026-07-13T21:00:00Z'
    run,_=planned(tmp,scope,spec=spec);fetch_all(run)
    with pytest.raises(DataError) as error:run.core_macro_evidence()
    assert error.value.code=='future_data'


def test_core_rechecks_scope_and_frozen_requirement_hash_before_model(environment,clock):
    _,scope,engine,tmp=environment;run,_=planned(tmp,scope);fetch_all(run);envelope=run.core_macro_evidence()
    packet=quant_packet(scope);packet['facts'].append(envelope)
    spec=specification();spec['research_spec']['questions'][0]['question']='A changed requirement contract.'
    out=engine.run(RunRequest(scope,'Synthetic changed core contract',DAY,'research','outlook'),packet,
        outlook_spec=spec,role_runner=lambda _:pytest.fail('Contract mismatch reached the model'),semantic_reviewer=outlook_review,
        clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert json.loads(out.output)['error_code']=='research_fact_contract_mismatch'
    packet['facts'][-1]['scope_key']='0'*64
    out=engine.run(RunRequest(scope,'Synthetic wrong scope',DAY,'research','outlook'),packet,
        outlook_spec=specification(),role_runner=lambda _:pytest.fail('Wrong scope reached the model'),semantic_reviewer=outlook_review,
        clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert json.loads(out.output)['error_code']=='scope_mismatch'


@pytest.mark.parametrize('mutation',['capture_day','publisher_future','invent_vintage','no_capture','fake_selection_hash'])
def test_core_rejects_invalid_snapshot_availability_even_with_recomputed_data_hash(environment,clock,mutation):
    _,scope,engine,tmp=environment;run,_=planned(tmp,scope);fetch_all(run);envelope=run.core_macro_evidence()
    fact=envelope['data']['facts'][0];meta=fact['availability']
    if mutation=='capture_day':meta['snapshot_as_of_date']='2026-07-13'
    elif mutation=='publisher_future':meta['publisher_available_at']='2026-07-13T23:59:00-04:00'
    elif mutation=='invent_vintage':meta['historical_vintage_certified']=True
    elif mutation=='no_capture':fact['available_at']=None
    else:meta['selection_hash']='invented'
    envelope['provenance']['sha256']=digest(envelope['data'])
    packet=quant_packet(scope);packet['facts'].append(envelope)
    out=engine.run(RunRequest(scope,'Synthetic malformed availability',DAY,'research','outlook'),packet,
        outlook_spec=specification(),role_runner=lambda _:pytest.fail('Bad availability reached the model'),semantic_reviewer=outlook_review,
        clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status!=RunStatus.SUCCEEDED and not out.official_output_allowed
    assert 'report' not in json.loads(out.output)


@pytest.mark.parametrize('change',[{'period_kind':[]},{'metric':[]},{'month':True}])
def test_pre_acquisition_selector_types_fail_without_network(environment,clock,change):
    _,scope,_,tmp=environment;bind=bindings();bind[0]['selector'].update(change)
    with pytest.raises(DataError) as error:planned(tmp,scope,bind=bind)
    assert error.value.code=='invalid_request' and not list(tmp.rglob('*.raw'))


def test_current_snapshot_date_only_release_keeps_unknown_publisher_clock(environment,clock):
    _,scope,_,tmp=environment
    raw=('<html><meta name="PubDate" content="2026-07-13">'+NBS_TEXT.replace('8月','6月').replace('—8','—6')+'</html>').encode()
    run=DataRun({'nbs':NBS({},FakeTransport(raw))},tmp,scope)
    plan=source_plan();plan['requirements']=plan['requirements'][:1]
    run.plan(plan);result=asyncio.run(run.fetch('nbs'))
    selected=run.select('nbs',bindings()[0]['selector'])['observation']
    assert selected['available_at']==CAPTURE.isoformat() and selected['publisher_available_at'] is None
    assert selected['publication_time_precision']=='day' and selected['eligibility']=='verified_current_snapshot'
    assert not run.summary()['official_output_allowed']
