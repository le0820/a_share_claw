"""Synthetic SW-shaped 31-sector contract fixtures; never real classification or market data."""
import copy,json
from datetime import datetime,timezone
import pytest
from a_share_claw.harness.contracts import RunRequest,RunStatus,digest
from a_share_claw.harness.quant import checked_quant_spec
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.harness.delivery import read_report
from a_share_claw.data_plugins.adapter import PluginEvidenceAdapter
from test_harness import environment
from test_harness_quant import envelope,AS_OF


def specification():
    assets=[];industries=[]
    for i in range(31):
        symbol=f'801{i+1:03d}.SW';name=f'Synthetic sector {i+1}'
        industries.append({'industry_code':f'{100000+i}','index_symbol':symbol,'name':name})
        assets.append({'symbol':symbol,'name':name,'unit':'index_points','currency':'CNY','adjustment':'NONE','market_timezone':'Asia/Shanghai','calendar_source':'fixture:synthetic_shared_calendar','anchor':{'trade_date':'2026-07-02','close_at':'2026-07-02T15:00:00+08:00'},'sessions':[{'trade_date':d,'close_at':d+'T15:00:00+08:00'} for d in ['2026-07-03','2026-07-06','2026-07-10']]})
    return {'schema_version':'quant-spec-v2','operation':'price_statistics','frequency':'daily','window_start':'2026-07-03','window_end':'2026-07-10','cutoff_timestamp':'2026-07-13T02:00:00Z','assets':assets,'benchmark':None,'metrics':['period_return','max_drawdown'],'annualization_factor':252,'rotation':{'schema_version':'sw-rotation-v1','classification_version':'SW2021','level':1,'market_scope':['SH','SZ'],'industries':industries,'windows':[{'week':'2026-W27','start_date':'2026-07-03','end_date':'2026-07-03'},{'week':'2026-W28','start_date':'2026-07-06','end_date':'2026-07-10'}],'ranking':'competition_12_decimal'}}


def packet(scope,s):
    series=[]
    for i,a in enumerate(s['assets']):
        prices=[100,110,110,99] if i<2 else [100,100,100,99+i]
        series.append({**{k:a[k] for k in ('symbol','name','unit','currency','adjustment','market_timezone')},'frequency':'daily','source':'Synthetic prices','source_file':'fixture:'+a['symbol'],'source_timestamp':s['cutoff_timestamp'],'publication_date':'2026-07-13','rows':[{'trade_date':session['trade_date'],'available_at':session['close_at'],'close':price} for session,price in zip([a['anchor'],*a['sessions']],prices)]})
    c={'schema_version':'industry-classification-v1',**{k:s['rotation'][k] for k in ('classification_version','level','market_scope','industries')},'effective_date':'2021-11-30','publication_date':'2021-11-30','publisher':'申万宏源研究','review_status':'host_reviewed','source_file':'fixture:Synthetic classification only','document_sha256':'a'*64}
    facts=[envelope(scope,'price_history',{'schema_version':'price-series-v1','series':series}),envelope(scope,'industry_classification',c)]
    for fact in facts:fact['provenance'].update(source_timestamp=s['cutoff_timestamp'],observation_date=s['window_end'],data_period=s['window_start']+'/'+s['window_end'])
    facts[1]['provenance'].update(rotation_spec_hash=digest(s['rotation']),source_file=c['source_file'],publication_date=c['publication_date'],observation_date=c['publication_date'],data_period='SW2021 effective '+c['effective_date'])
    return {'as_of_date':AS_OF,'facts':facts}


def test_complete_sw_shaped_universe_has_real_rank_transition_ties_and_bound_html(environment):
    storage,scope,engine,tmp=environment;s=specification();p=packet(scope,s)
    out=engine.run(RunRequest(scope,'Synthetic weekly rotation acceptance, not real SW data',AS_OF,'research','quant'),quant_spec=s,packet=p,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    delivery=read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root);rotation=delivery['report']['data']['industry_rotation']
    by_symbol={r['index_symbol']:r for r in rotation['industries']}
    assert len(by_symbol)==31 and rotation['input_hash']==digest(p['facts'][0]['data'])
    for symbol in ('801001.SW','801002.SW'):
        a,b=by_symbol[symbol]['observations'];assert a['rank']==1 and b['rank']==30 and b['rank_change']==-29
        assert a['rank_change'] is None and abs(b['period_return']+.1)<1e-12
    assert by_symbol['801031.SW']['observations'][-1]['rank']==1
    assert by_symbol['801031.SW']['observations'][-1]['rank_change']==2
    assert '<svg' in delivery['html'] and '31行业原生收盘图与数值' in delivery['html'] and '全部周收益与排名原始数值' in delivery['html']
    assert '月线辅助视角' in delivery['html'] and '部分月份（窗口内）' in delivery['html']
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert any(r['stage']=='react_action' and r['detail']['operation']=='sw_level1_rotation' for r in trace['run_steps'])
    assert not out.official_output_allowed and out.action=='NO_ACTION' and not TraceRepository(storage).read_state(scope,'quant')


def test_monthly_auxiliary_uses_prior_month_end_and_marks_partial_edges():
    from decimal import Decimal
    from a_share_claw.harness.rotation import compute_rotation
    s=specification();s.update(window_start='2026-07-27',window_end='2026-08-07',cutoff_timestamp='2026-08-10T01:00:00Z')
    s['rotation']['windows']=[{'week':'2026-W31','start_date':'2026-07-27','end_date':'2026-07-31'},
                              {'week':'2026-W32','start_date':'2026-08-03','end_date':'2026-08-07'}]
    dates=['2026-07-27','2026-07-31','2026-08-03','2026-08-07']
    for a in s['assets']:
        a['anchor']={'trade_date':'2026-07-24','close_at':'2026-07-24T15:00:00+08:00'}
        a['sessions']=[{'trade_date':d,'close_at':d+'T15:00:00+08:00'} for d in dates]
    prices={'schema_version':'price-series-v1','series':[
        {'symbol':a['symbol'],'rows':[{'trade_date':d,'close':v} for d,v in zip(['2026-07-24',*dates],[100,105,110,115,121])]} for a in s['assets']]}
    class Scope: key='synthetic'
    classification=packet(Scope(),s)['facts'][1]['data']
    output=compute_rotation(checked_quant_spec(s),prices,classification,'2026-08-10')
    for sector in output['industries']:
        july,august=sector['monthly_context']
        assert abs(july['period_return']-float(Decimal(110)/Decimal(100)-1))<1e-12
        assert abs(august['period_return']-float(Decimal(121)/Decimal(110)-1))<1e-12
        assert august['anchor_date']=='2026-07-31' and july['anchor_date']=='2026-07-24'
        assert july['rank']==august['rank']==1 and august['rank_change']==0
        assert all(v['coverage']=='window_segment' and not v['period_complete'] for v in (july,august))


@pytest.mark.parametrize('change',['missing_sector','classification_name','classification_version','unreviewed_classification','missing_week_price','future_classification','effective_after_window','duplicate_index','cross_scope','wrong_spec_hash','mixed_calendar','gap_window'])
def test_rotation_cannot_draw_from_incomplete_mixed_or_unreviewed_inputs(environment,change):
    storage,scope,engine,tmp=environment;s=specification();p=packet(scope,s)
    if change=='missing_sector':p['facts'][0]['data']['series'].pop()
    elif change=='classification_name':p['facts'][1]['data']['industries']=copy.deepcopy(p['facts'][1]['data']['industries']);p['facts'][1]['data']['industries'][0]['name']='Other'
    elif change=='classification_version':p['facts'][1]['data']['classification_version']='SW2014'
    elif change=='unreviewed_classification':p['facts'][1]['data']['review_status']='unverified'
    elif change=='missing_week_price':p['facts'][0]['data']['series'][0]['rows'].pop()
    elif change=='future_classification':p['facts'][1]['data']['effective_date']='2026-07-15'
    elif change=='effective_after_window':p['facts'][1]['data']['effective_date']='2026-07-04'
    elif change=='duplicate_index':s['rotation']['industries'][0]['index_symbol']=s['rotation']['industries'][1]['index_symbol']
    elif change=='cross_scope':p['facts'][1]['scope_key']='other'
    elif change=='wrong_spec_hash':p['facts'][1]['provenance']['rotation_spec_hash']='0'*64
    elif change=='mixed_calendar':s['assets'][0]['sessions'][0]['close_at']='2026-07-03T15:01:00+08:00'
    else:s['rotation']['windows'][1]['start_date']='2026-07-07'
    for f in p['facts']:f['provenance']['sha256']=digest(f['data'])
    out=engine.run(RunRequest(scope,'Synthetic rejected rotation',AS_OF,'research','quant'),quant_spec=s,packet=p,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.BLOCKED,out.output
    assert 'report_html' not in json.loads(out.output) and (engine.artifact_root/scope.key/out.run_id/'run.html').exists()


def test_missing_classification_stops_before_price_requests_and_retains_gap_html(environment):
    storage,scope,engine,tmp=environment;s=specification();adapter=PluginEvidenceAdapter(tmp/'sources',{'source_plan':{'framework':'Synthetic missing SW binding','requirements':[]},'bindings':[],'price_bindings':[],'cutoff_timestamp':s['cutoff_timestamp']},providers={})
    out=engine.run(RunRequest(scope,'Synthetic no classification provider',AS_OF,'research','quant'),quant_spec=s,evidence_adapter=adapter,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)['error_code']=='rotation_source_binding_missing'
    assert not TraceRepository(storage).read(out.run_id,scope)['tool_calls']


def test_rotation_research_cannot_be_promoted_to_official_state(environment):
    storage,scope,engine,tmp=environment;s=specification()
    out=engine.run(RunRequest(scope,'Synthetic forbidden official rotation',AS_OF,'official','quant'),quant_spec=s,packet=packet(scope,s),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)['error_code']=='rotation_official_not_supported'
    assert not TraceRepository(storage).read_state(scope,'quant')
