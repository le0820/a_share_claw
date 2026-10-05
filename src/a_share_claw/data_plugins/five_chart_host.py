"""Explicit host-owned refresh of frozen public documents; no model-facing tools.

A host must authorize one scope and the current market date, and provide the
reviewed publication descriptors. This adapter cannot publish policy state.
"""
from pathlib import Path
from datetime import date,datetime,timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit,urljoin
from concurrent.futures import ThreadPoolExecutor,as_completed
import hashlib,json,re,ssl
import httpx,truststore
from .tdx import WorkerTransport,INDEXES
from .five_normalize import normalize,text
from .providers import _Publication
from ..harness.contracts import digest
from ..harness.five_charts import missing_packet

class FiveChartHost:
 def __init__(self,*,scope_key,as_of_date,archive_root,documents,sec_user_agent=None):
  if not isinstance(scope_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',scope_key):raise ValueError('invalid_chart_scope_key')
  if date.fromisoformat(as_of_date).isoformat()!=as_of_date:raise ValueError('invalid_chart_as_of_date')
  self.scope_key=scope_key;self.as_of_date=as_of_date;self.root=Path(archive_root).resolve();self.documents=tuple(dict(x) for x in documents);self.sec_user_agent=sec_user_agent
  if len(self.documents)>180 or len({s['id'] for s in self.documents})!=len(self.documents):raise ValueError('invalid_five_chart_source_plan')
  for s in self.documents:
   self._allowed(s)
   params=s.get('params',{})
   if params.get('endDate',as_of_date)>as_of_date or int(params.get('field_tdr_date_value',as_of_date[:4]))>int(as_of_date[:4]):raise ValueError('future_chart_source_request')
  year=as_of_date[:4]
  self.discovery=({'id':'nbs_chart_index_0','url':'https://www.stats.gov.cn/sj/zxfb/index.html'},{'id':'nbs_chart_index_1','url':'https://www.stats.gov.cn/sj/zxfb/index_1.html'},{'id':'pbc_chart_index','url':'https://www.pbc.gov.cn/diaochatongjisi/116219/116225/index.html'},{'id':'nvda_chart_archive','url':'https://nvidianews.nvidia.com/news','params':{'year':year,'q':'financial results'}})
  for descriptor in self.discovery:self._allowed(descriptor)
  self.plan_hash=digest({'as_of_date':as_of_date,'scope_key':scope_key,'sources':self.documents,'discovery':self.discovery,'max_new_publications':16})

 @staticmethod
 def _allowed(s):
  if not re.fullmatch(r'[a-zA-Z0-9_]{1,80}',s['id']):raise ValueError('invalid_chart_source_id')
  if s.get('kind')=='tdx':
   if s.get('symbol') not in {'HSTECH.HK','SOX.PHLX','NDX.NASDAQ','SPX.SP500'}:raise ValueError('chart_index_not_declared')
   return
  parsed=urlsplit(s['url'])
  if parsed.scheme!='https' or parsed.username or parsed.password or parsed.port not in (None,443) or parsed.fragment or parsed.query:raise ValueError('chart_source_url_denied')
  host,path=parsed.hostname,parsed.path
  allowed=(host=='www.swsresearch.com' and path in {'/institute-sw/api/index_publish/current/','/institute-sw/api/index_publish/trend/'} or
   host=='www.stats.gov.cn' and (path in {'/sj/zxfb/index.html','/sj/zxfb/index_1.html'} or re.fullmatch(r'/sj/zxfb/202[0-9][01][0-9]/t202[0-9][01][0-9][0-3][0-9]_[0-9]+\.html',path)) or
   host=='www.pbc.gov.cn' and path.startswith('/diaochatongjisi/') and path.endswith(('.html','.shtml')) or
   host=='apps.bea.gov' and path=='/national/Release/XLS/Survey/Section2All_xls.xlsx' or
   host=='data.sec.gov' and path in {'/api/xbrl/companyfacts/CIK0001018724.json','/api/xbrl/companyfacts/CIK0000789019.json','/api/xbrl/companyfacts/CIK0001652044.json'} or
   host=='home.treasury.gov' and path=='/resource-center/data-chart-center/interest-rates/pages/xml' or
   host=='yield.chinabond.com.cn' and path=='/cbweb-czb-web/czb/historyQuery' or
   host=='nvidianews.nvidia.com' and (path=='/news' or re.fullmatch(r'/news/nvidia-announces-financial-results-for-(?:first|second|third|fourth)-quarter-(?:and-)?fiscal-20[0-9]{2}',path)) or
   host=='investor.nvidia.com' and re.fullmatch(r'/news/press-release-details/20[0-9]{2}/NVIDIA-Announces-Financial-Results-for-(?:First|Second|Third|Fourth)-Quarter-(?:and-)?Fiscal-20[0-9]{2}/default.aspx',path) or
   host=='jrj.sh.gov.cn' and path.endswith('.html'))
  if not allowed:raise ValueError('chart_source_not_declared')
  if host not in {'www.swsresearch.com','home.treasury.gov','yield.chinabond.com.cn'} and not(host=='nvidianews.nvidia.com' and path=='/news' and s.get('params')=={'year':str(s.get('params',{}).get('year')), 'q':'financial results'}) and s.get('params'):
   raise ValueError('unexpected_chart_source_parameters')
  if host=='nvidianews.nvidia.com' and path=='/news' and (set(s.get('params',{}))!={'year','q'} or not re.fullmatch(r'202[0-9]',str(s['params']['year'])) or s['params']['q']!='financial results'):raise ValueError('invalid_issuer_discovery')
  if host=='www.swsresearch.com':
   p=s.get('params',{})
   if path.endswith('/trend/') and (set(p)!={'swindexcode','period'} or not re.fullmatch(r'801[0-9]{3}',p['swindexcode']) or p['period']!='DAY'):raise ValueError('invalid_sw_chart_parameters')
   if path.endswith('/current/') and p not in ({'page':1,'page_size':50,'indextype':'一级行业'},{'page':1,'page_size':150,'indextype':'二级行业'}):raise ValueError('invalid_sw_catalog_parameters')
  if host=='home.treasury.gov':
   p=s.get('params',{})
   if set(p)!={'data','field_tdr_date_value'} or p['data'] not in {'daily_treasury_real_yield_curve','daily_treasury_yield_curve'} or not re.fullmatch(r'202[0-9]',str(p['field_tdr_date_value'])):raise ValueError('invalid_treasury_parameters')
  if host=='yield.chinabond.com.cn':
   if s.get('method','POST')!='POST':raise ValueError('chart_mutation_denied')
   p=s.get('params',{})
   if set(p)!={'startDate','endDate','gjqx','locale','qxmc'} or p['gjqx']!='0' or p['locale']!='cn_ZH' or p['qxmc']!='1':raise ValueError('invalid_china_curve_parameters')
   if not 0<=(datetime.fromisoformat(p['endDate'])-datetime.fromisoformat(p['startDate'])).days<365:raise ValueError('invalid_china_curve_window')
  elif s.get('method','GET')!='GET':raise ValueError('chart_mutation_denied')

 def collect(self,session):
  if session.request.scope.key!=self.scope_key or session.request.as_of_date!=self.as_of_date:raise ValueError('five_chart_host_scope_mismatch')
  if self.as_of_date!=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():raise ValueError('five_chart_live_capture_requires_today')
  if len(self.documents)+20+session.tool_count>session.request.max_tool_calls:raise ValueError('five_chart_source_budget_required')
  session.checkpoint();folder=self.root/self.scope_key/session.run_id
  folder.mkdir(parents=True,exist_ok=False);sources=[];paths={};errors=[];started=datetime.now(timezone.utc).isoformat()
  # Descriptors are host-authorized before any source call; credentials never enter plan/trace.
  session.step('five_chart_plan',{'source_plan_hash':self.plan_hash,'source_count':len(self.documents)+4,'max_new_publications':16,'scope_key':self.scope_key,'as_of_date':self.as_of_date})
  with session.action_lock:parent_span_id=next(reversed(session.open_actions),None)
  previous_status={}
  def existing_publication(s):
   if not s['id'].startswith(('nbs_econ_','nbs_gdp_','news_dc_','nvda_dc_','pbc_m2_yoy_','pbc_jan_official_reprint')):return None
   candidates=sorted((self.root/self.scope_key).glob('*/'+s['id']+'.meta.json'),key=lambda f:f.stat().st_mtime,reverse=True)
   for meta_path in candidates:
    previous_id=meta_path.parent.name
    if previous_id==session.run_id:continue
    with session.action_lock:
     if previous_id not in previous_status:
      try:previous_status[previous_id]=session.repository.read(previous_id,session.request.scope)['status']
      except LookupError:previous_status[previous_id]='unavailable'
    if previous_status[previous_id]!='succeeded':continue
    meta=json.loads(meta_path.read_text());raw_path=meta_path.with_name(s['id']+'.raw');raw_path.resolve().relative_to((self.root/self.scope_key/previous_id).resolve())
    if meta.get('url')!=s.get('url') or meta.get('params')!=s.get('params') or not raw_path.is_file():continue
    raw=raw_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=meta.get('sha256'):continue
    publication,body=text(raw)
    if s['id'].startswith('nbs_') and (not publication.publication_date() or '国家统计局' not in body or not any(x in body for x in ('工业增加值','社会消费品','固定资产投资','GDP'))):continue
    if s['id'].startswith('pbc_') and 'M2' not in body:continue
    if s['id'].startswith(('news_dc_','nvda_dc_')) and 'Data Center' not in body:continue
    return raw,meta,previous_id
   return None
  def one(s):
   session.checkpoint()
   with session.action_lock:session.tool_count+=1
   if session.remaining<=1:raise TimeoutError('budget_exceeded')
   with session.action('source',s['id'],'refresh_frozen_public_chart_source',{'source_id':s['id'],'source_plan_hash':self.plan_hash},parent_id=parent_span_id) as span:
    captured=datetime.now(timezone.utc).isoformat()
    archived=existing_publication(s)
    if archived:
     raw,previous,origin_id=archived;path=folder/(s['id']+'.raw');path.write_bytes(raw)
     meta={**previous,'file':str(path),'acquisition_kind':'scoped_archive','scope_key':self.scope_key,'run_id':session.run_id,'source_origin_run_id':origin_id,'reused_at':captured,'source_plan_hash':self.plan_hash}
     (folder/(s['id']+'.meta.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2));span.observe(status='ok',output_hash=meta['sha256'],acquisition_kind='scoped_archive');return meta,path
    if s.get('kind')=='tdx':
     spec=INDEXES[s['symbol']];raw=WorkerTransport().request({'operation':'bars','kind':'international','market':spec['market'],'catalog_market':spec['market'],'code':spec['code'],'count':600});url=json.loads(raw)['endpoint']
    else:
     headers={'User-Agent':'Mozilla/5.0 (compatible; a-share-claw-research/0.1)'}
     if urlsplit(s['url']).hostname=='data.sec.gov':
      if not self.sec_user_agent:raise ValueError('sec_user_agent_not_configured')
      headers['User-Agent']=self.sec_user_agent
     if urlsplit(s['url']).hostname=='www.swsresearch.com':headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.swsresearch.com/institute_sw/allIndex/releasedIndex'}
     # Default trusted CA plus the system CA, without weakening TLS verification.
     ctx=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
     for attempt in range(3):
      session.checkpoint();captured=datetime.now(timezone.utc).isoformat()
      try:
       with httpx.Client(trust_env=False,verify=ctx,follow_redirects=False,timeout=min(20,session.remaining)) as client:
        with client.stream('POST' if urlsplit(s['url']).hostname=='yield.chinabond.com.cn' else 'GET',s['url'],params=s.get('params'),headers=headers) as response:
         response.raise_for_status();parts=[];size=0
         for chunk in response.iter_bytes():
          session.checkpoint();size+=len(chunk)
          if size>20_000_000:raise ValueError('chart_source_too_large')
          parts.append(chunk)
         raw=b''.join(parts);url=s['url']
       if urlsplit(url).hostname=='www.stats.gov.cn':
        publication,body=text(raw)
        if 'Please enable JavaScript' in body or '国家统计局' not in body:raise ValueError('native_publication_unavailable')
       break
      except (httpx.TransportError,httpx.HTTPStatusError) as exc:
       retryable=not isinstance(exc,httpx.HTTPStatusError) or exc.response.status_code==429 or exc.response.status_code>=500
       if attempt==2 or not retryable:raise
    path=folder/(s['id']+'.raw');path.write_bytes(raw)
    meta={'id':s['id'],'url':url,'params':s.get('params'),'retrieved_at':captured,'sha256':hashlib.sha256(raw).hexdigest(),'file':str(path),'status':200,'proxy':'disabled','tls_verified':s.get('kind')!='tdx','ca_basis':'native_system_truststore' if s.get('kind')!='tdx' else 'native_tcp_sdk','source_plan_hash':self.plan_hash,'scope_key':self.scope_key,'run_id':session.run_id,'acquisition_kind':'fresh'}
    (folder/(s['id']+'.meta.json')).write_text(json.dumps(meta,ensure_ascii=False,indent=2));span.observe(status='ok',output_hash=meta['sha256']);return meta,path
  # Independent frozen requests run concurrently; no model can alter this bounded set.
  with ThreadPoolExecutor(max_workers=4) as pool:
   pending={pool.submit(one,s):s for s in (*self.documents,*self.discovery)}
   for future in as_completed(pending):
    s=pending[future]
    try:
     meta,path=future.result();sources.append(meta);paths[s['id']]=path
    except Exception as exc:errors.append({'source_id':s['id'],'error_code':type(exc).__name__,**({'http_status':exc.response.status_code} if isinstance(exc,httpx.HTTPStatusError) else {})})
  # Latest official lists add only previously absent publications for the frozen metrics.
  known={d.get('url') for d in self.documents};extra=[]
  for descriptor in self.discovery:
   path=paths.get(descriptor['id'])
   if path is None:continue
   parser=_Publication();parser.feed(path.read_text(errors='replace'))
   for href,title in parser.links:
    url=urljoin(descriptor['url'],href);parts=urlsplit(url)
    if url in known or parts.query or parts.fragment:continue
    if descriptor['id'].startswith('nbs_'):
     if not re.search(r'202[5-9]年',title) or not any(metric in title for metric in ('工业增加值','社会消费品零售','全国固定资产投资','国内生产总值初步')):continue
     native=re.search(r'/(t(202[0-9][01][0-9][0-3][0-9])_[0-9]+)\.html$',parts.path)
     if not native or native[2]>self.as_of_date.replace('-',''):continue
     doc={'id':('nbs_gdp_' if '国内生产总值' in title else 'nbs_econ_')+native[1],'url':url}
    elif descriptor['id'].startswith('pbc_'):
     if '金融统计数据报告' not in title or '社会融资' in title:continue
     y=re.search(r'(202[0-9])年',title);m=re.search(r'年(\d{1,2})月',title)
     if not y:continue
     month=int(m[1]) if m else 6 if '上半年' in title else 9 if '前三季度' in title else 3 if '一季度' in title else 12
     period_end=f'{y[1]}-{month:02}-01'
     if period_end>self.as_of_date:continue
     doc={'id':f'pbc_m2_yoy_{y[1]}_{month:02}','url':url}
    else:
     native=re.fullmatch(r'/news/nvidia-announces-financial-results-for-(first|second|third|fourth)-quarter-(?:and-)?fiscal-(20[0-9]{2})',parts.path)
     if not native:continue
     doc={'id':'news_dc_'+native[2]+'_q'+str(('first','second','third','fourth').index(native[1])+1),'url':url}
    self._allowed(doc);known.add(url);extra.append(doc)
  if len(extra)>16:raise ValueError('five_chart_discovery_budget_exceeded')
  if extra:
   session.step('five_chart_discovered_plan',{'parent_plan_hash':self.plan_hash,'source_plan_hash':digest(extra),'source_ids':[d['id'] for d in extra]})
   with ThreadPoolExecutor(max_workers=3) as pool:
    pending={pool.submit(one,d):d for d in extra}
    for future in as_completed(pending):
     d=pending[future]
     try:meta,path=future.result();sources.append(meta);paths[d['id']]=path
     except Exception as exc:errors.append({'source_id':d['id'],'error_code':type(exc).__name__,**({'http_status':exc.response.status_code} if isinstance(exc,httpx.HTTPStatusError) else {})})
  session.checkpoint()
  try:
   with session.action('calculation','five_chart_native_normalization','compute_fixed_pressure_and_native_metric_bindings',{'source_hashes':{s['id']:s['sha256'] for s in sources}}) as span:
    packet=normalize(sources,lambda name:paths[name].read_bytes(),scope_key=self.scope_key,run_id=session.run_id,as_of_date=self.as_of_date)
    span.observe(status='ok',output_hash=digest(packet),gap_count=len(packet['gaps']))
  except (KeyError,ValueError,TypeError,ETParseError) as exc:
   packet=missing_packet(self.scope_key,session.run_id,self.as_of_date,'源数据解析未通过：'+type(exc).__name__);packet['sources']=sources
  # A nonempty series must not hide a failed historical publication or warm-up input.
  for error in errors:
   sid=error['source_id']
   if sid.startswith(('sec_','news_dc_','nvda_dc_','nvda_chart_')):charts=('cn_ai','us_ai')
   elif sid.startswith('treasury_real_'):charts=('cn_ai','us_ai','banks_rates','us_real_inflation')
   elif sid.startswith(('treasury_nominal_','china_curve_')):charts=('banks_rates',)
   elif sid.startswith('bea_'):charts=('us_real_inflation',)
   elif sid.startswith(('nbs_','pbc_')):charts=('cn_consumption',)
   elif sid=='sw_801780':charts=('banks_rates',)
   elif sid in {'sw_801770','sw_semiconductor_retry','sw_secondary_catalog_retry'}:charts=('cn_ai',)
   else:charts=('cn_consumption',)
   for cid in charts:packet['gaps'].append({'chart':cid,'metric':'原生来源 '+sid,'reason':'本次刷新失败：'+error['error_code']+(' HTTP '+str(error['http_status']) if 'http_status' in error else '')+'；未读取旧快照填补'})
  (folder/'source_errors.json').write_text(json.dumps({'scope_key':self.scope_key,'run_id':session.run_id,'source_plan_hash':self.plan_hash,'errors':errors},ensure_ascii=False,indent=2))
  packet['refresh_started_at']=started;packet['source_errors']=errors;packet['source_plan_hash']=self.plan_hash
  return packet

# XML errors are handled as data gaps, never converted to fake observations.
from xml.etree.ElementTree import ParseError as ETParseError


def load_host_contract(path,*,scope_key,as_of_date,archive_root):
 import os
 obj=json.loads(Path(path).read_text())
 if set(obj)!={'schema_version','scope_key','as_of_date','documents'} or obj['schema_version']!='five-chart-host-v1' or obj['scope_key']!=scope_key or obj['as_of_date']!=as_of_date:raise ValueError('invalid_five_chart_host_contract')
 return FiveChartHost(scope_key=scope_key,as_of_date=as_of_date,archive_root=archive_root,documents=obj['documents'],sec_user_agent=os.environ.get('SEC_USER_AGENT'))
