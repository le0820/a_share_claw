"""Explicit ordinary-chat source binding; synthetic model and source transport."""
import asyncio,json,copy
from unittest.mock import patch
import httpx,pytest
from a_share_claw.agent import InvestmentAgent
from a_share_claw.chat_host import TrustedChatProfile
from a_share_claw.harness.contracts import Scope,RunStatus,canonical
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.data_plugins.adapter import PluginEvidenceAdapter
from a_share_claw.data_plugins.tdx import EasyTDX
from a_share_claw.data_plugins.watch import watch_source_contract
from test_harness_hosts import host
from test_harness import ROOT
from test_market_watch import spec,WatchTransport,native_clock
from test_source_core_handoff import clock,DAY
from test_sdk_research import configured
from test_harness_framework import FrameworkEndpoint,proposal


def profile(config,context,tmp):
    quant=spec(tmp/'calendars')
    document={'schema_version':'trusted-chat-host-v1','scope':Scope.from_context(ROOT,context).__dict__,
        'as_of_date':DAY,'workflow':'quant','parameters':{'quant_spec':quant},'source_contract':watch_source_contract(quant,DAY)}
    return document,TrustedChatProfile.parse(document)


def endpoint_for(parameters):
    endpoint=FrameworkEndpoint();original=endpoint.response
    def response(request):
        entry=json.loads(json.loads(request.content)['messages'][-1]['content'])
        if 'candidate' in entry:return original(request)
        endpoint.requests.append(entry)
        return httpx.Response(200,json={'id':'synthetic-chat','object':'chat.completion','created':1,'model':'synthetic-sdk-model',
            'choices':[{'index':0,'message':{'role':'assistant','content':canonical(proposal(parameters))},'finish_reason':'stop'}],
            'usage':{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}})
    endpoint.response=response;return endpoint


def test_bound_ordinary_chat_freezes_before_four_sources_and_delivers_html(host,native_clock):
    config,storage,context,tmp=host;doc,binding=profile(config,context,tmp);transport=WatchTransport()
    adapter=PluginEvidenceAdapter(tmp/'sources',doc['source_contract'],providers={'easytdx':EasyTDX({},transport)})
    endpoint=endpoint_for(doc['parameters'])
    with patch('a_share_claw.agent.build_model_client',side_effect=endpoint.client),patch.object(TrustedChatProfile,'evidence_adapter',return_value=adapter):
        out=asyncio.run(InvestmentAgent(configured(config),storage,trusted_chat=binding).run_result(context,'分析冻结窗口的四指数表现'))
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert len(transport.calls)==4 and len(endpoint.requests)==2
    trace=TraceRepository(storage).read(out.run_id,Scope.from_context(ROOT,context))
    assert trace['request']['as_of_date']==DAY
    assert next(r['id'] for r in trace['run_steps'] if r['stage']=='plan') < min(r['id'] for r in trace['run_steps'] if r['stage']=='react_action' and r['detail']['kind']=='source')
    assert not out.official_output_allowed and out.action=='NO_ACTION'
    assert InvestmentAgent(config,storage).read_core_report(context,out.run_id,format='html').startswith('<!doctype html>')


@pytest.mark.parametrize('change',['principal','session','agent_key','date','workflow','constraints'])
def test_chat_binding_rejects_widening_before_models_or_source_construction(host,change):
    config,storage,context,tmp=host;doc,binding=profile(config,context,tmp);kwargs={}
    if change in {'principal','session','agent_key'}:
        doc['scope'][change]='other';binding=TrustedChatProfile.parse(doc)
    elif change=='date':kwargs['as_of_date']='2026-07-13'
    elif change=='workflow':kwargs['workflow']='industry'
    else:kwargs['planning_constraints']={}
    with patch('a_share_claw.agent.build_model_client',side_effect=AssertionError('no model allowed')),patch.object(TrustedChatProfile,'evidence_adapter',side_effect=AssertionError('no source allowed')):
        out=asyncio.run(InvestmentAgent(configured(config),storage,trusted_chat=binding).run_result(context,'synthetic request',**kwargs))
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)['completion']=='host_binding_rejected'
    trace=TraceRepository(storage).read(out.run_id,Scope.from_context(ROOT,context))
    assert not trace['model_calls'] and not trace['tool_calls'] and TraceRepository(storage).read_state(Scope.from_context(ROOT,context),'quant') is None


def test_model_cannot_change_protected_symbol_before_source_request(host,native_clock):
    config,storage,context,tmp=host;doc,binding=profile(config,context,tmp);transport=WatchTransport()
    adapter=PluginEvidenceAdapter(tmp/'sources',doc['source_contract'],providers={'easytdx':EasyTDX({},transport)})
    altered=copy.deepcopy(doc['parameters']);altered['quant_spec']['assets'][0]['symbol']='QQQ.US'
    endpoint=endpoint_for(altered)
    with patch('a_share_claw.agent.build_model_client',side_effect=endpoint.client),patch.object(TrustedChatProfile,'evidence_adapter',return_value=adapter):
        out=asyncio.run(InvestmentAgent(configured(config),storage,trusted_chat=binding).run_result(context,'synthetic protected parameters'))
    assert out.status!=RunStatus.SUCCEEDED and not transport.calls
    assert json.loads(out.output)['error_code']=='planning_constraint_changed'


def test_missing_bound_provider_returns_gap_without_fabricated_report(host,native_clock):
    config,storage,context,tmp=host;doc,binding=profile(config,context,tmp)
    adapter=PluginEvidenceAdapter(tmp/'sources',doc['source_contract'],providers={})
    endpoint=endpoint_for(doc['parameters'])
    with patch('a_share_claw.agent.build_model_client',side_effect=endpoint.client),patch.object(TrustedChatProfile,'evidence_adapter',return_value=adapter):
        out=asyncio.run(InvestmentAgent(configured(config),storage,trusted_chat=binding).run_result(context,'synthetic unavailable source'))
    assert out.status==RunStatus.BLOCKED,out.output
    assert out.action=='NO_ACTION' and 'report_html' not in json.loads(out.output)


@pytest.mark.parametrize('change_policy',[False,True])
def test_chat_native_flow_binding_preserves_frozen_freshness_policy_before_fetch(host,native_clock,change_policy):
    from test_flows import spec as flow_spec
    from test_tdx_market_discovery import Transport
    from a_share_claw.data_plugins.flow_handoff import flow_source_contract
    config,storage,context,tmp=host;quant=flow_spec()
    doc={'schema_version':'trusted-chat-host-v1','scope':Scope.from_context(ROOT,context).__dict__,
        'as_of_date':DAY,'workflow':'quant','parameters':{'quant_spec':quant},'source_contract':flow_source_contract(quant,DAY)}
    binding=TrustedChatProfile.parse(doc);transport=Transport();transport.calls=[]
    original=transport.request
    def counted(p):transport.calls.append(p);return original(p)
    transport.request=counted
    adapter=PluginEvidenceAdapter(tmp/'sources',doc['source_contract'],providers={'easytdx':EasyTDX({},transport)})
    parameters=copy.deepcopy(doc['parameters'])
    if change_policy:parameters['quant_spec']['native_update_policy']='retain_unfinalized_native_zero'
    endpoint=endpoint_for(parameters)
    with patch('a_share_claw.agent.build_model_client',side_effect=endpoint.client),patch.object(TrustedChatProfile,'evidence_adapter',return_value=adapter):
        out=asyncio.run(InvestmentAgent(configured(config),storage,trusted_chat=binding).run_result(context,'展示冻结口径的全市场主力净额估计'))
    if change_policy:
        assert out.status!=RunStatus.SUCCEEDED and not transport.calls
        assert json.loads(out.output)['error_code']=='planning_constraint_changed'
    else:
        assert out.status==RunStatus.SUCCEEDED,out.output
        assert len(transport.calls)==1 and len(endpoint.requests)==2
        assert '资金流向：供应商主力净额估计' in InvestmentAgent(config,storage).read_core_report(context,out.run_id,format='html')
