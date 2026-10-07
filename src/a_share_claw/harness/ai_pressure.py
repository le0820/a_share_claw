"""Native filed-date financial proxies for the user-selected pressure model.

Current Company Facts captures are not certified historical revision vintages.
"""
from datetime import date
from .five_charts import score,pressure

BUYERS=('amzn','msft','goog')
DEBT={'amzn':'ProceedsFromIssuanceOfLongTermDebt','msft':'ProceedsFromDebtMaturingInMoreThanThreeMonths','goog':'ProceedsFromDebtNetOfIssuanceCosts'}
CAPEX={'amzn':'PaymentsToAcquireProductiveAssets','msft':'PaymentsToAcquirePropertyPlantAndEquipment','goog':'PaymentsToAcquirePropertyPlantAndEquipment'}
OCF='NetCashProvidedByUsedInOperatingActivities'

def duration(r):return (date.fromisoformat(r['end'])-date.fromisoformat(r['start'])).days+1

def native_ttm(rows,cutoff):
 selected={}
 for r in sorted(rows,key=lambda r:(r.get('filed',''),r.get('accn',''))):
  if r.get('form') in ('10-K','10-Q') and 'start' in r and r['filed']<=cutoff and r['end']<=cutoff:selected[(r['start'],r['end'])]=r
 rows=list(selected.values());annual=[r for r in rows if 350<=duration(r)<=380]
 if not annual:raise ValueError('missing_native_annual')
 a=max(annual,key=lambda r:(r['end'],r['filed']))
 ytd=[r for r in rows if r['end']>a['end'] and 70<=duration(r)<=310 and 1<=(date.fromisoformat(r['start'])-date.fromisoformat(a['end'])).days<=2]
 if not ytd:return {'value':a['val'],'period_end':a['end'],'components':[a],'formula':'native annual / native trailing year'}
 y=max(ytd,key=lambda r:(r['end'],r['filed']))
 prior=[r for r in rows if abs(duration(r)-duration(y))<=7 and 350<=(date.fromisoformat(y['end'])-date.fromisoformat(r['end'])).days<=378 and abs((date.fromisoformat(r['start'])-date.fromisoformat(a['start'])).days)<=2]
 if not prior:raise ValueError('missing_comparable_prior_ytd')
 p=max(prior,key=lambda r:(r['end'],r['filed']))
 return {'value':a['val']+y['val']-p['val'],'period_end':y['end'],'components':[a,y,p],'formula':'prior FY + current YTD - comparable prior YTD'}

def quarter_key(day):return int(day[:4])*4+(int(day[5:7])-1)//3

def compute(facts,dc,real_yields,cutoff):
 native={}
 for buyer in BUYERS:
  native[buyer]={name:facts[buyer]['facts']['us-gaap'][concept]['units']['USD'] for name,concept in [('capex',CAPEX[buyer]),('ocf',OCF),('debt',DEBT[buyer])]}
 events=sorted({r['filed'] for vals in native.values() for rows in vals.values() for r in rows if r.get('start') and '2020-01-01'<=r['filed']<=cutoff}|{r['release_date'] for r in dc if r['release_date']<=cutoff}|set(real_yields))
 history={'demand':{},'cash_flow':{},'external_financing':{}};yield_history=[];output=[]
 dc=sorted(dc,key=lambda r:r['fiscal_year']*4+r['quarter'])
 for day in events:
  available=[r for r in dc if r['release_date']<=day];metrics={};scores={k:None for k in ('demand','cash_flow','external_financing','external_constraints')};details={};source_ids=[]
  try:
   last=available[-8:]
   if len(last)!=8 or any((b['fiscal_year']*4+b['quarter'])-(a['fiscal_year']*4+a['quarter'])!=1 for a,b in zip(last,last[1:])):raise ValueError('missing_dc_yoy_ttm')
   metrics['demand']=(sum(r['value_usd_millions'] for r in last[-4:])/sum(r['value_usd_millions'] for r in last[:4])-1)*100
   key=last[-1]['fiscal_year']*4+last[-1]['quarter'];h=history['demand'];scores['demand']=score(metrics['demand'],[v for k,v in sorted(h.items()) if k<key],polarity=-1);h[key]=metrics['demand'];details['dc_components']=last;source_ids.extend(r['source_id'] for r in last)
  except (ValueError,ZeroDivisionError):pass
  try:
   components={b:{name:native_ttm(rows,day) for name,rows in vals.items()} for b,vals in native.items()}
   capex=sum(v['capex']['value'] for v in components.values());ocf=sum(v['ocf']['value'] for v in components.values());debt=sum(v['debt']['value'] for v in components.values())
   if capex<=0 or ocf<=0 or debt<0:raise ValueError('invalid_native_pressure_denominator')
   metrics['cash_flow']=capex/ocf;metrics['external_financing']=debt/capex
   key=max(quarter_key(x['period_end']) for vals in components.values() for x in vals.values())
   for name in ('cash_flow','external_financing'):
    h=history[name];scores[name]=score(metrics[name],[v for k,v in sorted(h.items()) if k<key]);h[key]=metrics[name]
   details['buyer_ttm']=components;source_ids.extend('sec_'+b for b in BUYERS)
  except (KeyError,ValueError,ZeroDivisionError):pass
  # Yield scores update on native Treasury observations only; on non-yield events,
  # explicitly carry the last published yield and its already computed score.
  if day in real_yields:
   value=real_yields[day]['value'];yield_score=score(value,yield_history,minimum=60,window=252);yield_history.append(value);last_yield=real_yields[day]
  if yield_history:
   metrics['external_constraints']=last_yield['value'];scores['external_constraints']=yield_score;details['yield_observation_date']=last_yield['date'];source_ids+=last_yield['source_ids']
  result=pressure(scores)
  if day>='2025-01-01':output.append({'date':day,**result,'scores':scores,'metrics':metrics,'components':details,'source_ids':sorted(set(source_ids)),'release_date':day,'calibration':'prior distinct quarterly periods: financial min8/max12; prior native yields min60/max252; zero MAD is missing'})
 return output
