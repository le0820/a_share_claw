"""Frozen whole-market native main-order estimates; no trading or capital-account inference."""
from __future__ import annotations
import json,math,re
from datetime import datetime
from zoneinfo import ZoneInfo
from .contracts import canonical,digest,validate_date

BASIS='provider_defined_main_order_net_estimate'
MARKETS={'SH':1,'SZ':0,'BJ':2}


def checked_flow_spec(spec):
    from .quant import timestamp
    keys={'schema_version','operation','observation_date','cutoff_timestamp','close_at','calendar_source','measurement_basis','unit','universe','native_update_policy'}
    if not isinstance(spec,dict):raise ValueError('invalid_flow_spec')
    v2=spec.get('schema_version')=='flow-spec-v2'
    if set(spec)!=(keys|{'markets'} if v2 else keys) or spec.get('schema_version') not in {'flow-spec-v1','flow-spec-v2'} or spec['operation']!='fund_flow_snapshot' or spec['measurement_basis']!=BASIS or spec['unit']!='CNY':raise ValueError('invalid_flow_spec')
    declared=spec.get('markets',['SH','SZ','BJ'])
    if declared not in (['SH','SZ'],['SH','SZ','BJ']):raise ValueError('invalid_flow_spec')
    day=validate_date(spec['observation_date']);close=timestamp(spec['close_at']);cutoff=timestamp(spec['cutoff_timestamp'])
    if spec['native_update_policy'] not in {'all_rows_post_close','retain_unfinalized_native_zero'}:raise ValueError('invalid_flow_spec')
    if close.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()!=day or close>cutoff or not isinstance(spec['calendar_source'],str) or not spec['calendar_source'].strip():raise ValueError('invalid_flow_spec')
    universe=spec['universe']
    if not isinstance(universe,list) or not len(declared)<=len(universe)<=10000:raise ValueError('invalid_flow_spec')
    seen=set();markets=set()
    for row in universe:
        if not isinstance(row,dict) or set(row)!={'market','code','name'} or row['market'] not in MARKETS or not isinstance(row['code'],str) or not re.fullmatch(r'[0-9]{6}',row['code']) or not isinstance(row['name'],str) or not row['name'].strip():raise ValueError('invalid_flow_spec')
        key=(row['market'],row['code'])
        if key in seen:raise ValueError('invalid_flow_spec')
        seen.add(key);markets.add(row['market'])
    if markets!=set(declared):raise ValueError('insufficient_coverage')
    return json.loads(canonical(spec))


def compute_flows(spec,data,reference,as_of_date):
    from .quant import timestamp
    spec=checked_flow_spec(spec);cutoff=timestamp(spec['cutoff_timestamp'])
    if cutoff>reference or cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()>as_of_date or spec['observation_date']>as_of_date:raise ValueError('future_data')
    if not isinstance(data,dict) or set(data)!={'schema_version','measurement_basis','unit','observation_date','source_timestamp','source_file','rows'} or data['schema_version']!='flow-observations-v1' or any(data[k]!=spec[k] for k in ('measurement_basis','unit','observation_date')):raise ValueError('flow_contract_mismatch')
    if not isinstance(data['rows'],list):raise ValueError('flow_contract_mismatch')
    capture=timestamp(data['source_timestamp'])
    if capture>cutoff or capture<timestamp(spec['close_at']) or not isinstance(data['source_file'],str) or not data['source_file']:raise ValueError('future_data')
    markets=spec.get('markets',list(MARKETS))
    expected={(r['market'],r['code']):r for r in spec['universe']};received={};amounts={m:[] for m in markets};nets={m:[] for m in markets};unfinalized={m:[] for m in markets}
    for row in data['rows']:
        if not isinstance(row,dict) or set(row)!={'market','code','name','observation_date','amount','main_order_net_amount','native_updated_at'}:raise ValueError('flow_contract_mismatch')
        key=(row['market'],row['code'])
        if key not in expected or key in received or row['name']!=expected[key]['name'] or row['observation_date']!=spec['observation_date']:raise ValueError('flow_contract_mismatch')
        updated=timestamp(row['native_updated_at'])
        if updated>capture or updated.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()!=spec['observation_date']:raise ValueError('flow_contract_mismatch')
        for field in ('amount','main_order_net_amount'):
            if type(row[field]) not in (int,float) or not math.isfinite(row[field]):raise ValueError('flow_contract_mismatch')
        if row['amount']<0 or abs(row['main_order_net_amount'])>row['amount']+.01:raise ValueError('flow_contract_mismatch')
        if updated<timestamp(spec['close_at']):
            if spec['native_update_policy']!='retain_unfinalized_native_zero' or row['amount']!=0 or row['main_order_net_amount']!=0:raise ValueError('flow_contract_mismatch')
            unfinalized[row['market']].append({k:row[k] for k in ('code','name','native_updated_at')})
        received[key]=row;amounts[row['market']].append(row['amount']);nets[row['market']].append(row['main_order_net_amount'])
    if received.keys()!=expected.keys():raise ValueError('insufficient_coverage')
    metrics={m:{'native_main_order_net_amount':math.fsum(nets[m]),'turnover_amount':math.fsum(amounts[m])} for m in markets}
    metrics['ALL']={k:math.fsum(v[k] for v in metrics.values()) for k in ('native_main_order_net_amount','turnover_amount')}
    audit={m:{'instruments':len(nets[m]),'coverage_status':'complete_against_frozen_universe_with_unfinalized_zeros' if unfinalized[m] else 'complete_against_frozen_universe','unfinalized_native_zero_quotes':unfinalized[m],'observation_date':spec['observation_date'],'source_file':data['source_file']} for m in markets}
    zero_markets=[m for m in markets if all(v==0 for v in nets[m]) and any(v>0 for v in amounts[m])]
    for m in markets:audit[m]['uniform_zero_net_with_positive_turnover']=m in zero_markets
    return {'schema_version':'flow-output-v1','specification':spec,'metrics':metrics,'metric_units':{'native_main_order_net_amount':'CNY','turnover_amount':'CNY'},'series_audit':audit,
        'flow_chart':{'unit':'CNY','observation_date':spec['observation_date'],'input_hash':digest(data),'values':[{ 'market':m,'value':v['native_main_order_net_amount']} for m,v in metrics.items()]},
        'limitations':['图表为供应商定义的主力净额估计；核心只聚合已准入原生金额，未按成交规模重算。','主力分类阈值未经独立认证；净额不代表投资者总净现金流、北向资金或融资融券。','当前捕获不认证历史可用版本；本报告不构成交易动作，不进入日评分、L2或仓位规则。']+(['含 '+str(sum(map(len,unfinalized.values())))+' 条收盘前更新的原生零金额，逐条列入审计；保留供应商快照零值，不推断停牌、未上市或全天无交易。全市场汇总仅为当前捕获快照，未认证最终收盘成交完整性。'] if any(unfinalized.values()) else []),
        'unknowns':['主力分类阈值与逐笔重算一致性未认证。','申万2021一级归属尚未准入，本报告只呈现全市场及交易所汇总。']+([','.join(zero_markets)+' 全部原生主力净额为零但有成交额；字段支持与分类覆盖待确认。保留原生报价，不认定真实买卖平衡；ALL同受此限制。'] if zero_markets else [])}
