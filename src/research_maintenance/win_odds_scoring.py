"""Deterministic scoring kernel. Caller must supply reviewed factor definitions,
dated percentiles and monthly ICIR weights; this does not invent missing evidence.
Only score inputs that belong to the frozen requested universe and cutoff.
"""
from datetime import date
import math
import hashlib
from pathlib import Path

EARNINGS=('eps_yoy_percentile','roe_yoy_change_percentile')
MICRO=('momentum_percentile','volatility_reversal_percentile','volume_price_elasticity_percentile')
ODDS=('pe_5y_percentile','pb_5y_percentile','ps_5y_percentile')

def finite(x):
    return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)

def weights_valid(weights, keys):
    return (isinstance(weights,dict) and set(weights)==set(keys)
            and all(finite(v) and v>=0 for v in weights.values())
            and abs(sum(weights.values())-1)<1e-10)

def provenance_valid(field):
    files=field.get('source_files')
    hashes=field.get('source_sha256')
    if not isinstance(files,list) or not files or len(files)!=len(set(files)): return False
    if not isinstance(hashes,dict) or set(files)!=set(hashes): return False
    try:
        return all(Path(f).is_absolute() and Path(f).is_file()
                   and hashlib.sha256(Path(f).read_bytes()).hexdigest()==hashes[f] for f in files)
    except (OSError,TypeError,ValueError): return False

def score(record, *, cutoff, universe, methodology):
    date.fromisoformat(cutoff)
    gaps=[]
    code=record.get('code')
    if code not in universe: gaps.append('index_identity_outside_requested_universe')
    if methodology.get('status')!='reviewed': gaps.append('methodology_not_reviewed')
    if not methodology.get('version'): gaps.append('methodology_version_missing')
    required_definition_keys=('forecast_horizon','forecast_aggregation','pe_denominator',
        'loss_handling','cross_section_universe','volume_price_elasticity_formula',
        'volatility_reversal_formula','icir_window','icir_target','icir_sign_normalization',
        'contract_liability_adjustment')
    definitions=methodology.get('definitions',{})
    if any(not definitions.get(k) for k in required_definition_keys): gaps.append('methodology_definition_missing')
    if definitions.get('cross_section_universe')!=list(universe): gaps.append('cross_section_universe_mismatch')
    if record.get('as_of_date')!=cutoff: gaps.append('requested_cutoff_mismatch')
    values={}
    for key in EARNINGS+MICRO+ODDS:
        field=record.get('factors',{}).get(key,{})
        v=field.get('value')
        if not finite(v) or not 0<=v<=100: gaps.append(key+':missing_or_invalid_value')
        if field.get('admission_status')!='reviewed': gaps.append(key+':not_reviewed')
        if field.get('methodology_version')!=methodology.get('version'): gaps.append(key+':methodology_mismatch')
        if not provenance_valid(field): gaps.append(key+':provenance_missing_or_hash_mismatch')
        for dkey in ('observation_date','available_date'):
            try:
                d=date.fromisoformat(field[dkey])
                if d>date.fromisoformat(cutoff): gaps.append(key+':future_'+dkey)
            except (ValueError,TypeError,KeyError): gaps.append(key+':invalid_'+dkey)
        if key in ODDS and field.get('lookback_years')!=5: gaps.append(key+':not_five_years')
        if key in ODDS and field.get('history_complete') is not True: gaps.append(key+':history_incomplete')
        values[key]=v
    weights=record.get('monthly_icir_weights',{})
    if weights.get('admission_status')!='reviewed': gaps.append('icir_weights_not_reviewed')
    if weights.get('methodology_version')!=methodology.get('version'): gaps.append('icir_methodology_mismatch')
    if not provenance_valid(weights): gaps.append('icir_provenance_missing_or_hash_mismatch')
    for k,keys in [('earnings',EARNINGS),('micro',MICRO),('win_categories',('earnings','micro'))]:
        if not weights_valid(weights.get(k),keys): gaps.append('invalid_'+k+'_weights')
    try:
        training_end=date.fromisoformat(weights['latest_target_available_date'])
        if training_end>date.fromisoformat(cutoff): gaps.append('icir_future_target_leakage')
        effective=date.fromisoformat(weights['effective_date'])
        if effective>date.fromisoformat(cutoff): gaps.append('icir_future_weights')
        if effective.strftime('%Y-%m')!=cutoff[:7]: gaps.append('icir_weights_wrong_month')
    except (ValueError,TypeError,KeyError): gaps.append('icir_dates_missing_or_invalid')
    # Extension must be incorporated in reviewed earnings factor construction.
    # Never drop it or choose an extra arbitrary weight in this kernel.
    if record.get('contract_liability_adjustment_status')!='reviewed': gaps.append('contract_liability_adjustment_unresolved')
    result={'code':code,'as_of_date':cutoff,'methodology_version':methodology.get('version'),
            'win_rate_score':None,'odds_score':None,'composite_score':None,'gaps':sorted(set(gaps)),
            'composite_weights':{'win':0.8,'odds':0.2},'chart_crosshair':[50,50],
            'score_semantics':'0-100 research score; not calibrated probability'}
    if gaps: return result
    earnings=sum(values[k]*weights['earnings'][k] for k in EARNINGS)
    micro=sum(values[k]*weights['micro'][k] for k in MICRO)
    win=earnings*weights['win_categories']['earnings']+micro*weights['win_categories']['micro']
    odds=sum(100-values[k] for k in ODDS)/3
    result.update(win_rate_score=win,odds_score=odds,composite_score=0.8*win+0.2*odds)
    return result
