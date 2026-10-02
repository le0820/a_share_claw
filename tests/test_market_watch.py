"""Four requested index identities from native source to chart delivery; synthetic only."""
import copy
import hashlib
import json
from datetime import datetime,timezone

import pytest

from a_share_claw.data_plugins.adapter import PluginEvidenceAdapter
from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.tdx import EasyTDX, INDEXES
from a_share_claw.data_plugins.watch import watch_source_contract
from a_share_claw.harness.contracts import RunRequest,RunStatus
from a_share_claw.harness.delivery import read_report
from a_share_claw.harness.market_watch import freeze_watch, WATCH_INDEXES
from a_share_claw.harness.trace import TraceRepository
from test_harness import environment
from test_source_core_handoff import clock,Clock,DAY


@pytest.fixture(autouse=True)
def native_clock(clock,monkeypatch):monkeypatch.setattr('a_share_claw.data_plugins.tdx.datetime',Clock)


def calendars(tmp):
    tmp.mkdir(exist_ok=True)
    raw=b'Synthetic reviewed exchange metadata, not official data'
    (tmp/'source.txt').write_bytes(raw)
    result={}
    for ex in ('XNYS','XNAS','XSHG','XSHE'):
        zone='America/New_York' if ex in {'XNYS','XNAS'} else 'Asia/Shanghai'
        rules={'schema_version':'exchange-calendar-rules-v1','exchange':ex,'market_timezone':zone,
            'coverage_start':'2026-07-09','coverage_end':'2026-07-13','regular_close':'16:00' if zone=='America/New_York' else '15:00',
            'closed_dates':[],'special_closes':{},'source_documents':[{'url':'https://synthetic.invalid/'+ex,
            'source_file':'source.txt','sha256':hashlib.sha256(raw).hexdigest(),'publication_date':None,'retrieved_at':'2026-07-14T00:00:00Z'}],
            'review_status':'host_reviewed','reviewed_at':'2026-07-14T00:00:01Z'}
        p=tmp/(ex+'.json');p.write_text(json.dumps(rules));result[ex]=p
    return result


def spec(tmp):
    return freeze_watch(DAY,'2026-07-10','2026-07-13',DAY+'T01:00:00Z',calendars(tmp),
        reference=datetime(2026,7,14,2,tzinfo=timezone.utc))['quant_spec']


class WatchTransport:
    def __init__(self):self.calls=[];self.omit=None;self.wrong_name=None
    def request(self,p):
        self.calls.append(p)
        native=next(n for s,n in INDEXES.items() if s in WATCH_INDEXES and n['code']==p['code'])
        return json.dumps({'sdk_version':'1.20.4','endpoint':'tcp://116.205.135.205:7727' if p['kind']=='international' else 'tcp://121.36.248.138:7709',
            'identity':[{'market':native['market'],'code':p['code'],'name':self.wrong_name or native['native_names'][0]}],
            'bars':[{'trade_date':day,'open':v,'close':v,'high':v+1,'low':v-1} for day,v in
                [('2026-07-09',100),('2026-07-10',110),('2026-07-13',105)] if day!=self.omit]}).encode()


def test_four_native_source_series_core_statistics_and_html(environment):
    storage,scope,engine,tmp=environment;s=spec(tmp/'calendars');t=WatchTransport()
    adapter=PluginEvidenceAdapter(tmp/'sources',watch_source_contract(s,DAY),providers={'easytdx':EasyTDX({},t)})
    out=engine.run(RunRequest(scope,'Synthetic S&P500 Nasdaq100 CSI300 ChiNext',DAY,'research','quant'),
        quant_spec=s,evidence_adapter=adapter,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    delivery=read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)
    assert set(delivery['report']['data']['chart_series'])==set(WATCH_INDEXES)
    assert len(t.calls)==4 and len(TraceRepository(storage).read(out.run_id,scope)['tool_calls'])==4
    for symbol in WATCH_INDEXES:assert symbol in delivery['html']
    assert 'COMP.NASDAQ' not in delivery['html'] and 'QQQ.US' not in delivery['html']
    assert not delivery['official_output_allowed'] and delivery['action']=='NO_ACTION'
    assert all(v['input_hash'] for v in delivery['report']['data']['chart_series'].values())


@pytest.mark.parametrize('change',['wrong_native_code','wrong_native_name','missing_session'])
def test_ambiguous_identity_or_incomplete_calendar_cannot_draw_complete_four_index_report(environment,change):
    storage,scope,engine,tmp=environment;s=spec(tmp/'calendars');t=WatchTransport();contract=watch_source_contract(s,DAY)
    if change=='wrong_native_code':contract['source_plan']['requirements'][0]['params']['provider_code']='A_NDX'
    elif change=='wrong_native_name':t.wrong_name='NASDAQ Composite'
    else:t.omit='2026-07-10'
    adapter=PluginEvidenceAdapter(tmp/'sources',contract,providers={'easytdx':EasyTDX({},t)})
    out=engine.run(RunRequest(scope,'Synthetic forbidden index substitution',DAY,'research','quant'),quant_spec=s,
        evidence_adapter=adapter,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status!=RunStatus.SUCCEEDED
    assert 'report_html' not in json.loads(out.output)
    if change=='wrong_native_code':assert not t.calls
    assert TraceRepository(storage).read_state(scope,'quant') is None


def test_calendar_identity_and_source_binding_precede_acquisition(tmp_path):
    files=calendars(tmp_path);files['XNYS']=files['XNAS']
    with pytest.raises(ValueError,match='watch_calendar_identity_mismatch'):
        freeze_watch(DAY,'2026-07-10','2026-07-13',DAY+'T01:00:00Z',files,reference=datetime(2026,7,14,2,tzinfo=timezone.utc))
    s=spec(tmp_path/'other');s['assets'][0]['name']='NASDAQ Composite'
    with pytest.raises(DataError):watch_source_contract(s,DAY)


def test_unrequested_sdk_history_is_not_admitted_or_used_to_block_the_frozen_window(tmp_path):
    from a_share_claw.data_plugins.core import Requirement
    s=spec(tmp_path);requirement=Requirement.parse(watch_source_contract(s,DAY)['source_plan']['requirements'][1])
    transport=WatchTransport();original=transport.request
    def raw_with_unrelated_bad_ohlc(params):
        obj=json.loads(original(params));obj['bars'].append({'trade_date':'2025-05-08','open':100,'high':110,'low':105,'close':108})
        return json.dumps(obj).encode()
    transport.request=raw_with_unrelated_bad_ohlc
    payload=EasyTDX({},transport).fetch(requirement)
    assert len(payload.data['bars'])==3 and all(v['trade_date'].startswith('2026-07') for v in payload.data['bars'])
    assert '2025-05-08' in payload.raw.decode()  # retained for source diagnostics
    transport.omit=None
    def selected_bad_ohlc(params):
        obj=json.loads(original(params));obj['bars'][1]['low']=111
        return json.dumps(obj).encode()
    transport.request=selected_bad_ohlc
    with pytest.raises(DataError,match='bounds'):EasyTDX({},transport).fetch(requirement)
