import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('win_odds', ROOT/'src/research_maintenance/win_odds_scoring.py')
kernel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kernel)


def test_actual_35_index_snapshot_has_no_valid_scores():
    frame = json.loads((ROOT/'data/research/metric_pool/snapshots/win_odds.json').read_text())
    universe = [r['code'] for r in frame['rows']]
    assert len(set(universe)) == 35
    assert {'399673', 'NDX', 'SPX', 'HSTECH'} <= set(universe)
    for row in frame['rows']:
        scored = kernel.score(row, cutoff=frame['as_of_date'], universe=universe, methodology={})
        assert scored['gaps'] and scored['composite_score'] is None


def test_nested_weights_and_user_composite_math(tmp_path):
    raw = tmp_path/'test_only.json'
    raw.write_text('SYNTHETIC_TEST_ONLY')
    provenance = {'source_files': [str(raw)], 'source_sha256': {str(raw): hashlib.sha256(raw.read_bytes()).hexdigest()}}
    keys = ('forecast_horizon','forecast_aggregation','pe_denominator','loss_handling','volume_price_elasticity_formula','volatility_reversal_formula','icir_window','icir_target','icir_sign_normalization','contract_liability_adjustment')
    method = {'status': 'reviewed', 'version': 'TEST_ONLY', 'definitions': {k:'TEST_ONLY' for k in keys}}
    method['definitions']['cross_section_universe'] = ['TEST_ONLY']
    factors = {}
    for key in kernel.EARNINGS + kernel.MICRO + kernel.ODDS:
        factors[key] = {**provenance, 'admission_status':'reviewed','methodology_version':'TEST_ONLY',
                        'observation_date':'2026-10-07','available_date':'2026-10-07','lookback_years':5,
                        'history_complete':True,'value':80 if key in kernel.EARNINGS else 60 if key in kernel.MICRO else 20}
    record = {'code':'TEST_ONLY','as_of_date':'2026-10-07','factors':factors,'contract_liability_adjustment_status':'reviewed',
              'monthly_icir_weights':{**provenance,'admission_status':'reviewed','methodology_version':'TEST_ONLY',
                  'earnings':{k:.5 for k in kernel.EARNINGS},'micro':{k:1/3 for k in kernel.MICRO},
                  'win_categories':{'earnings':.5,'micro':.5},'latest_target_available_date':'2026-09-30','effective_date':'2026-10-01'}}
    result = kernel.score(record,cutoff='2026-10-07',universe=['TEST_ONLY'],methodology=method)
    assert result['win_rate_score'] == 70
    assert result['odds_score'] == 80
    assert result['composite_score'] == 72
    record['monthly_icir_weights']['latest_target_available_date']='2026-10-08'
    assert kernel.score(record,cutoff='2026-10-07',universe=['TEST_ONLY'],methodology=method)['composite_score'] is None
