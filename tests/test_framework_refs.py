"""Complete host parameter references are resolved by code before review/acquisition."""
import copy,json
from datetime import date,timedelta
from unittest.mock import patch
import httpx,pytest
from a_share_claw.harness.contracts import RunRequest,RunStatus,canonical,digest
from a_share_claw.harness.framework import host_parameters_ref
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.sdk_research import SDKResearchAdapter
from test_harness import environment
from test_harness_hosts import host
from test_sdk_research import OfflineEndpoint,configured
from test_harness_framework import review
from test_harness_quant import quant_spec

DAY='2026-10-02'

def complete_constraints():
    spec=quant_spec();spec.update(window_start='2026-07-01',window_end='2026-09-30',cutoff_timestamp='2026-10-01T00:00:00Z')
    dates=[];day=date(2026,7,1)
    while day<=date(2026,9,30):
        if day.weekday()<5:dates.append(day.isoformat())
        day+=timedelta(days=1)
    for asset in spec['assets']:
        ending='T16:00:00-04:00' if asset['market_timezone']=='America/New_York' else 'T15:00:00+08:00'
        asset['anchor']={'trade_date':'2026-06-30','close_at':'2026-06-30'+ending}
        asset['sessions']=[{'trade_date':d,'close_at':d+ending} for d in dates]
    return {'quant_spec':spec}

def reply(payload):
    entry=payload.json()
    return {'framework':'Synthetic frozen quarter index framework: compare return, drawdown and risk; acquire facts before conclusions.',
        'parameters_ref':host_parameters_ref(entry['workflow'],entry['host_constraints'],entry['as_of_date'],entry['scope_key']),
        'unresolved_constraints':[]}

@pytest.mark.parametrize('change',['hash','scope','date','workflow','extra','partial'])
def test_bad_or_partial_reference_cannot_freeze_or_acquire(environment,change):
    storage,scope,engine,tmp=environment;constraints=complete_constraints();calls=[]
    def proposed(payload):
        value=reply(payload)
        if change=='hash':value['parameters_ref']['constraints_hash']='0'*64
        elif change=='scope':value['parameters_ref']['scope_key']='other'
        elif change=='date':value['parameters_ref']['as_of_date']='2026-10-01'
        elif change=='workflow':value['parameters_ref']['workflow']='industry'
        elif change=='extra':value['parameters']={}
        else:value['parameters_ref'].pop('constraints_hash')
        return value
    out=engine.run(RunRequest(scope,'Synthetic quarter reference guard',DAY,'research','quant'),
        planning_constraints=constraints,framework_proposer=proposed,framework_reviewer=lambda p:calls.append(p))
    assert out.status==RunStatus.BLOCKED and not calls
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert not trace['tool_calls'] and not any(r['stage']=='plan' for r in trace['run_steps'])
    assert (engine.artifact_root/scope.key/out.run_id/'run.html').exists()


def test_complete_reference_materializes_original_lists_and_pairs_span(environment):
    storage,scope,engine,tmp=environment;constraints=complete_constraints()
    out=engine.run(RunRequest(scope,'Synthetic quarter reference planning',DAY,'plan','quant'),
        planning_constraints=constraints,framework_proposer=reply,framework_reviewer=review)
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert json.loads(out.output)['plan']['parameters']==constraints
    actions=[r for r in TraceRepository(storage).read(out.run_id,scope)['run_steps'] if r['stage']=='react_action' and r['detail']['operation']=='resolve_host_parameters']
    assert [r['detail']['boundary'] for r in actions]==['start','end']
    assert actions[0]['detail']['span_id']==actions[1]['detail']['span_id']

class ReferenceEndpoint(OfflineEndpoint):
    def response(self,request):
        body=json.loads(request.content);entry=json.loads(body['messages'][-1]['content']);self.requests.append(entry)
        if 'candidate' in entry:
            result={'candidate_hash':entry['candidate_hash'],'passed':True,'findings':[],
                    'reviewer':'synthetic_reference_reviewer','version':'fixture-v1'}
        else:
            assert set(body['response_format']['json_schema']['schema']['properties'])=={'framework','parameters_ref','unresolved_constraints'}
            result={'framework':'Synthetic quarter price framework and visualization; admitted facts required before calculating results.',
                    'parameters_ref':entry['host_parameters_ref'],'unresolved_constraints':[]}
        return httpx.Response(200,json={'id':'synthetic-reference','object':'chat.completion','created':1,'model':'synthetic-sdk-model',
            'choices':[{'index':0,'message':{'role':'assistant','content':canonical(result)},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})

def test_real_sdk_transport_uses_calendar_metadata_and_core_keeps_full_contract(environment,host):
    storage,scope,engine,tmp=environment;config,*_=host;endpoint=ReferenceEndpoint();constraints=complete_constraints()
    with patch('a_share_claw.agent.build_model_client',side_effect=endpoint.client):
        out=engine.run(RunRequest(scope,'Synthetic full-quarter reference model transport',DAY,'plan','quant'),
            planning_constraints=constraints,framework_adapter=SDKResearchAdapter(configured(config)))
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert json.loads(out.output)['plan']['parameters']==constraints
    proposal,reviewed=endpoint.requests
    summary=proposal['host_constraints']['quant_spec']['assets'][0]['sessions']
    assert summary['count']==len(constraints['quant_spec']['assets'][0]['sessions'])
    assert summary['sha256']==digest(constraints['quant_spec']['assets'][0]['sessions'])
    assert reviewed['candidate']['parameters']['quant_spec']['assets'][0]['sessions']==summary
    trace=TraceRepository(storage).read(out.run_id,scope)
    transports=[r['detail'] for r in trace['run_steps'] if r['stage']=='framework_transport']
    assert len(transports)==2 and all(r['model_chars']<r['original_chars']*.6 for r in transports)
    assert len(trace['model_calls'])==2 and not trace['tool_calls'] and not TraceRepository(storage).read_state(scope,'quant')
