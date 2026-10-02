"""Scope-bound native SW closes; classification is separately reviewed existing evidence."""
import json
from zoneinfo import ZoneInfo
from ..harness.contracts import canonical, digest
from ..harness.quant import timestamp
from .core import DataError
from .normalization import checked_result


def price_evidence(run, contract):
    spec=contract['specification']['quant_spec'];assets={a['symbol']:a for a in spec['assets']}
    series=[];sources=[]
    for binding in contract['price_bindings']:
        result=run._archived_result(binding['requirement_id'])
        data=checked_result(result,'swresearch','market.sw_index_daily_snapshot')
        source=result['provenance'];capture=timestamp(source['retrieved_at']);asset=assets[binding['symbol']]
        if (data['symbol']!=asset['symbol'] or data['native_code']!=asset['symbol'][:-3] or data['unit']!='index_points' or
                data['snapshot_as_of_date']!=contract['as_of_date'] or data['historical_vintage_certified'] is not False or
                data['availability_basis']!='observed_current_snapshot' or
                capture.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()!=contract['as_of_date'] or
                capture>timestamp(spec['cutoff_timestamp'])):
            raise DataError('future_data','Native Shenwan current capture must fit the frozen date and cutoff')
        sessions=[asset['anchor'],*asset['sessions']];rows={r['trade_date']:r for r in data['bars']}
        if len(rows)!=len(data['bars']) or set(rows)!={s['trade_date'] for s in sessions}:
            raise DataError('insufficient_coverage','Every frozen session is required; no carrying prices or adding sessions')
        if any(timestamp(s['close_at'])>capture for s in sessions):
            raise DataError('price_before_close','Capture precedes a required session close')
        series.append({'symbol':asset['symbol'],**{k:asset[k] for k in ('name','unit','currency','adjustment','market_timezone')},
            'frequency':'daily','source':'Shenwan official current captured native daily index',
            'source_file':str(run.directory/source['artifact']),'source_timestamp':capture.isoformat(),
            'publication_date':contract['as_of_date'],
            'current_snapshot':{'basis':'observed_current_snapshot','snapshot_as_of_date':contract['as_of_date'],
                'historical_vintage_certified':False,'source_run_id':run.run_id,'raw_sha256':source['sha256'],
                'provider':'swresearch','adapter_version':source['version'],'raw_format':'native_http_json',
                'native_identity':{'code':data['native_code'],'name':asset['name'],'publisher':'申万宏源研究'}},
            'rows':[{'trade_date':s['trade_date'],'close':rows[s['trade_date']]['close'],'available_at':capture.isoformat()} for s in sessions]})
        sources.append({'symbol':asset['symbol'],'requirement_id':binding['requirement_id'],'input_hash':digest(result),**source})
    data={'schema_version':'price-series-v3','series':series};path=run.directory/'core-price-evidence.json'
    envelope={'capability':'price_history','scope_key':run.core_scope_key,'data':data,'fallback_status':'none',
        'provenance':{'source':'planned Shenwan official daily snapshots','source_file':str(path),'sha256':digest(data),
            'source_timestamp':max((s['source_timestamp'] for s in series),key=timestamp),'publication_date':contract['as_of_date'],
            'publication_date_basis':'aggregate_snapshot_capture_date_not_original_release',
            'observation_date':max(a['sessions'][-1]['trade_date'] for a in assets.values()),
            'data_period':spec['window_start']+'/'+spec['window_end'],'source_run_id':run.run_id,
            'source_contract_hash':digest(contract),'quant_spec_hash':digest(spec),'source_selections':sources}}
    if path.exists():
        if json.loads(path.read_text())!=envelope:raise DataError('hash_mismatch','Pinned Shenwan evidence cannot change')
    else:run._save(path.name,envelope)
    return envelope
