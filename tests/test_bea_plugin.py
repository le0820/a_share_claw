"""Synthetic regression checks after actual BEA source-shape acceptance."""
import asyncio
import copy
from datetime import datetime, timezone

import pytest

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.bea import BEA, METRICS, native_requirement
from a_share_claw.data_plugins.core import DataError
from a_share_claw.harness.contracts import RunRequest,RunStatus
from test_data_plugins import FakeTransport,requirement
from test_harness import environment
from test_harness_quant import quant_packet,outlook_review
from test_source_core_handoff import specification,reply

DAY='2026-07-14'
CAPTURE=datetime(2026,7,14,0,tzinfo=timezone.utc)
HTML=b'''<html><h1>News Release</h1><h1>Personal Income and Outlays, June 2026</h1>
<p>EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, Monday, July 13, 2026</p>
<p>From the preceding month, the PCE price index for June increased 0.3 percent. Excluding food and energy, the PCE price index increased 0.2 percent.</p>
<p>From the same month one year ago, the PCE price index for June increased 3.4 percent. Excluding food and energy, the PCE price index increased 3.0 percent from one year ago.</p></html>'''

class Clock(datetime):
    @classmethod
    def now(cls,tz=None):return CAPTURE.astimezone(tz) if tz else CAPTURE.replace(tzinfo=None)

@pytest.fixture
def clock(monkeypatch):
    import a_share_claw.data_plugins.core as core
    import a_share_claw.data_plugins.bea as bea
    monkeypatch.setattr(core,'datetime',Clock);monkeypatch.setattr(bea,'datetime',Clock)

def plan(day=DAY):
    return {'framework':'Synthetic reported PCE source contract, not an index calculation','requirements':[
        requirement('bea','macro.pce_release_snapshot',{'url':'https://www.bea.gov/news/2026/personal-income-and-outlays-june-2026','year':2026,'month':6},day=day,rid='bea').json()]}

def spec():
    s=specification();s['research_spec']['required_facts']=[f for f in s['research_spec']['required_facts'] if f['fact_id'].startswith('price.')]+[
        native_requirement(m,2026,6,m) for m in METRICS]
    for q in s['research_spec']['questions']:
        if q['question_id']!='market_comparison':q['required_fact_ids']=list(METRICS)
    return s

def bindings():
    return [{'fact_id':m,'requirement_id':'bea','selector':{'metric':m,'year':2026,'month':6,'period_kind':'month'}} for m in METRICS]

def run(tmp,scope,raw=HTML,specification=None):
    t=FakeTransport(raw);r=DataRun({'bea':BEA({},t)},tmp,scope)
    r.plan_core_outlook(plan(),specification or spec(),bindings())
    return r,t

def test_reported_bea_rates_enter_core_roles_and_dual_reports(environment,clock):
    _,scope,engine,tmp=environment;r,t=run(tmp/'source',scope)
    x=asyncio.run(r.fetch('bea'));assert x['status']=='ok'
    env=r.core_macro_evidence();assert len(t.calls)==1 and not r.summary()['official_output_allowed']
    facts=env['data']['facts'];assert [f['value'] for f in facts]==[0.3,0.2,3.4,3.0]
    assert all(f['entity']=='US' and f['source']=='bea' and f['unit']=='percent' for f in facts)
    assert all(f['publication_date']=='2026-07-13' and f['availability']['publisher_available_at']=='2026-07-13T08:30:00-04:00' and f['available_at']==CAPTURE.isoformat() for f in facts)
    p=quant_packet(scope);p['facts'].append(env)
    def roles(payload):
        x=reply(payload)
        if x['role']=='ping_heng':x['monitoring_triggers'][0]['fact_ids']=list(METRICS)
        return x
    result=engine.run(RunRequest(scope,'Synthetic reported PCE outlook',DAY,'research','outlook'),p,
        outlook_spec=spec(),role_runner=roles,semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert result.status==RunStatus.SUCCEEDED,result.output
    assert result.action=='NO_ACTION' and not result.official_output_allowed

@pytest.mark.parametrize('raw,code',[
 (HTML.replace(b'Outlays, June',b'Outlays, May'),'period_mismatch'),
 (HTML.replace(b'EDT, Monday',b'EST, Monday'),'invalid_schema'),
 (HTML.replace(b'Monday, July 13',b'Tuesday, July 14'),'future_data'),
 (HTML.replace(b'index for June',b'index for May'),'insufficient_coverage'),
 (HTML.replace(b'0.3 percent',b'0.3 basis points'),'insufficient_coverage'),
 (HTML.replace(b'Excluding food and energy',b'Including food and energy'),'insufficient_coverage'),
 (HTML.replace(b'</html>',b'<p>From the preceding month, the PCE price index for June increased 0.4 percent. Excluding food and energy, the PCE price index increased 0.2 percent.</p></html>'),'source_disagreement'),
])
def test_source_wrong_period_units_core_definition_clock_and_conflict_fail_closed(tmp_path,clock,raw,code):
    t=FakeTransport(raw);r=DataRun({'bea':BEA({},t)},tmp_path,'test');r.plan(plan());x=asyncio.run(r.fetch('bea'))
    assert x['error_code']==code and not x['ok']

def test_historical_and_unofficial_url_rejected_before_network(tmp_path,clock):
    t=FakeTransport(HTML);r=DataRun({'bea':BEA({},t)},tmp_path/'historical','test');r.plan(plan('2026-07-13'))
    assert asyncio.run(r.fetch('bea'))['error_code']=='historical_unavailable' and not t.calls
    p=plan();p['requirements'][0]['params']['url']='https://example.com/news/2026/personal-income-and-outlays-june-2026'
    r=DataRun({'bea':BEA({},t)},tmp_path/'host','test');r.plan(p)
    assert asyncio.run(r.fetch('bea'))['error_code']=='source_denied' and not t.calls

@pytest.mark.parametrize('change',[{'unit':'Index 2017=100'},{'metric':'pce_mom'},{'entity':'CN'},{'data_period':'2026-01-01/2026-06-30'}])
def test_binding_cannot_relabel_reported_percent_as_index_calculation_or_ytd(environment,clock,change):
    _,scope,_,tmp=environment;s=spec();s['research_spec']['required_facts'][-4].update(change)
    with pytest.raises((ValueError,DataError)):run(tmp,scope,specification=s)
    assert not list(tmp.rglob('*.raw'))

@pytest.mark.parametrize('target',['raw','result','contract'])
def test_bea_handoff_rejects_archive_tamper(environment,clock,target):
    _,scope,_,tmp=environment;r,_=run(tmp,scope);x=asyncio.run(r.fetch('bea'))
    if target=='raw':(r.directory/x['provenance']['artifact']).write_bytes(b'changed')
    elif target=='result':(r.directory/'results/bea.json').write_text('{}')
    else:(r.directory/'core-contract.json').write_text('{}')
    with pytest.raises(DataError) as error:r.core_macro_evidence()
    assert error.value.code=='hash_mismatch'
