"""Manual, read-only snapshot archive for verified public research sources.
No scheduler, credentials, rentals, scores or official-state updates.
"""
import argparse,json,hashlib,subprocess,tempfile,fcntl,os,re,math
from pathlib import Path
from datetime import datetime,timezone,date
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
from decimal import Decimal,InvalidOperation
REPO=Path(__file__).resolve().parents[2]
P=REPO/'data/research/metric_pool/runtime'
E=P/'evidence'
CONFIG=REPO/'data/research/metric_pool/sources.json'
if not __debug__:raise RuntimeError("Validation requires Python assertions enabled")
SOURCES={'ramp_tsm':('ramp_index','ramp_tsm'),'openrouter_models':('openrouter_models','catalog'),'vast_h100_offers':('vast_h100_public_probe','offers'),'vercel_text_shares':('vercel_models_text_export','shares'),'inferencex_cost_registry':('inferencex_views_options_cost_review','cost_registry'),'inferencex_r1_modeled_cost':('inferencex_cost_review_r1_h200','cost_calculator'),'inferencex_v4_modeled_cost':('inferencex_cost_review_v4_b200','cost_calculator')}
def ramp_data(raw):
 page=raw.decode('utf-8');decoder=json.JSONDecoder();chunks=[]
 for match in re.finditer(r'self\.__next_f\.push\(',page):
  try:value,_=decoder.raw_decode(page[match.end():].lstrip())
  except ValueError:continue
  if isinstance(value,list) and len(value)>1 and isinstance(value[1],str):chunks.append(value[1])
 stream=''.join(chunks);marker='"tokenPrices":';pos=stream.find(marker)
 assert pos>=0,'Ramp token schema missing (HTML shell is not token data)'
 rows,_=decoder.raw_decode(stream[pos+len(marker):].lstrip());assert isinstance(rows,list) and rows,'Empty Ramp token data'
 return rows

def ramp_keys(raw):
 return {(r['usage_date'],r['model_maker'],r['token_type']):(r['token_count_7d'],r['token_cost_usd_7d']) for r in ramp_data(raw)}

def validate(kind,raw):
 if kind=='ramp_tsm':
  rows=ramp_data(raw);keys=set();bydate={};expected={(m,t) for m in ['anthropic','openai'] for t in ['all_tokens','uncached_input','output']}
  for row in rows:
   day=date.fromisoformat(row['usage_date']);key=(day,row['model_maker'],row['token_type']);assert key not in keys,'Duplicate Ramp key';keys.add(key)
   bydate.setdefault(day,set()).add(key[1:])
   for field in ['token_count_7d','token_cost_usd_7d']:
    value=row[field];assert not isinstance(value,bool) and isinstance(value,(int,float)) and math.isfinite(value) and value>=0,'Invalid Ramp quantity/cost'
  assert all(v==expected for v in bydate.values()),'Ramp provider/type basket changed or incomplete; review required'
  days=sorted(bydate);missing=[{'previous':x.isoformat(),'date':y.isoformat(),'days':(y-x).days} for x,y in zip(days,days[1:]) if (y-x).days!=1]
  return {'rows':len(rows),'date_labels':len(days),'first_date_label':days[0].isoformat(),'last_date_label':days[-1].isoformat(),'makers':['anthropic','openai'],'types':['all_tokens','uncached_input','output'],'calendar_gaps':missing,'measure':'TSM enterprise sample, native daily-labelled rolling7d counts and billed USD costs','fixed_customer_cohort_verified':False,'historical_PIT_verified':False,'all_tokens_counted_once':True}
 data=json.loads(raw)
 if kind=='cost_registry':
  assert data['view']=='options' and data['apiVersion']=='v1','Cost registry schema changed'
  hardware={r['key']:r for r in data['hardware']}
  assert len(hardware)==len(data['hardware']) and {'h200','b200'}<=set(hardware)
  prices={}
  for key in ['h200','b200']:
   value=Decimal(str(hardware[key]['costPerHour']['h']))
   assert value.is_finite() and value>0,'Invalid modeled GPU hourly cost'
   prices[key]=str(value)
  return {'basis':'registered modeled ownership TCO USD/GPU/hour','prices':prices,'historical_cost_vintage_verified':False}
 if kind=='cost_calculator':
  assert data['view']=='calculator' and data['apiVersion']=='v1','Calculator schema changed'
  rows=data['hardware'];assert isinstance(rows,list) and rows and data['count']==len(rows),'Empty or inconsistent calculator'
  params=data['params'];assert params['costProvider']=='costh' and params['costType']=='total' and params['tcoBasis']=='internal','Cost basis changed'
  assert params['sequence']=='8k/1k' and params['target']==35 and params['percentile']=='p90','Workload changed'
  keys=set();eligible=[];excluded=[]
  for row in rows:
   key=row['resultKey'];assert key not in keys,'Duplicate configuration';keys.add(key)
   for value in [row['value'],row['cost']['total']]:
    number=Decimal(str(value));assert number.is_finite() and number>0,'Invalid cost/throughput'
   assert isinstance(row['clamped'],bool),'Clamping verdict missing'
   (excluded if row['clamped'] else eligible).append(key)
  return {'model':params['model'],'configurations':len(rows),'unclamped_configurations':eligible,
   'excluded_clamped_configurations':excluded,'frozen_params':params,'performance_asof_date':params['date'],
   'cost_observation_basis':'snapshot retrieval time, not performance asof date','historical_cost_vintage_verified':False,
   'production_applicability_verified':False}
 if kind=='catalog':
  rows=data['data'];assert isinstance(rows,list) and rows,'Empty/invalid catalog'
  ids=[r['id'] for r in rows];assert len(ids)==len(set(ids)),'Duplicate model IDs'
  excluded=[]
  for r in rows:
   for key in ['prompt','completion']:
    assert key in r['pricing'],'Missing expected price field'
    try:value=Decimal(str(r['pricing'][key]));valid=value.is_finite() and value>=0
    except InvalidOperation:valid=False
    if not valid:excluded.append({'model':r['id'],'field':key,'raw_value':r['pricing'][key],'reason':'Not usable numeric price; excluded, never zero-filled'})
  return {'model_count':len(rows),'pricing_unit':'advertised USD/token','excluded_price_fields':excluded,'numeric_pricing_models':len(rows)-len({r['model'] for r in excluded}),'historical_prices_verified':False}
 if kind=='shares':
  assert data['dataset']=='models' and data['modality']=='text' and data['license']=='CC-BY-4.0','Export contract changed'
  rows=data['rows'];assert isinstance(rows,list) and rows,'Empty share export'
  keys=set();totals={}
  for r in rows:
   assert r['group']=='model' and r['modality']=='text' and r['metric'] in ['requests','tokens','spend'],'Share dimensions changed'
   value=Decimal(str(r['share_percent']));assert value.is_finite() and 0<=value<=100,'Invalid share'
   key=(r['date'],r['name'],r['metric']);assert key not in keys,'Duplicate share rows';keys.add(key)
   group=(r['date'],r['metric']);totals[group]=totals.get(group,Decimal(0))+value
  assert all(abs(v-100)<Decimal('0.001') for v in totals.values()),'Incomplete share denominator'
  return {'rows':len(rows),'days':len({r['date'] for r in rows}),'absolute_volume':False,'spend_basis':'list-price normalized','license':'CC-BY-4.0'}
 rows=data['offers'];assert isinstance(rows,list),'Invalid offer list'
 ids=set();unique=set()
 for r in rows:
  assert r['id'] not in ids,'Duplicate offer IDs';ids.add(r['id'])
  assert r['gpu_name']=='H100 SXM' and r['rentable'] is True and r['rented'] is False and r['verification']=='verified','Query scope changed'
  assert isinstance(r['gpu_ids'],list) and len(r['gpu_ids'])==len(set(r['gpu_ids']))==r['num_gpus'],'GPU identity/count mismatch'
  assert r['machine_id'] is not None,'Missing machine ID'
  unique|={(r['machine_id'],g) for g in r['gpu_ids']}
 return {'offer_count':len(rows),'unique_platform_gpu_ids':len(unique),'truncated':data.get('truncated'),'count_scope':'filtered platform listing; not guaranteed physical supply'}
def archive(source,fetch,as_of=None,saved_vintage=None):
 prefix,kind=SOURCES[source];baseline_body=E/(prefix+'.raw');baseline_meta=E/(prefix+'.meta.json')
 if saved_vintage is not None:
  assert source=='ramp_tsm' and not fetch,'Saved vintage selection is Ramp import only'
  if saved_vintage=='followup':baseline_body=E/'raw/ramp_followup_vintage/20261008.html';baseline_meta=baseline_body.with_suffix('.meta.json')
 configs=json.loads(CONFIG.read_text());config=configs[source]
 assert config['validation_kind']==kind and config['prefix']==prefix,'Source configuration mismatch'
 baseline=json.loads(baseline_meta.read_text()) if not fetch else config
 url=baseline['url']
 assert url==config['url'],'Unexpected source URL; review configuration explicitly'
 P.mkdir(parents=True,exist_ok=True)
 if as_of is not None:
  assert fetch and kind=='cost_calculator','--as-of requires --fetch with a cost calculator source'
  cutoff=date.fromisoformat(as_of);assert cutoff<=datetime.now(timezone.utc).date(),'Future cutoff forbidden'
  parsed=urlsplit(url);query=dict(parse_qsl(parsed.query));query['date']=as_of
  url=urlunsplit((parsed.scheme,parsed.netloc,parsed.path,urlencode(query),parsed.fragment))
 if fetch:
  with tempfile.TemporaryDirectory(dir=P) as temp:
   body=Path(temp)/'response';command=['/usr/bin/curl']
   if kind=='ramp_tsm':command+=['--noproxy','*']
   command+=['--location','--compressed','--silent','--show-error','--max-time','30','--max-filesize',str(16000000 if kind=='ramp_tsm' else 10000000),'--output',str(body),'--write-out','%{json}',url]
   proc=subprocess.run(command,capture_output=True,text=True,timeout=35)
   try:stats=json.loads(proc.stdout)
   except ValueError:stats={}
   raw=body.read_bytes() if body.exists() else b''
   meta={'source':source,'url':url,'retrieved_at':datetime.now(timezone.utc).isoformat(),'status':stats.get('http_code'),'curl_exit_code':proc.returncode,'ssl_verify_result':stats.get('ssl_verify_result'),'transport':('curl_direct_tls_verified' if kind=='ramp_tsm' else 'curl_existing_proxy_tls_verified')}
   transport_valid=proc.returncode==0 and stats.get('http_code')==200 and stats.get('ssl_verify_result')==0
 else:
  raw=baseline_body.read_bytes();meta={**baseline,'source':source};transport_valid=baseline.get('status')==200 and hashlib.sha256(raw).hexdigest()==baseline.get('sha256')
 digest=hashlib.sha256(raw).hexdigest();error=None;summary=None
 try:
  assert transport_valid,'HTTP/transport/hash validation failed'
  summary=validate(kind,raw)
  if kind=='ramp_tsm':
   capture_date=datetime.fromisoformat(meta['retrieved_at']).astimezone(timezone.utc).date();last=date.fromisoformat(summary['last_date_label']);assert last<=capture_date,'Future data label'
   summary.update(age_days_at_capture_UTC=(capture_date-last).days,native_daily5day_freshness=(capture_date-last).days<=5,weekly21day_freshness=(capture_date-last).days<=21,freshness_does_not_promote_scoring=True)
 except (AssertionError,KeyError,TypeError,ValueError,InvalidOperation) as exc:error=type(exc).__name__+': '+str(exc)
 root=P/'public_snapshot_history';folder=root/source/digest;folder.mkdir(parents=True,exist_ok=True)
 # Content-addressed payloads are immutable; failed responses remain quarantined evidence.
 dest=folder/'payload'
 if dest.exists():assert hashlib.sha256(dest.read_bytes()).hexdigest()==digest
 else:dest.write_bytes(raw)
 contract_version=4 if kind=='ramp_tsm' else 3
 observation={'validation_contract_version':contract_version,'source':source,'retrieved_at':meta['retrieved_at'],'payload_sha256':digest,'payload_path':str(dest.relative_to(P)),'url':url,'accepted_research_snapshot':error is None,'quarantined_reason':error,'summary':summary,'official_admitted':False,'historical_backfill':False,'metadata':meta}
 journal=root/'observations.jsonl';root.mkdir(exist_ok=True)
 with (root/'archive.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  old=journal.read_text() if journal.exists() else ''
  existing=[json.loads(line) for line in old.splitlines() if line]
  if kind=='ramp_tsm' and error is None:
   previous=[r for r in existing if r['source']==source and r['accepted_research_snapshot'] and r['retrieved_at']<observation['retrieved_at']]
   if previous:
    prev=max(previous,key=lambda r:r['retrieved_at']);prev_path=P/prev['payload_path'];prev_raw=prev_path.read_bytes();assert hashlib.sha256(prev_raw).hexdigest()==prev['payload_sha256'],'Previous archive changed'
    before=ramp_keys(prev_raw);after=ramp_keys(raw);common=set(before)&set(after)
    observation['numeric_revision_comparison']={'previous_payload_sha256':prev['payload_sha256'],'previous_capture_time':prev['retrieved_at'],'common_keys':len(common),'changed_common_keys':sum(before[k]!=after[k] for k in common),'added_keys':len(set(after)-set(before)),'dropped_keys':len(set(before)-set(after)),'post_cutoff_payloads_not_merged':True}
   else:observation['numeric_revision_comparison']=None
  duplicate=any(r['source']==source and r['retrieved_at']==observation['retrieved_at'] and r['payload_sha256']==digest and r['accepted_research_snapshot']==observation['accepted_research_snapshot'] and r['summary']==summary and r.get('validation_contract_version')==contract_version for r in existing)
  if not duplicate:
   temp=journal.with_suffix('.tmp');temp.write_text(old+json.dumps(observation,ensure_ascii=False)+'\n');os.replace(temp,journal)
  fcntl.flock(lock,fcntl.LOCK_UN)
 return {'source':source,'accepted':error is None,'duplicate_import_skipped':duplicate,'error':error,'summary':summary,'numeric_revision_comparison':observation.get('numeric_revision_comparison')}
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--source',choices=['all',*SOURCES],default='all');parser.add_argument('--fetch',action='store_true',help='Fetch current public snapshots; default imports saved evidence with original times');parser.add_argument('--as-of',help='Explicit performance cutoff YYYY-MM-DD for a cost calculator fetch; costs remain current snapshot assumptions');parser.add_argument('--saved-vintage',choices=['baseline','followup'],help='Ramp saved evidence import with original capture time, no fetch');parser.add_argument('--runtime-dir',type=Path,default=P,help='Separate writable research archive; ignored by Git');parser.add_argument('--evidence-dir',type=Path,default=E,help='Existing authorized native payload/meta directory for offline imports');args=parser.parse_args();P=args.runtime_dir.resolve();E=args.evidence_dir.resolve()
 selected=SOURCES if args.source=='all' else [args.source]
 failed=False
 for source in selected:
  result=archive(source,args.fetch,args.as_of,args.saved_vintage);print(json.dumps(result,ensure_ascii=False));failed=failed or not result['accepted']
 raise SystemExit(1 if failed else 0)
