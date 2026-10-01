"""Frozen monthly fact-pack comparisons, calculated by the core before roles."""
from __future__ import annotations

import calendar
import json
import math
from datetime import date

from .contracts import canonical, digest

BASIS={
    'reported_yoy_rate':{'industrial_value_added_yoy','services_production_yoy','retail_sales_yoy','cpi_yoy','core_cpi_yoy','ppi_yoy','pce_price_yoy_reported','core_pce_price_yoy_reported'},
    'reported_mom_rate':{'pce_price_mom_reported','core_pce_price_mom_reported'},
    'stock_yoy_rate':{'m1_yoy','m2_yoy','tsf_stock_yoy'},
    'cumulative_yoy_rate':{'fixed_asset_investment_yoy'},
    'level':{'urban_surveyed_unemployment'},
}


def checked_history(spec):
    groups=spec.get('monthly_history')
    if groups is None:return None
    if not isinstance(groups,list) or not 1<=len(groups)<=40:raise ValueError('invalid_research_spec')
    required={f['fact_id']:f for f in spec['required_facts']};seen=set();covered=set()
    for group in groups:
        if (not isinstance(group,dict) or set(group)!={'comparison_id','basis','fact_ids'} or
                not isinstance(group['comparison_id'],str) or not group['comparison_id'].strip() or group['comparison_id'] in seen or
                not isinstance(group['basis'],str) or group['basis'] not in BASIS or not isinstance(group['fact_ids'],list) or
                not 2<=len(group['fact_ids'])<=13 or any(not isinstance(i,str) or i not in required for i in group['fact_ids'])):
            raise ValueError('invalid_research_spec')
        seen.add(group['comparison_id']);items=[required[i] for i in group['fact_ids']];first=items[0];previous=None
        for f in items:
            if (f['metric'] not in BASIS[group['basis']] or any(f[k]!=first[k] for k in ('entity','metric','unit','value_type')) or
                    f['unit']!='percent' or f['value_type']!='number' or f['entity']!=('US' if 'pce_price_' in f['metric'] else 'CN')):
                raise ValueError('research_fact_contract_mismatch')
            try:
                start,end=(date.fromisoformat(x) for x in f['data_period'].split('/'))
            except (ValueError,AttributeError):raise ValueError('invalid_research_spec') from None
            index=end.year*12+end.month
            if (end.day!=calendar.monthrange(end.year,end.month)[1] or
                    start!=date(end.year,1 if group['basis']=='cumulative_yoy_rate' else end.month,1) or
                    f['observation_start']!=end.isoformat() or f['observation_end']!=end.isoformat() or
                    previous is not None and (index!=previous+1)):
                raise ValueError('research_fact_contract_mismatch')
            if group['basis']=='cumulative_yoy_rate' and start.year!=date.fromisoformat(items[0]['data_period'].split('/')[0]).year:
                raise ValueError('research_fact_contract_mismatch')
            previous=index
        covered.update(group['fact_ids'])
    # A monthly job must declare history for every admitted native macro requirement.
    native={i for i,f in required.items() if f['metric'] in set().union(*BASIS.values())}
    if covered!=native:raise ValueError('insufficient_coverage:monthly_history')
    for q in spec['questions']:
        if q['question_id'] in {'base_scenario','risk_monitoring'} and not native<=set(q['required_fact_ids']):
            raise ValueError('insufficient_coverage:monthly_history')
    return json.loads(canonical(groups))


def compare(spec,facts):
    groups=checked_history(spec)
    if groups is None:return None
    comparisons=[]
    for group in groups:
        items=[facts[i] for i in group['fact_ids']]
        if any(type(f['value']) not in (int,float) or not math.isfinite(f['value']) or f['fallback_status']!='none' for f in items):
            raise ValueError('unverified_evidence')
        if len({f['source'] for f in items})!=1:raise ValueError('research_fact_contract_mismatch')
        # Same file is a common published version, not proof of historical PIT availability.
        same_version=len({(f['source_file'],f['publication_date']) for f in items})==1
        limitations=['Reported rate/level differences are percentage points, never a new month-on-month growth rate.',
                     'Two observations establish only an adjacent change; a persistent trend requires a longer comparable history.',
                     'Current capture does not certify a historical point-in-time vintage.']
        if not same_version:limitations.append('Separate release versions: published-rate comparison only; historical revisions are not harmonized, so underlying trend is not certified.')
        if group['basis']=='cumulative_yoy_rate':limitations.append('Changing year-to-date windows: compare cumulative YoY rates only; no standalone monthly growth inference.')
        comparisons.append({**group,'entity':items[0]['entity'],'metric':items[0]['metric'],'unit':'percentage_points',
            'formula':'current - previous','observations':[{'fact_id':f['fact_id'],'period':f['data_period'],'value':f['value'],
                'publication_date':f['publication_date'],'source_file':f['source_file'],'available_at':f.get('available_at'),
                'input_hash':digest(f),'source_notes':f.get('availability',{}).get('source_notes',[])} for f in items],
            'changes':[{'previous_fact_id':a['fact_id'],'current_fact_id':b['fact_id'],'value':b['value']-a['value']} for a,b in zip(items,items[1:])],
            'comparison_scope':'same_published_version' if same_version else 'separate_published_versions',
            'persistent_trend_certified':False,'limitations':limitations})
    return {'schema_version':'monthly-history-v1','comparisons':comparisons}
