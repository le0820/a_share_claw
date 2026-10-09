import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_snapshots', ROOT / 'src/research_maintenance/public_snapshots.py')
snapshots = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshots)


def test_failed_payload_is_quarantined_and_repeat_is_idempotent(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    monkeypatch.setattr(snapshots, 'E', evidence)
    monkeypatch.setattr(snapshots, 'P', tmp_path / 'runtime')
    raw = b'<html>Application shell without numeric data</html>'
    (evidence / 'ramp_index.raw').write_bytes(raw)
    config = json.loads(snapshots.CONFIG.read_text())['ramp_tsm']
    (evidence / 'ramp_index.meta.json').write_text(json.dumps({
        'url': config['url'], 'status': 200, 'retrieved_at': '2026-01-01T00:00:00Z',
        'sha256': hashlib.sha256(raw).hexdigest()}))
    first = snapshots.archive('ramp_tsm', False)
    second = snapshots.archive('ramp_tsm', False)
    assert first['accepted'] is False
    assert second['duplicate_import_skipped'] is True
    entries = [json.loads(s) for s in (snapshots.P/'public_snapshot_history/observations.jsonl').read_text().splitlines()]
    assert len(entries) == 1
    assert entries[0]['official_admitted'] is False
    assert (snapshots.P / entries[0]['payload_path']).read_bytes() == raw


def ramp_fixture():
    rows = [{'usage_date': '2026-01-01', 'model_maker': maker, 'token_type': kind,
             'token_count_7d': 1, 'token_cost_usd_7d': 1}
            for maker in ('anthropic', 'openai') for kind in ('all_tokens', 'uncached_input', 'output')]
    return rows


def test_valid_ramp_json_with_whitespace():
    stream = json.dumps({'tokenPrices': ramp_fixture()})
    raw = ('self.__next_f.push( ' + json.dumps([1, stream]) + ')').encode()
    assert snapshots.validate('ramp_tsm', raw)['rows'] == 6


@pytest.mark.parametrize('variant', ['missing', 'duplicate', 'negative'])
def test_ramp_changed_basket_and_invalid_quantities_rejected(variant):
    rows = ramp_fixture()
    if variant == 'missing':
        rows.pop()
    elif variant == 'duplicate':
        rows.append(rows[0])
    else:
        rows[0]['token_count_7d'] = -1
    stream = json.dumps({'tokenPrices': rows})
    raw = ('self.__next_f.push(' + json.dumps([1, stream]) + ')').encode()
    with pytest.raises(AssertionError):
        snapshots.validate('ramp_tsm', raw)
