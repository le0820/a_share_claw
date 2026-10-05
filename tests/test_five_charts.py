"""Pressure arithmetic, no-future native duration selection, and scoped view gates."""
import copy,json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
import pytest
from a_share_claw.harness.five_charts import score,pressure,missing_packet,validate_packet,render,CHARTS
from a_share_claw.harness.ai_pressure import native_ttm
from a_share_claw.data_plugins.five_chart_host import FiveChartHost
from a_share_claw.harness.contracts import RunRequest
from a_share_claw.harness.trace import TraceRepository
from test_harness import environment,DAY


def test_fixed_four_weights_missing_never_reweights():
 result=pressure({'demand':10,'cash_flow':20,'external_financing':30,'external_constraints':40})
 assert result['contributions']=={'demand':4,'cash_flow':6,'external_financing':7.5,'external_constraints':2}
 assert result['total']==19.5
 assert pressure({'demand':None,'cash_flow':20,'external_financing':30,'external_constraints':40})['total'] is None
 with pytest.raises(ValueError):pressure({'demand':10})
 with pytest.raises(ValueError):pressure({'demand':101,'cash_flow':20,'external_financing':30,'external_constraints':40})


def test_standardization_direction_warmup_dispersion_clipping():
 history=list(range(8))
 assert score(7,history)>50 and score(7,history,polarity=-1)<50
 assert score(10,history[:7]) is None and score(10,[1]*8) is None
 assert score(10**9,history)==100 and score(-10**9,history)==0
 assert score(10,[*range(100),*range(8)],window=8)==score(10,history,window=8)
 assert score(2,[0]*59,minimum=60,window=252) is None


def fact(start,end,val,filed):return {'start':start,'end':end,'val':val,'filed':filed,'form':'10-Q','accn':filed}

def test_native_ttm_comparable_period_zero_debt_future_revision_excluded():
 rows=[fact('2024-01-01','2024-12-31',100,'2025-02-01'),fact('2024-01-01','2024-06-30',40,'2024-08-01'),fact('2025-01-01','2025-06-30',60,'2025-08-01'),fact('2024-01-01','2024-06-30',999,'2026-02-01')]
 assert native_ttm(rows,'2025-07-31')['value']==100
 result=native_ttm(rows,'2025-08-02');assert result['value']==120
 assert all(x['filed']<='2025-08-02' for x in result['components'])
 assert native_ttm([fact('2024-01-01','2024-12-31',0,'2025-02-01')],'2025-02-02')['value']==0
 with pytest.raises(ValueError):native_ttm(rows[1:],'2025-08-02')


def test_scoped_packet_dates_sources_and_silent_gap_gates():
 p=missing_packet('scope','run',DAY);validate_packet(p,'scope','run',DAY)
 for args in [('other','run',DAY),('scope','other',DAY),('scope','run','2026-08-01')]:
  with pytest.raises(ValueError):validate_packet(p,*args)
 q=copy.deepcopy(p);q['gaps']=[]
 with pytest.raises(ValueError,match='silent_chart_gap'):validate_packet(q,'scope','run',DAY)
 p['sources']=[{'id':'native','sha256':'0'*64,'retrieved_at':p['generated_at']}]
 p['series']=[{'name':'通信','unit':'price_first_observation_100','basis':'native index','points':[{'date':'2025-01-02','value':100,'source_ids':['native']}]}]
 validate_packet(p,'scope','run',DAY)
 q=copy.deepcopy(p);q['series'][0]['points'][0]['date']='2029-01-01'
 with pytest.raises(ValueError):validate_packet(q,'scope','run',DAY)
 q=copy.deepcopy(p);q['series'][0]['points'][0]['source_ids']=['unbound']
 with pytest.raises(ValueError):validate_packet(q,'scope','run',DAY)
 q=copy.deepcopy(p);q['refresh_started_at']=(datetime.fromisoformat(q['generated_at'])+timedelta(seconds=1)).isoformat()
 with pytest.raises(ValueError):validate_packet(q,'scope','run',DAY)
 text=render(p);assert '<script' not in text
 assert all('id="'+cid+'"' in text for cid,_,_ in CHARTS)


def test_host_denies_generic_url_mutation_unbounded_and_cross_scope(tmp_path):
 for doc in [{'id':'bad','url':'https://example.com/a'}, {'id':'bad','url':'https://www.stats.gov.cn/sj/zxfb/202501/t20250101_123.html','method':'DELETE'}, {'id':'bad','url':'http://data.sec.gov/api/xbrl/companyfacts/CIK0001018724.json'}, {'id':'bad','kind':'tdx','symbol':'ETF.PROXY'}]:
  with pytest.raises(ValueError):FiveChartHost(scope_key='scope',as_of_date=DAY,archive_root=tmp_path,documents=[doc])
 host=FiveChartHost(scope_key='wrong',as_of_date=DAY,archive_root=tmp_path,documents=[])
 class Request:pass
 class Session:pass
 session=Session();session.request=Request();session.request.scope=type('Scope',(),{'key':'scope'})();session.request.as_of_date=DAY
 with pytest.raises(ValueError,match='scope_mismatch'):host.collect(session)


def test_each_run_generates_five_sections_and_new_packet_clock(environment):
 storage,scope,engine,_=environment;repo=TraceRepository(storage)
 outputs=[engine.run(RunRequest(scope,'five-chart diagnostic',DAY,'plan','macro')) for _ in range(2)]
 packets=[]
 for out in outputs:
  root=engine.artifact_root/scope.key/out.run_id
  packet=json.loads((root/'five_charts.json').read_text());packets.append(packet)
  assert packet['run_id']==out.run_id and packet['scope_key']==scope.key
  assert all('id="'+cid+'"' in (root/'run.html').read_text() for cid,_,_ in CHARTS)
  assert any(Path(a['detail']['path']).name=='five_charts.html' for a in repo.read(out.run_id,scope)['artifacts'])
 assert packets[0]['generated_at']!=packets[1]['generated_at']


def test_refresh_adapter_never_called_by_plan_and_bad_scope_rejected(environment):
 storage,scope,engine,_=environment
 class Host:
  def __init__(self):self.calls=0
  def collect(self,session):self.calls+=1;return missing_packet('wrong',session.run_id,DAY)
 host=Host();engine.run(RunRequest(scope,'five-chart planning',DAY,'plan','macro'),five_chart_adapter=host);assert host.calls==0
 out=engine.run(RunRequest(scope,'five-chart wrong binding',DAY,'research','macro'),five_chart_adapter=host)
 assert host.calls==1 and out.status.value!='succeeded' and out.action=='NO_ACTION'
 assert json.loads(out.output)['error_code']=='invalid_schema'
 assert any(r['stage']=='react_action' and r['detail'].get('observation',{}).get('error_code')=='five_chart_scope_or_date_mismatch' for r in TraceRepository(storage).read(out.run_id,scope)['run_steps'])


def test_future_provider_window_is_rejected_before_network(tmp_path):
 doc={'id':'future','url':'https://yield.chinabond.com.cn/cbweb-czb-web/czb/historyQuery','params':{'startDate':'2026-07-01','endDate':'2026-12-01','gjqx':'0','locale':'cn_ZH','qxmc':'1'}}
 with pytest.raises(ValueError,match='future_chart_source_request'):FiveChartHost(scope_key='scope',as_of_date='2026-10-05',archive_root=tmp_path,documents=[doc])


def test_parallel_source_actions_share_explicit_outer_parent(environment):
 from concurrent.futures import ThreadPoolExecutor
 from threading import Barrier
 from a_share_claw.harness.runtime import RunSession
 storage,scope,engine,_=environment;session=RunSession(storage,RunRequest(scope,'parallel source spans',DAY,'research','macro'));barrier=Barrier(2)
 with session.action('source','batch','frozen_parallel_batch') as parent:
  def child(name):
   with session.action('source',name,'one_frozen_source',parent_id=parent.span_id):barrier.wait(timeout=2)
  with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(child,['a','b']))
 steps=TraceRepository(storage).read(session.run_id,scope)['run_steps'];starts=[r['detail'] for r in steps if r['stage']=='react_action' and r['detail']['boundary']=='start' and r['detail']['operation'] in {'a','b'}]
 assert len(starts)==2 and all(s['parent_span_id']==parent.span_id for s in starts)
 session.finish('{}')


def test_archive_reuse_requires_same_scope_and_explicit_original_clock():
 current=datetime.now(timezone.utc);p=missing_packet('scope','run',DAY);p['generated_at']=current.isoformat();p['refresh_started_at']=(current-timedelta(seconds=1)).isoformat()
 p['sources']=[{'id':'original','sha256':'0'*64,'retrieved_at':(current-timedelta(days=1)).isoformat(),'reused_at':current.isoformat(),'source_origin_run_id':'prior','acquisition_kind':'scoped_archive','scope_key':'scope'}]
 validate_packet(p,'scope','run',DAY)
 q=copy.deepcopy(p);q['sources'][0]['scope_key']='other'
 with pytest.raises(ValueError,match='unauthorized_chart_archive_reuse'):validate_packet(q,'scope','run',DAY)
 q=copy.deepcopy(p);q['sources'][0]['acquisition_kind']='fresh'
 with pytest.raises(ValueError,match='stale_chart_capture'):validate_packet(q,'scope','run',DAY)
