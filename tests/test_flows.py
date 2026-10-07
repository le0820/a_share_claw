"""Native whole-market money amounts -> admitted core sums -> signed report chart."""
import copy,json,math
from datetime import datetime,timezone
from pathlib import Path
import pytest
from a_share_claw.harness.contracts import RunRequest,RunStatus
from a_share_claw.harness.flows import BASIS,checked_flow_spec,compute_flows
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.harness.delivery import read_report
from a_share_claw.data_plugins.adapter import PluginEvidenceAdapter
from a_share_claw.data_plugins.flow_handoff import flow_source_contract
from a_share_claw.data_plugins.tdx import EasyTDX
from test_harness import environment
from test_source_core_handoff import clock,Clock,DAY
from test_tdx_market_discovery import Transport


@pytest.fixture(autouse=True)
def native_clock(clock,monkeypatch):monkeypatch.setattr('a_share_claw.data_plugins.tdx.datetime',Clock)


def spec():
    return {'schema_version':'flow-spec-v1','operation':'fund_flow_snapshot','native_update_policy':'all_rows_post_close','observation_date':'2026-07-13','cutoff_timestamp':DAY+'T01:00:00Z','close_at':'2026-07-13T15:00:00+08:00','calendar_source':'Synthetic host reviewed three-market close','measurement_basis':BASIS,'unit':'CNY',
        'universe':[{'market':m,'code':c,'name':'Synthetic'} for m,c in [('SH','600000'),('SZ','000001'),('BJ','920001')]]}


def adapter(tmp,s,t):return PluginEvidenceAdapter(tmp/'sources',flow_source_contract(s,DAY),providers={'easytdx':EasyTDX({},t)})


def test_all_three_exchange_native_values_are_summed_without_double_counting_or_sign_loss(environment):
    storage,scope,engine,tmp=environment;s=spec();t=Transport()
    t.obj['quotes'][0]['fields']['main_net_amount']=50;t.obj['quotes'][1]['fields']['main_net_amount']=-90
    out=engine.run(RunRequest(scope,'Synthetic complete market flow chart',DAY,'research','quant'),quant_spec=s,evidence_adapter=adapter(tmp,s,t),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    delivery=read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)
    data=delivery['report']['data'];assert data['metrics']['ALL']=={'native_main_order_net_amount':-60,'turnover_amount':300}
    assert data['metrics']['SH']['native_main_order_net_amount']==50 and data['metrics']['SZ']['native_main_order_net_amount']==-90
    assert '资金流向：供应商主力净额估计' in delivery['html'] and '<rect' in delivery['html'] and '原始金额（元）' in delivery['html']
    assert '5577' not in delivery['html'] and not out.official_output_allowed and out.action=='NO_ACTION'
    assert delivery['report']['source_table'][0]['observation_date']=='2026-07-13'
    assert delivery['report']['source_table'][0]['publication_date_basis']=='snapshot_capture_date_not_original_release'
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert len(trace['tool_calls'])==1 and any(r['stage']=='react_action' and r['detail']['operation']=='fund_flow_snapshot' for r in trace['run_steps'])
    assert 'fund_flow_snapshot' in json.loads(out.output)['plan']['required_capabilities']
    assert not TraceRepository(storage).read_state(scope,'quant')


@pytest.mark.parametrize('change',['missing_bj','missing_field','wrong_date','pre_close','swapped_name','amount_unit','over_net'])
def test_invalid_flow_never_emits_money_chart_or_official_state(environment,change):
    storage,scope,engine,tmp=environment;s=spec();t=Transport()
    if change=='missing_bj':t.obj['quotes'].pop()
    elif change=='missing_field':del t.obj['quotes'][0]['fields']['main_net_amount']
    elif change=='wrong_date':t.obj['quotes'][0]['fields']['server_update_date']=20260710
    elif change=='pre_close':t.obj['quotes'][0]['fields']['server_update_time']=143000
    elif change=='swapped_name':t.obj['quotes'][0]['name']='Other'
    elif change=='amount_unit':s['unit']='ten_thousand_CNY'
    else:t.obj['quotes'][0]['fields']['main_net_amount']=101
    if change=='amount_unit':
        with pytest.raises(ValueError):checked_flow_spec(s)
        return
    out=engine.run(RunRequest(scope,'Synthetic rejected money chart',DAY,'research','quant'),quant_spec=s,evidence_adapter=adapter(tmp,s,t),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status!=RunStatus.SUCCEEDED and 'report_html' not in json.loads(out.output)
    assert out.action=='NO_ACTION' and not TraceRepository(storage).read_state(scope,'quant')
    assert (engine.artifact_root/scope.key/out.run_id/'run.html').exists()


def test_flow_estimates_cannot_be_promoted_to_formal_scoring(environment):
    storage,scope,engine,tmp=environment;s=spec();t=Transport()
    good=engine.run(RunRequest(scope,'Synthetic research snapshot',DAY,'research','quant'),quant_spec=s,evidence_adapter=adapter(tmp,s,t),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    source=json.loads((engine.artifact_root/scope.key/good.run_id/'fund_flow_snapshot.json').read_text())
    out=engine.run(RunRequest(scope,'Synthetic forbidden promotion',DAY,'official','quant'),quant_spec=s,packet={'as_of_date':DAY,'facts':[source]},clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)['error_code']=='flow_official_not_supported'
    assert TraceRepository(storage).read_state(scope,'quant') is None


@pytest.mark.parametrize('policy,net,amount,allowed',[('all_rows_post_close',0,0,False),('retain_unfinalized_native_zero',0,0,True),('retain_unfinalized_native_zero',1,2,False),('retain_unfinalized_native_zero',0,2,False)])
def test_preclose_zero_retention_requires_explicit_policy_and_discloses_every_record(environment,policy,net,amount,allowed):
    storage,scope,engine,tmp=environment;s=spec();s['native_update_policy']=policy;t=Transport()
    t.obj['quotes'][0]['fields'].update(main_net_amount=net,amount=amount,server_update_time=1)
    out=engine.run(RunRequest(scope,'Synthetic unfinalized zero quote snapshot',DAY,'research','quant'),quant_spec=s,evidence_adapter=adapter(tmp,s,t),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert (out.status==RunStatus.SUCCEEDED)==allowed
    if allowed:
        delivery=read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)
        audit=delivery['report']['data']['series_audit']['SH']
        assert audit['unfinalized_native_zero_quotes']==[{'code':'600000','name':'Synthetic','native_updated_at':'2026-07-13T00:00:01+08:00'}]
        assert '未确认收盘更新的原生零值' in delivery['html'] and '未认证最终收盘成交完整性' in delivery['html']


def test_uniform_zero_net_on_positive_turnover_is_disclosed_as_uncertified_field_support(environment):
    storage,scope,engine,tmp=environment;s=spec();t=Transport();t.obj['quotes'][-1]['fields']['main_net_amount']=0
    out=engine.run(RunRequest(scope,'Synthetic unsupported-looking zero field',DAY,'research','quant'),quant_spec=s,evidence_adapter=adapter(tmp,s,t),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    delivery=read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)
    assert delivery['report']['data']['series_audit']['BJ']['uniform_zero_net_with_positive_turnover']
    assert '字段支持待确认，不代表真实买卖平衡，ALL同受此限制' in delivery['html']


def test_sh_sz_only_is_frozen_before_source_and_never_requires_or_aggregates_bj(environment):
    storage,scope,engine,tmp=environment;s=spec();s.update(schema_version='flow-spec-v2',markets=['SH','SZ'])
    s['universe']=[r for r in s['universe'] if r['market']!='BJ']
    t=Transport();t.obj['quotes']=t.obj['quotes'][:2];del t.obj['market_totals']['BJ'];del t.obj['page_counts']['BJ']
    calls=[];original=t.request
    def counted(p):calls.append(p);return original(p)
    t.request=counted
    out=engine.run(RunRequest(scope,'Synthetic SH SZ only market snapshot',DAY,'research','quant'),quant_spec=s,evidence_adapter=adapter(tmp,s,t),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert calls[0]['markets']==['SH','SZ']
    delivery=read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)
    assert set(delivery['report']['data']['metrics'])=={'SH','SZ','ALL'}
    assert delivery['report']['data']['metrics']['ALL']=={'native_main_order_net_amount':-40,'turnover_amount':200}
    assert 'ALL为沪深两市汇总' in delivery['html'] and '>BJ<' not in delivery['html']
    bad=copy.deepcopy(s);bad['universe'].append({'market':'BJ','code':'920001','name':'Synthetic'})
    with pytest.raises(ValueError,match='insufficient_coverage'):checked_flow_spec(bad)
