"""Exact native flow handoff under a core-frozen three-exchange universe."""
from __future__ import annotations
import asyncio,json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from ..harness.acquisition import EvidenceBatch
from ..harness.contracts import Scope,canonical,digest
from ..harness.flows import checked_flow_spec,BASIS,MARKETS
from ..harness.quant import timestamp
from .core import DataRun,Requirement,DataError


def flow_source_contract(spec,as_of_date):
    s=checked_flow_spec(spec)
    return {'source_plan':{'framework':'Acquire only complete frozen three-exchange native main-order estimates; no scoring or trading' if s['schema_version']=='flow-spec-v1' else 'Acquire only the explicitly frozen exchange universe native main-order estimates; no scoring or trading','requirements':[{'requirement_id':'flows','provider':'easytdx','capability':'market.a_share_flow_snapshot','as_of_date':as_of_date,'params':{'markets':s.get('markets',['SH','SZ','BJ']),'max_rows':10000,'observation_date':s['observation_date']},'required':True}]},
        'bindings':[{'requirement_id':'flows','capability':'fund_flow_snapshot','measurement_basis':BASIS}],'price_bindings':[],'cutoff_timestamp':s['cutoff_timestamp']}


def prepare_flow(adapter,request):
    e=request.json();plan=e['plan'];scope=Scope(**e['scope']);spec=checked_flow_spec(plan['parameters']['quant_spec']);c=json.loads(adapter._configuration)
    if plan['workflow']!='quant' or plan['mode']!='research' or scope.key!=plan['scope_key'] or e['needed_capabilities']!=['fund_flow_snapshot'] or c!=flow_source_contract(spec,plan['as_of_date']):raise ValueError('source_contract_mismatch')
    requirement=Requirement.parse(c['source_plan']['requirements'][0])
    if adapter.providers is not None:providers={k:v for k,v in adapter.providers.items() if k=='easytdx'}
    else:
        from . import default_registry
        settings=dict(adapter.settings);enabled={k.strip() for k in settings.get('ASCLAW_DATA_PROVIDERS','nbs,pbc,easytdx,bea,sec').split(',')}
        settings['ASCLAW_DATA_PROVIDERS']='easytdx' if 'easytdx' in enabled else ''
        providers=(adapter.registry or default_registry()).snapshot(settings)
    run=DataRun(providers,adapter.artifact_root,scope);run.plan(c['source_plan'])
    run._save('core-flow-contract.json',{'core_run_id':e['core_run_id'],'plan_id':plan['plan_id'],'scope_key':scope.key,'quant_spec':spec,'source_contract':c})
    ticket={'schema_version':'evidence-batch-v1','core_run_id':e['core_run_id'],'plan_id':plan['plan_id'],'scope_key':scope.key,'as_of_date':plan['as_of_date'],'source_run_id':run.run_id,'requirements':[requirement.json()]}
    def fetch(call):
        if call.json()!={'core_run_id':e['core_run_id'],'source_run_id':run.run_id,'requirement_id':'flows'}:raise ValueError('source_contract_mismatch')
        return asyncio.run(run.fetch('flows'))
    def finish(call):
        if call.document!=request.document:raise ValueError('source_contract_mismatch')
        try:result=run._archived_result('flows')
        except DataError as exc:raise ValueError('insufficient_coverage:source_handoff.'+exc.code) from None
        if result['status']!='ok' or result['fallback_status']!='none' or result['truncated']:raise ValueError('unverified_evidence')
        p=result['provenance'];native=result['data'];capture=p['retrieved_at']
        if timestamp(capture)>timestamp(spec['cutoff_timestamp']):raise ValueError('future_data')
        if native['native_units']!={'amount':'CNY','main_net_amount':'CNY'} or native['observation_date']!=spec['observation_date'] or native['missing_fields']:raise ValueError('flow_contract_mismatch')
        raw_path=run.directory/p['artifact'];rows=[]
        for q in native['quotes']:
            f=q['fields'];rows.append({'market':q['requested_market'],'code':q['code'],'name':q['name'],'observation_date':datetime.strptime(str(f['server_update_date']),'%Y%m%d').date().isoformat(),'amount':f['amount'],'main_order_net_amount':f['main_net_amount'],'native_updated_at':datetime.strptime(str(f['server_update_date'])+f"{f['server_update_time']:06d}",'%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat()})
        expected={(r['market'],r['code']):r['name'] for r in spec['universe']}
        if {(r['market'],r['code']):r['name'] for r in rows}!=expected or len(rows)!=len(expected):raise ValueError('insufficient_coverage')
        data={'schema_version':'flow-observations-v1','measurement_basis':BASIS,'unit':'CNY','observation_date':spec['observation_date'],'source_timestamp':capture,'source_file':str(raw_path),'rows':rows}
        fact={'capability':'fund_flow_snapshot','scope_key':scope.key,'data':data,'fallback_status':'none','provenance':{'source':'easytdx native main-order estimate','source_file':str(raw_path),'source_timestamp':capture,'publication_date':plan['as_of_date'],'date_basis':'snapshot_capture_date_not_original_release','observation_date':spec['observation_date'],'data_period':spec['observation_date'],'sha256':digest(data),'quant_spec_hash':digest(spec),'source_run_id':run.run_id,'raw_sha256':p['sha256'],'units':{'amount':'CNY','main_order_net_amount':'CNY'}}}
        fact['provenance']['publication_date_basis']=fact['provenance'].pop('date_basis')
        run._save('core-flow-evidence.json',fact)
        return {'as_of_date':plan['as_of_date'],'facts':json.loads(canonical(e['existing_packet']['facts']))+[fact]}
    return EvidenceBatch(canonical(ticket),fetch,finish)
