"""Implemented SEC-to-core handoff checks; synthetic current JSON, dates and roles only."""
import asyncio
import copy
import json
from datetime import datetime,timezone
from pathlib import Path

import pytest

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.providers import SEC
from a_share_claw.data_plugins.normalization import sec_fact
from a_share_claw.harness.sec_facts import native_requirement
from a_share_claw.harness.contracts import RunRequest,RunStatus,digest
from a_share_claw.harness.trace import TraceRepository
from test_data_plugins import requirement
from test_harness import environment
from test_sec_filing_metadata import ACCESSION,submissions,company_facts
from test_source_core_handoff import clock,Clock,DAY,CAPTURE,planned,fetch_all,source_plan,specification,bindings,reply
from test_harness_quant import quant_packet,outlook_review


def selector(start="2026-01-01",end="2026-06-30"):
    return {"cik":"0000320193","concept":"us-gaap:Revenues","unit":"USD","period_start":start,
        "period_end":end,"filed":"2026-07-13","accession":ACCESSION}


class NativeTransport:
    def __init__(self):self.calls=[];self.mismatch=False;self.second_capture=False;self.instant=False
    def get(self,url,params=None,headers=None):
        self.calls.append((url,headers));obj=submissions() if '/submissions/' in url else company_facts()
        if '/submissions/' in url:
            rows=obj['filings']['recent'];rows['filingDate'][0]='2026-07-13';rows['acceptanceDateTime'][0]='2026-07-13T18:00:00Z'
            if self.mismatch:rows['form'][0]='10-K'
        else:
            for row in obj['facts']['us-gaap']['Revenues']['units']['USD']:
                row['filed']='2026-07-13'
                if self.instant:row.pop('start',None)
            if self.instant:obj['facts']['us-gaap']['Assets']=obj['facts']['us-gaap'].pop('Revenues')
        return json.dumps(obj).encode()


def plan():
    return {'framework':'Synthetic native filing facts before acquisition','requirements':[
        requirement('sec','company.facts_snapshot',{'cik':'320193','concepts':['us-gaap:Revenues']},day=DAY,rid='sec-facts').json(),
        requirement('sec','company.filing_metadata_snapshot',{'cik':'320193','accession':ACCESSION},day=DAY,rid='sec-filing').json()]}


def bind(sel=None):return [{'fact_id':'sec.revenue','requirement_id':'sec-facts','metadata_requirement_id':'sec-filing','selector':sel or selector()}]


def spec(sel=None):
    return {'subject':'Synthetic native company disclosure','technical_required':False,'debate_required':False,'debate_reason':'',
        'required_facts':[native_requirement(sel or selector(),'sec.revenue')],
        'questions':[{'question_id':r,'question':'Interpret the fixed native disclosure.','role':r,'required_fact_ids':['sec.revenue']} for r in ('jia_zhi','ping_heng')]}


def prepared(tmp,scope,*,source_plan=None,research_spec=None,bindings=None,transport=None,cutoff=None):
    transport=transport or NativeTransport();run=DataRun({'sec':SEC({'SEC_USER_AGENT':'synthetic contact@example.com'},transport)},tmp,scope)
    run.plan_core_research(source_plan or plan(),research_spec or spec(),bindings or bind(),cutoff_timestamp=cutoff or DAY+'T01:00:00Z')
    return run,transport


def company_reply(payload):
    e=payload.json();qs=[q for q in e['plan']['parameters']['research_spec']['questions'] if q['role']==e['role']]
    return {'role':e['role'],'phase':e['phase'],'packet_id':e['packet_id'],'packet_version':e['packet_version'],
        'answers':[{'question_id':q['question_id'],'fact_ids':q['required_fact_ids'],'inference':'Conditional synthetic disclosure only.'} for q in qs],
        'responds_to':[],'unknowns':[],'monitoring_triggers':[{'condition':'Review a new dated disclosure.','fact_ids':['sec.revenue']}] if e['role']=='ping_heng' else []}


def test_current_native_sec_to_core_company_dual_report(environment,clock):
    storage,scope,engine,tmp=environment;run,t=prepared(tmp/'source',scope);fetch_all(run);env=run.core_research_evidence()
    assert run.core_research_evidence()==env and len(t.calls)==2 and not run.summary()['official_output_allowed']
    f=env['data']['facts'][0];m=f['availability']['sec_filing']
    assert f['value']==30 and f['unit']=='USD' and f['data_period']=='2026-01-01/2026-06-30'
    assert f['publication_date']=='2026-07-13' and f['available_at']==CAPTURE.isoformat()
    assert m['acceptance_timestamp_raw']=='2026-07-13T18:00:00Z' and not m['public_dissemination_certified']
    seen=[]
    def runner(payload):seen.append(payload.json());return company_reply(payload)
    out=engine.run(RunRequest(scope,'Synthetic native financial fact',DAY,'research','company'),{'as_of_date':DAY,'facts':[env]},
        research_spec=spec(),role_runner=runner,semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    result=json.loads(out.output);assert seen[0]['packet']['facts']['sec.revenue']['availability']['sec_filing']==m
    assert '不认证公开传播时间' in Path(result['report_markdown']['path']).read_text()
    assert result['data']['confirmed_facts'][0]['availability']['sec_filing']==m
    assert out.action=='NO_ACTION' and not out.official_output_allowed and TraceRepository(storage).read_state(scope,'company') is None


def test_sec_comparative_period_is_not_replaced_by_filing_report_period(environment,clock):
    _,scope,_,tmp=environment;sel=selector(end='2026-03-31');run,_=prepared(tmp,scope,research_spec=spec(sel),bindings=bind(sel));fetch_all(run)
    f=run.core_research_evidence()['data']['facts'][0]
    assert f['value']==12 and f['observation_date']=='2026-03-31' and f['availability']['sec_filing']['report_date']=='2026-06-30'


def test_native_instantaneous_fact_has_no_invented_duration(environment,clock):
    _,scope,_,tmp=environment;t=NativeTransport();t.instant=True;sel=selector(start=None);sel['concept']='us-gaap:Assets'
    p=plan();p['requirements'][0]['params']['concepts']=['us-gaap:Assets']
    run,_=prepared(tmp,scope,source_plan=p,research_spec=spec(sel),bindings=bind(sel),transport=t);fetch_all(run);f=run.core_research_evidence()['data']['facts'][0]
    assert f['data_period']=='2026-06-30' and f['availability']['sec_filing']['period_start'] is None


def test_joint_outlook_includes_same_run_sec_current_facts(environment,clock):
    _,scope,engine,tmp=environment;base,_=planned(tmp/'base',scope);providers=dict(base.providers);providers['sec']=SEC({'SEC_USER_AGENT':'synthetic contact@example.com'},NativeTransport())
    ospec=specification();ospec['research_spec']['required_facts']+=spec()['required_facts']
    ospec['research_spec']['questions'][0]['required_fact_ids'].append('sec.revenue')
    p=source_plan();p['requirements']+=plan()['requirements'];run=DataRun(providers,tmp/'sources',scope)
    run.plan_core_outlook(p,ospec,bindings()+bind());fetch_all(run);env=run.core_macro_evidence()
    packet=quant_packet(scope);packet['facts'].append(env)
    out=engine.run(RunRequest(scope,'Synthetic native joint outlook',DAY,'research','outlook'),packet,outlook_spec=ospec,
        role_runner=reply,semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert next(f for f in json.loads(out.output)['data']['confirmed_facts'] if f['fact_id']=='sec.revenue')['value']==30


@pytest.mark.parametrize('field,value',[('entity','AAPL'),('metric','revenue_yoy'),('unit','million USD'),('data_period','2026-Q2')])
def test_sec_core_native_semantics_frozen_before_fetch(environment,clock,field,value):
    _,scope,_,tmp=environment;s=spec();s['required_facts'][0][field]=value
    with pytest.raises(DataError):prepared(tmp,scope,research_spec=s)
    assert not list(tmp.rglob('*.raw'))


@pytest.mark.parametrize('change',['legacy_facts','legacy_metadata','missing_metadata','wrong_accession','different_cik','historical_date'])
def test_source_bindings_cannot_substitute_legacy_or_other_filing(environment,clock,change):
    _,scope,_,tmp=environment;p=plan();b=bind()
    if change=='legacy_facts':p['requirements'][0]['capability']='company.facts'
    elif change=='legacy_metadata':p['requirements'][1]['capability']='company.filing_metadata'
    elif change=='missing_metadata':del b[0]['metadata_requirement_id']
    elif change=='wrong_accession':p['requirements'][1]['params']['accession']='0000950170-26-000124'
    elif change=='different_cik':p['requirements'][1]['params']['cik']='123'
    else:p['requirements'][1]['as_of_date']='2026-07-13'
    with pytest.raises(DataError):prepared(tmp,scope,source_plan=p,bindings=b)


@pytest.mark.parametrize('capability',['company.facts_snapshot','company.filing_metadata_snapshot'])
def test_sec_current_capabilities_reject_old_capture_before_network(tmp_path,clock,capability):
    t=NativeTransport();r=DataRun({'sec':SEC({'SEC_USER_AGENT':'synthetic contact@example.com'},t)},tmp_path,'legacy')
    params={'cik':'320193','concepts':['us-gaap:Revenues']} if capability=='company.facts_snapshot' else {'cik':'320193','accession':ACCESSION}
    req=requirement('sec',capability,params,day='2026-07-13');r.plan({'framework':'Synthetic old capture','requirements':[req.json()]})
    assert asyncio.run(r.fetch('input'))['error_code']=='historical_unavailable' and not t.calls


@pytest.mark.parametrize('target',['raw','result','contract'])
def test_handoff_rechecks_both_source_archives_and_contract(environment,clock,target):
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run)
    p=run.directory/run.results['sec-filing']['provenance']['artifact'] if target=='raw' else run.directory/'results/sec-filing.json' if target=='result' else run.directory/'core-contract.json'
    p.write_text('{}')
    with pytest.raises(DataError) as e:run.core_research_evidence()
    assert e.value.code=='hash_mismatch'


def test_source_pair_capture_uses_later_time_and_refuses_cross_midnight(environment,clock):
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run);meta=copy.deepcopy(run.results['sec-filing']);fact=run.results['sec-facts']
    meta['provenance']['retrieved_at']=DAY+'T00:30:00Z'
    assert sec_fact(fact,metadata=meta,**selector())['observation']['available_at']==DAY+'T00:30:00+00:00'
    meta['provenance']['retrieved_at']='2026-07-14T17:00:00Z'
    with pytest.raises(DataError):sec_fact(fact,metadata=meta,**selector())


@pytest.mark.parametrize('change',['invent_dissemination','backdate_capture','unit_alias','acceptance','changed_spec'])
def test_core_rechecks_sec_capture_and_filing_semantics_before_roles(environment,clock,change):
    _,scope,engine,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run);env=run.core_research_evidence();f=env['data']['facts'][0];s=spec()
    if change=='invent_dissemination':f['availability']['sec_filing']['public_dissemination_certified']=True
    elif change=='backdate_capture':f['available_at']='2026-07-13T18:00:00Z'
    elif change=='unit_alias':f['availability']['sec_filing']['unit']='USD/shares'
    elif change=='acceptance':f['availability']['sec_filing']['acceptance_timestamp_declared']='2026-07-13T19:00:00Z'
    else:s['questions'][0]['question']='A changed frozen specification'
    env['provenance']['sha256']=digest(env['data'])
    out=engine.run(RunRequest(scope,'Synthetic changed filing',DAY,'research','company'),{'as_of_date':DAY,'facts':[env]},research_spec=s,
        role_runner=lambda _:pytest.fail('Invalid SEC facts reached role'),semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status!=RunStatus.SUCCEEDED and not out.official_output_allowed


def test_capture_after_frozen_research_cutoff_blocks_handoff(environment,clock):
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope,cutoff='2026-07-13T23:30:00Z');fetch_all(run)
    with pytest.raises(DataError):run.core_research_evidence()


def test_all_five_sources_share_frozen_outlook_run_and_core_computations(environment,clock,monkeypatch):
    from test_fred_core_handoff import NativeTransport as FredTransport,fred_spec,fred_plan,fred_bindings
    from test_tdx_price_handoff import IndexTransport,index_spec,price_plan,price_bindings
    from a_share_claw.data_plugins.tdx import EasyTDX
    from a_share_claw.data_plugins.providers import FRED
    from a_share_claw.harness.outlook import derived_requirements
    monkeypatch.setattr('a_share_claw.data_plugins.tdx.datetime',Clock)
    storage,scope,engine,tmp=environment;base,_=planned(tmp/'base',scope);ps=dict(base.providers)
    ps['fred']=FRED({'FRED_API_KEY':'synthetic-key'},FredTransport());ps['easytdx']=EasyTDX({},IndexTransport())
    ps['sec']=SEC({'SEC_USER_AGENT':'synthetic contact@example.com'},NativeTransport())
    ospec=fred_spec();ospec['quant_spec']=index_spec()
    ospec['research_spec']['required_facts']=[f for f in ospec['research_spec']['required_facts'] if not f['fact_id'].startswith('price.')]+derived_requirements(index_spec())+spec()['required_facts']
    next(q for q in ospec['research_spec']['questions'] if q['question_id']=='market_comparison')['required_fact_ids']=[f['fact_id'] for f in derived_requirements(index_spec())]
    ospec['research_spec']['questions'][0]['required_fact_ids'].append('sec.revenue')
    p=fred_plan();p['requirements']+=plan()['requirements']+price_plan()['requirements'];run=DataRun(ps,tmp/'sources',scope)
    run.plan_core_outlook(p,ospec,fred_bindings()+bind(),price_bindings=price_bindings());fetch_all(run)
    assert set(run.providers)=={'nbs','pbc','fred','sec','easytdx'} and run.summary()['required_data_complete']
    packet={'as_of_date':DAY,'facts':[run.core_macro_evidence(),run.core_price_evidence()]}
    out=engine.run(RunRequest(scope,'Synthetic five-source implemented handoff',DAY,'research','outlook'),packet,outlook_spec=ospec,
        role_runner=reply,semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    facts={f['fact_id']:f for f in json.loads(out.output)['data']['confirmed_facts']}
    assert facts['sec.revenue']['value']==30 and facts['pce.yoy']['value']==pytest.approx(20)
    assert len(json.loads(out.output)['data']['market_statistics']['metrics'])==3
    assert out.action=='NO_ACTION' and not out.official_output_allowed and TraceRepository(storage).read_state(scope,'macro') is None


@pytest.mark.parametrize('change',['missing_provenance','later_available','different_run','different_selection'])
def test_core_rejects_missing_or_mismatched_sec_capture_provenance(environment,clock,change):
    _,scope,engine,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run);env=run.core_research_evidence();f=env['data']['facts'][0]
    if change=='missing_provenance':del env['provenance']['source_selections']
    elif change=='later_available':f['available_at']=DAY+'T01:30:00Z'
    elif change=='different_run':f['availability']['source_run_id']='0'*32
    else:f['availability']['selection_hash']='0'*64
    env['provenance']['sha256']=digest(env['data'])
    out=engine.run(RunRequest(scope,'Synthetic mismatched SEC provenance',DAY,'research','company'),{'as_of_date':DAY,'facts':[env]},research_spec=spec(),
        role_runner=lambda _:pytest.fail('Invalid provenance reached role'),semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status!=RunStatus.SUCCEEDED and not out.official_output_allowed
