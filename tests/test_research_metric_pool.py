import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('metric_pool', ROOT / 'src/research_maintenance/metric_pool.py')
pool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pool)


def packet(tmp_path):
    raw = tmp_path / 'source.json'
    raw.write_text('{"synthetic_test_only":true}')
    data = {'schema_version': 1, 'metric': 'ai_cloud_revenue_usd', 'series_id': 'TEST_ONLY',
            'unit': 'USD', 'population': 'synthetic test company', 'definition': 'test-only revenue',
            'frequency': 'quarterly', 'source_url': 'https://example.org/test',
            'captured_at': '2026-01-02T00:00:00Z',
            'source_payload_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(),
            'records': [{'observation_date': '2025-12-31', 'release_date': None,
                         'value': 1, 'qualifier': 'approximate'}]}
    path = tmp_path / 'packet.json'
    path.write_text(json.dumps(data))
    return path, raw, data


def test_original_full_scope_and_all_snapshot_hashes():
    data = pool.validate_registry()
    assert data['counts'] == {'已获取': 40, '可间接估计': 33, '未获取': 38}
    assert data['win_odds_scored_indices'] == 0
    assert all(m['refresh_entry'] and m['refresh_requirements'] for m in data['metrics'])
    assert not any(m['official_history_eligible'] for m in data['metrics'])


def test_immutable_idempotent_vintage_revision_and_cutoff(tmp_path):
    path, raw, data = packet(tmp_path)
    runtime = tmp_path / 'runtime'
    first = pool.import_series(path, raw, runtime)
    assert not first['duplicate_skipped']
    assert pool.import_series(path, raw, runtime)['duplicate_skipped']
    data['captured_at'] = '2026-01-03T00:00:00Z'
    data['records'][0]['value'] = 2
    path.write_text(json.dumps(data))
    second = pool.import_series(path, raw, runtime)
    assert first['packet_sha256'] != second['packet_sha256']
    entries = [json.loads(x) for x in (runtime / 'series_observations.jsonl').read_text().splitlines()]
    assert entries[-1]['revision']['changed_common_dates'] == 1
    assert len(entries) == 2
    before = pool.status(runtime, '2026-01-02')
    selected = next(x for x in before['metrics'] if x['metric'] == data['metric'])
    assert len(selected['dated_imported_vintages']) == 1
    assert selected['dated_imported_vintages'][0]['latest_release_date'] is None
    assert not selected['official_admitted']
    assert not any(x['dated_imported_vintages'] for x in pool.status(runtime, '2025-12-31')['metrics'])


@pytest.mark.parametrize('change', [
    {'metric': 'outside_pool'}, {'source_payload_sha256': '0'*64},
    {'captured_at': '2026-01-02T00:00:00'}, {'official_admitted': True},
    {'captured_at': '2099-01-01T00:00:00Z'},
])
def test_invalid_contract_blocks_import(tmp_path, change):
    path, raw, data = packet(tmp_path)
    data.update(change)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        pool.import_series(path, raw, tmp_path / 'runtime')
    assert not (tmp_path / 'runtime/series_observations.jsonl').exists()


@pytest.mark.parametrize('update', [
    {'value': None}, {'value': float('nan')}, {'value': True}, {'qualifier': 'near_full'},
    {'observation_date': '2026-01-04'}, {'release_date': '2026-01-04'},
])
def test_bad_record_never_becomes_observation(tmp_path, update):
    path, raw, data = packet(tmp_path)
    data['records'][0].update(update)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        pool.import_series(path, raw, tmp_path / 'runtime')


def test_population_unit_drift_and_tamper_are_rejected(tmp_path):
    path, raw, data = packet(tmp_path)
    runtime = tmp_path / 'runtime'
    first = pool.import_series(path, raw, runtime)
    data['unit'] = 'RMB'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='changed source/unit'):
        pool.import_series(path, raw, runtime)
    archived = runtime / 'series_history' / first['packet_sha256'] / 'source_payload'
    archived.write_text('tampered')
    with pytest.raises(ValueError, match='payload hash'):
        pool.status(runtime, '2026-01-03')


def test_duplicate_observation_dates_rejected(tmp_path):
    path, raw, data = packet(tmp_path)
    data['records'].append(copy.deepcopy(data['records'][0]))
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='duplicate'):
        pool.import_series(path, raw, tmp_path / 'runtime')


def test_data_date_drives_refresh_due_not_capture_date(tmp_path):
    path, raw, data = packet(tmp_path)
    data['frequency'] = 'daily'
    path.write_text(json.dumps(data))
    runtime = tmp_path / 'runtime'
    pool.import_series(path, raw, runtime)
    row = next(x for x in pool.status(runtime, '2026-01-10')['metrics'] if x['metric'] == data['metric'])
    history = row['dated_imported_vintages'][0]
    assert history['age_days'] == 10
    assert history['refresh_due'] is True
