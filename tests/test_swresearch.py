"""Synthetic native HTTP fixtures; these do not certify live SW identity or coverage."""
import asyncio,copy,json
from datetime import datetime,timezone
import pytest
from a_share_claw.data_plugins import DataRun,default_registry
from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.swresearch import SWResearch
from a_share_claw.data_plugins.adapter import PluginEvidenceAdapter
from a_share_claw.harness.contracts import RunRequest,RunStatus
from test_data_plugins import FakeTransport,requirement
from test_source_core_handoff import Clock,clock,DAY
from test_rotation import specification,packet
from test_harness import environment

@pytest.fixture(autouse=True)
def source_clock(clock,monkeypatch):
    monkeypatch.setattr('a_share_claw.data_plugins.swresearch.datetime',Clock)

def rows(code='801001'):
    return [{'swindexcode':code,'bargaindate':d,'openindex':v,'maxindex':v+1,'minindex':v-1,'closeindex':v}
        for d,v in [('2026-07-02',100),('2026-07-03',110),('2026-07-06',110),('2026-07-10',99)]]

def req():return requirement('swresearch','market.sw_index_daily_snapshot',{'symbol':'801001.SW','start_date':'2026-07-02','end_date':'2026-07-10'},day=DAY)

def test_source_is_registered_but_only_explicitly_enabled():
    registry=default_registry()
    assert 'swresearch' not in registry.snapshot({})
    assert set(registry.snapshot({'ASCLAW_DATA_PROVIDERS':'swresearch'}))=={'swresearch'}

def test_catalog_requires_31_but_cannot_certify_classification(tmp_path):
    t=FakeTransport({'data':{'count':31,'results':[{'synthetic':i} for i in range(31)]}})
    provider=SWResearch({},t)
    result=provider.fetch(requirement('swresearch','industry.sw2021_level1_metadata',{},day=DAY))
    assert result.availability=='unverified' and not result.data['classification_version_certified']
    assert t.calls[0][1]['indextype']=='一级行业'

class DocumentTransport:
    def __init__(self, raw=b'%PDF-1.7\nsynthetic fixture only'):
        self.raw=raw;self.calls=[]
    def get(self,url,params=None,headers=None):
        self.calls.append(url);return self.raw

def document_req(url='https://wxweb.swsresearch.com/swsreport/2021_08/328340.pdf'):
    return requirement('swresearch','industry.publisher_document',
                       {'url':url,'purpose':'classification_standard'},day=DAY)

def test_publisher_document_preserves_original_bytes_without_date_or_review_promotion(tmp_path):
    import hashlib
    transport=DocumentTransport();run=DataRun({'swresearch':SWResearch({},transport)},tmp_path,'synthetic-document')
    run.plan({'framework':'Synthetic explicit classification document for host review, no prices',
              'requirements':[document_req().json()]})
    result=asyncio.run(run.fetch('input'))
    assert result['status']=='unverified' and result['fallback_status']=='unverified'
    data=result['data'];assert data['review_status']=='unreviewed'
    assert data['publication_date'] is data['classification_effective_date'] is data['index_effective_date'] is None
    assert not data['classification_version_certified']
    assert data['document_sha256']==result['provenance']['sha256']==hashlib.sha256(transport.raw).hexdigest()
    assert (run.directory/result['provenance']['artifact']).read_bytes()==transport.raw
    assert len(transport.calls)==1

@pytest.mark.parametrize('url',[
    'http://wxweb.swsresearch.com/swsreport/2021_08/328340.pdf',
    'https://wxweb.swsresearch.com.evil.test/swsreport/2021_08/328340.pdf',
    'https://user@wxweb.swsresearch.com/swsreport/2021_08/328340.pdf',
    'https://wxweb.swsresearch.com:8443/swsreport/2021_08/328340.pdf',
    'https://wxweb.swsresearch.com/swsreport/2021_08/328340.pdf?token=x',
    'https://wxweb.swsresearch.com/swsreport/2021_08/328340.pdf#classification',
    'https://wxweb.swsresearch.com/swsreport/2021_13/328340.pdf',
    'https://wxweb.swsresearch.com/swsreport/2021_08/../328340.pdf'])
def test_publisher_document_rejects_unfrozen_source_before_network(url):
    transport=DocumentTransport()
    with pytest.raises(DataError):SWResearch({},transport).fetch(document_req(url))
    assert transport.calls==[]

@pytest.mark.parametrize('raw',[b'<html>Access denied</html>',b'',b'%PDF-1.7'+b'x'*20_000_000])
def test_publisher_document_rejects_access_notice_and_oversize(raw):
    with pytest.raises(DataError):SWResearch({},DocumentTransport(raw)).fetch(document_req())

@pytest.mark.parametrize('change',['code','duplicate','zero','nan','bad_ohlc','missing_anchor','missing_last'])
def test_native_window_rejects_invalid_history(change):
    data=rows()
    if change=='code':data[0]['swindexcode']='801999'
    elif change=='duplicate':data.append(data[-1])
    elif change=='zero':data[-1]['closeindex']=0
    elif change=='nan':data[-1]['closeindex']='NaN'
    elif change=='bad_ohlc':data[-1]['maxindex']=1
    elif change=='missing_anchor':data.pop(0)
    else:data.pop()
    with pytest.raises((DataError,ValueError)):
        SWResearch({},FakeTransport({'data':data})).fetch(req())

def test_current_daily_window_retains_native_raw_and_no_pit(tmp_path):
    t=FakeTransport({'data':rows()});run=DataRun({'swresearch':SWResearch({},t)},tmp_path,'synthetic')
    run.plan({'framework':'Synthetic dated daily window','requirements':[req().json()]})
    result=asyncio.run(run.fetch('input'))
    assert result['status']=='ok' and result['fallback_status']=='none'
    assert not result['data']['historical_vintage_certified']
    assert json.loads((run.directory/result['provenance']['artifact']).read_text())=={'data':rows()}
    assert t.calls[0][1]=={'swindexcode':'801001','period':'DAY'}
    historical=requirement('swresearch',req().capability,dict(req().params),day='2026-07-13')
    with pytest.raises(DataError,match='today'):SWResearch({},t).fetch(historical)
    assert len(t.calls)==1

class HistoryTransport:
    def __init__(self):self.calls=[];self.omit=False
    def get(self,url,params=None,headers=None):
        self.calls.append((url,params));values=rows(params['swindexcode'])
        if self.omit:values.pop(2)
        return json.dumps({'data':values}).encode()

def config(spec):
    requirements=[requirement('swresearch','market.sw_index_daily_snapshot',{'symbol':a['symbol'],
        'start_date':a['anchor']['trade_date'],'end_date':a['sessions'][-1]['trade_date']},day=DAY,rid='sw'+str(i)).json() for i,a in enumerate(spec['assets'])]
    return {'source_plan':{'framework':'Synthetic SW31 reviewed classification then exact daily snapshots','requirements':requirements},
        'bindings':[],'price_bindings':[{'symbol':r['params']['symbol'],'requirement_id':r['requirement_id']} for r in requirements],
        'cutoff_timestamp':spec['cutoff_timestamp']}

@pytest.mark.parametrize('missing_session',[False,True])
def test_reviewed_existing_classification_and_native_prices_reach_core(environment,missing_session):
    storage,scope,engine,tmp=environment;spec=specification();spec['cutoff_timestamp']=DAY+'T01:00:00Z';existing=packet(scope,spec);existing['facts'].pop(0)
    t=HistoryTransport();t.omit=missing_session
    adapter=PluginEvidenceAdapter(tmp/'sources',config(spec),providers={'swresearch':SWResearch({},t)})
    out=engine.run(RunRequest(scope,'Synthetic SW official endpoint binding',DAY,'research','quant'),
        quant_spec=spec,packet=existing,evidence_adapter=adapter,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==(RunStatus.BLOCKED if missing_session else RunStatus.SUCCEEDED),out.output
    assert len(t.calls)==31
    if not missing_session:
        data=json.loads(out.output)['data']['industry_rotation']
        assert data['classification_basis']=='synthetic_fixture'
        assert data['industries'][0]['daily_context'][0]['anchor_date']=='2026-07-02'
    assert out.action=='NO_ACTION' and not out.official_output_allowed


def test_classification_effective_after_window_stops_before_any_http(environment):
    storage,scope,engine,tmp=environment;spec=specification();spec['cutoff_timestamp']=DAY+'T01:00:00Z'
    existing=packet(scope,spec);existing['facts'].pop(0)
    from a_share_claw.harness.contracts import digest
    fact=existing['facts'][0];fact['data']['effective_date']='2026-07-04';fact['provenance']['sha256']=digest(fact['data'])
    t=HistoryTransport();adapter=PluginEvidenceAdapter(tmp/'sources',config(spec),providers={'swresearch':SWResearch({},t)})
    out=engine.run(RunRequest(scope,'Synthetic future classification guard',DAY,'research','quant'),
        quant_spec=spec,packet=existing,evidence_adapter=adapter,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)['error_code']=='future_data'
    assert not t.calls
