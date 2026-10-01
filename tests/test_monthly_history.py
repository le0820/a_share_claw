"""Regressions after live monthly-source boundary acceptance."""
import asyncio
import copy
import io
from datetime import datetime

import pytest
from openpyxl import Workbook

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.bea import BEA,METRICS,native_requirement
from a_share_claw.data_plugins.core import DataError
from a_share_claw.harness.contracts import RunRequest,RunStatus
from a_share_claw.harness.monthly_history import compare,checked_history
from a_share_claw.harness.research import catalog
from test_bea_plugin import clock,DAY,CAPTURE,spec
from test_data_plugins import FakeTransport,requirement
from test_harness import environment
from test_harness_quant import quant_packet,outlook_review
from test_source_core_handoff import reply


def workbook(change=None):
    w=Workbook();s=w.active;s.title='PIOhist_M'
    s.append([None,None,None,None,None,None,datetime(2026,7,13),None]);s.append(['June 2026 Personal Income and Outlays']);s.append(['Historical Comparisons']);s.append([])
    s.append([None,datetime(2026,6,1),'Last period with equal value',None,'Last period with larger value',None,'Last period with smaller value',None]);s.append(['Chain-type price indexes']);s.append(['Percent change from preceding month:'])
    s.append(['PCE',0.3,'---','---','---','---','2026M05',0.1]);s.append(['PCE, excluding food and energy',0.2,'---','---','---','---','2026M05',0.1])
    s.append(['Percent change from month one year ago:']);s.append(['PCE',3.4,'2026M05',3.4,'---','---','---','---']);s.append(['PCE, excluding food and energy',3.0,'2026M05',3.0,'---','---','---','---'])
    if change:change(s)
    b=io.BytesIO();w.save(b);return b.getvalue()


def historical_spec():
    x=spec();r=x['research_spec'];r['monthly_history']=[]
    for metric in METRICS:
        r['required_facts'].append(native_requirement(metric,2026,5,metric+'.prior'))
        r['monthly_history'].append({'comparison_id':metric,'basis':'reported_mom_rate' if '_mom_' in metric else 'reported_yoy_rate','fact_ids':[metric+'.prior',metric]})
    for q in r['questions']:
        if q['question_id']!='market_comparison':q['required_fact_ids']=[f['fact_id'] for f in r['required_facts'] if not f['fact_id'].startswith('price.')]
    return x


def prepare(tmp,scope,raw=None):
    t=FakeTransport(workbook() if raw is None else raw);run=DataRun({'bea':BEA({},t)},tmp,scope)
    plan={'framework':'Same-published-version sparse historical PCE comparisons','requirements':[requirement('bea','macro.pce_history_snapshot',{'url':'https://www.bea.gov/sites/default/files/2026-07/pi0626-hist.xlsx','year':2026,'month':6},day=DAY,rid='bea').json()]}
    bindings=[{'fact_id':metric+('.prior' if month==5 else ''),'requirement_id':'bea','selector':{'metric':metric,'year':2026,'month':month,'period_kind':'month'}} for metric in METRICS for month in (5,6)]
    run.plan_core_outlook(plan,historical_spec(),bindings);return run,t


def test_history_enters_tool_free_roles_and_both_reports(environment,clock):
    _,scope,engine,tmp=environment;run,_=prepare(tmp/'sources',scope)
    assert asyncio.run(run.fetch('bea'))['ok'];env=run.core_macro_evidence();packet=quant_packet(scope);packet['facts'].append(env)
    seen=[]
    def roles(payload):
        e=payload.json();h=e['packet']['monthly_history'];seen.append(h)
        assert len(h['comparisons'])==4 and all(c['comparison_scope']=='same_published_version' for c in h['comparisons'])
        assert all(not c['persistent_trend_certified'] for c in h['comparisons'])
        x=reply(payload)
        if x['role']=='ping_heng':x['monitoring_triggers'][0]['fact_ids']=[METRICS[0]]
        return x
    out=engine.run(RunRequest(scope,'Compare monthly facts with historical packs',DAY,'research','outlook'),packet,outlook_spec=historical_spec(),role_runner=roles,semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=CAPTURE.tzinfo))
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert out.action=='NO_ACTION' and not out.official_output_allowed and len(seen)==2
    assert list(tmp.rglob('monthly_history.json')) and '月度历史事实对照' in next(tmp.rglob('report.md')).read_text()


@pytest.mark.parametrize('mutate',[
    lambda s:s.__setitem__('monthly_history',s['monthly_history'][:-1]),
    lambda s:s['monthly_history'][0].__setitem__('basis','reported_yoy_rate'),
    lambda s:s['monthly_history'][0]['fact_ids'].reverse(),
    lambda s:s['required_facts'][-4].__setitem__('entity','CN'),
    lambda s:s['required_facts'][-4].__setitem__('data_period','2026-04-01/2026-04-30'),
    lambda s:s['questions'][0]['required_fact_ids'].remove(METRICS[0]+'.prior'),
])
def test_missing_history_wrong_basis_country_gap_and_role_omission_block_before_acquisition(mutate):
    s=historical_spec()['research_spec'];mutate(s)
    with pytest.raises(ValueError):checked_history(s)


@pytest.mark.parametrize('mutate,code',[
    (lambda s:s.__setitem__('A2','May 2026 Personal Income and Outlays'),'invalid_schema'),
    (lambda s:s.__setitem__('C5','Last available month'),'invalid_schema'),
    (lambda s:s.__setitem__('G8','2026M08'),'future_data'),
    (lambda s:s.__setitem__('H8',0.8),'source_disagreement'),
])
def test_native_workbook_identity_future_and_label_conflicts_fail_closed(environment,clock,mutate,code):
    _,scope,_,tmp=environment;run,_=prepare(tmp,scope,workbook(mutate))
    result=asyncio.run(run.fetch('bea'));assert not result['ok'] and result['error_code']==code


def test_history_versions_fallback_and_sparse_gap_are_explicit(environment,clock):
    _,scope,_,tmp=environment;run,_=prepare(tmp,scope);asyncio.run(run.fetch('bea'));env=run.core_macro_evidence()
    facts=catalog({'schema_version':'research-facts-v2','facts':env['data']['facts']},DAY);s=historical_spec()['research_spec']
    facts[METRICS[0]+'.prior']['source_file']='other-published-version'
    h=compare(s,facts)['comparisons'][0];assert h['comparison_scope']=='separate_published_versions' and not h['persistent_trend_certified']
    facts[METRICS[0]+'.prior']['fallback_status']='stale'
    with pytest.raises(ValueError,match='unverified_evidence'):compare(s,facts)
    with pytest.raises(DataError,match='exact monthly identity'):run.select('bea',{'metric':METRICS[0],'year':2026,'month':4,'period_kind':'month'})
    raw=next(tmp.rglob('*.raw'));raw.write_bytes(b'changed')
    with pytest.raises(DataError) as error:run.core_macro_evidence()
    assert error.value.code=='hash_mismatch'
