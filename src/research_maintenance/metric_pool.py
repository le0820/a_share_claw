"""Manual research maintenance, outside core providers and official scoring.

Frozen registry + immutable evidence imports + dated update status. No network
calls, scheduling, interpolation, fallback or official-state promotion here.
"""
import argparse
from collections import Counter
from datetime import date, datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
POOL = REPO / 'data/research/metric_pool'

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def load(path):
    return json.loads(path.read_text())

def timestamp(text):
    result = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Capture timestamps require a timezone')
    return result.astimezone(timezone.utc)

def validate_registry(pool=POOL):
    registry = load(pool / 'registry.json')
    metrics = registry['metrics']
    ids = [m['metric'] for m in metrics]
    if len(ids) != 111 or len(set(ids)) != 111:
        raise ValueError('Original 111-field pool changed')
    if dict(Counter(m['classification'] for m in metrics)) != registry['counts']:
        raise ValueError('Classification counts differ')
    sources = load(pool / 'sources.json')
    for m in metrics:
        if not m['refresh_entry'] or not m['refresh_requirements'] or not m['next_review_action']:
            raise ValueError('Missing metric maintenance contract: ' + m['metric'])
        if not set(m['snapshot_source_ids']) <= set(sources):
            raise ValueError('Unknown refresh source')
        if m['official_history_eligible'] or m['formal_complete']:
            raise ValueError('Snapshot pool cannot promote official state')
    for name, entry in load(pool / 'snapshots/manifest.json').items():
        if Path(name).name != name or digest((pool / 'snapshots' / name).read_bytes()) != entry['packaged_sha256']:
            raise ValueError('Frozen snapshot hash mismatch: ' + name)
    spec_hash = digest((pool / 'snapshots/model_spec_v1.md').read_bytes())
    if spec_hash != registry['spec_sha256']:
        raise ValueError('Original AISDI specification changed')
    return registry

IDENTITY = ('metric', 'series_id', 'unit', 'population', 'definition', 'frequency', 'source_url')
MAX_AGE_DAYS = {'daily': 5, 'weekly': 21, 'monthly': 60, 'quarterly': 150, 'event': None}

def validate_packet(packet, raw, registry):
    if packet.get('schema_version') != 1:
        raise ValueError('Unknown series packet schema')
    if packet.get('metric') not in {m['metric'] for m in registry['metrics']}:
        raise ValueError('Metric is outside original pool')
    if any(not isinstance(packet.get(k), str) or not packet[k].strip() for k in IDENTITY):
        raise ValueError('Series identity/units/definition are required')
    if not packet['source_url'].startswith('https://'):
        raise ValueError('A public HTTPS source URL is required')
    if packet['frequency'] not in MAX_AGE_DAYS:
        raise ValueError('Use a reviewed daily/weekly/monthly/quarterly/event frequency')
    if packet.get('official_admitted', False):
        raise ValueError('Manual imports cannot declare official admission')
    captured = timestamp(packet['captured_at'])
    if captured > datetime.now(timezone.utc):
        raise ValueError('Future capture time')
    if digest(raw) != packet.get('source_payload_sha256'):
        raise ValueError('Original source payload hash mismatch')
    records = packet.get('records')
    if not isinstance(records, list) or not records:
        raise ValueError('Empty series')
    seen = set()
    for r in records:
        day = date.fromisoformat(r['observation_date'])
        if day > captured.date() or day in seen:
            raise ValueError('Future or duplicate observation date')
        seen.add(day)
        released = r.get('release_date')
        if released is not None and date.fromisoformat(released) > captured.date():
            raise ValueError('Release date later than capture')
        value = r.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Missing/nonfinite values are not observations')
        if r.get('qualifier') not in {'exact', 'approximate', 'lower_bound', 'upper_bound', 'conditional'}:
            raise ValueError('Native qualifiers must remain explicit')
    return captured

def immutable(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as f:
            f.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ValueError('Immutable evidence changed')

def import_series(packet_path, raw_path, runtime, pool=POOL):
    registry = validate_registry(pool)
    packet_bytes = packet_path.read_bytes()
    packet, raw = json.loads(packet_bytes), raw_path.read_bytes()
    validate_packet(packet, raw, registry)
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / 'series.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        journal = runtime / 'series_observations.jsonl'
        old = journal.read_text() if journal.exists() else ''
        entries = [json.loads(line) for line in old.splitlines() if line]
        identity = {key: packet[key] for key in IDENTITY}
        for entry in entries:
            if (entry['identity']['metric'], entry['identity']['series_id']) == (packet['metric'], packet['series_id']) and entry['identity'] != identity:
                raise ValueError('Same series ID changed source/unit/population/definition; create a reviewed new series')
        sha = digest(packet_bytes)
        folder = runtime / 'series_history' / sha
        immutable(folder / 'packet.json', packet_bytes)
        immutable(folder / 'source_payload', raw)
        duplicate = any(e['packet_sha256'] == sha for e in entries)
        if not duplicate:
            previous = [e for e in entries if e['identity'] == identity]
            revision = None
            if previous:
                # Compare capture order, not journal insertion order (imports may arrive out of order).
                previous = [e for e in previous if timestamp(e['captured_at']) < timestamp(packet['captured_at'])]
                if previous:
                    prev = max(previous, key=lambda e: timestamp(e['captured_at']))
                    old_raw = (runtime / prev['packet_path']).read_bytes()
                    if digest(old_raw) != prev['packet_sha256']:
                        raise ValueError('Previous archived packet changed')
                    before = {r['observation_date']: r for r in json.loads(old_raw)['records']}
                    after = {r['observation_date']: r for r in packet['records']}
                    revision = {'previous_packet_sha256': prev['packet_sha256'],
                                'changed_common_dates': sum(before[d] != after[d] for d in before.keys() & after.keys()),
                                'added_dates': sorted(after.keys() - before.keys()),
                                'dropped_dates': sorted(before.keys() - after.keys())}
            entry = {'identity': identity, 'captured_at': packet['captured_at'], 'packet_sha256': sha,
                     'source_payload_sha256': digest(raw), 'packet_path': str((folder / 'packet.json').relative_to(runtime)),
                     'semantic_review_status': 'unverified', 'official_admitted': False,
                     'revision': revision}
            temp = journal.with_suffix('.tmp')
            temp.write_text(old + json.dumps(entry, ensure_ascii=False) + '\n')
            os.replace(temp, journal)
    return {'metric': packet['metric'], 'series_id': packet['series_id'], 'duplicate_skipped': duplicate,
            'packet_sha256': sha, 'semantic_review_status': 'unverified', 'official_admitted': False}

def status(runtime, as_of, pool=POOL):
    registry = validate_registry(pool)
    cutoff = date.fromisoformat(as_of)
    if cutoff > datetime.now(timezone.utc).date():
        raise ValueError('Future status cutoff')
    journal = runtime / 'series_observations.jsonl'
    entries = [json.loads(line) for line in journal.read_text().splitlines()] if journal.exists() else []
    public_journal = runtime / 'public_snapshot_history/observations.jsonl'
    public_entries = [json.loads(line) for line in public_journal.read_text().splitlines()] if public_journal.exists() else []
    public_latest = {}
    for e in public_entries:
        if timestamp(e['retrieved_at']).date() > cutoff:
            continue
        payload = (runtime / e['payload_path']).read_bytes()
        if digest(payload) != e['payload_sha256']:
            raise ValueError('Public archived payload hash mismatch')
        source_id = e['source']
        if source_id not in public_latest or timestamp(e['retrieved_at']) > timestamp(public_latest[source_id]['retrieved_at']):
            public_latest[source_id] = e
    rows = []
    for m in registry['metrics']:
        candidates = [e for e in entries if e['identity']['metric'] == m['metric']]
        histories = []
        for e in candidates:
            raw = (runtime / e['packet_path']).read_bytes()
            if digest(raw) != e['packet_sha256']:
                raise ValueError('Archived packet hash mismatch')
            packet = json.loads(raw)
            source = (runtime / e['packet_path']).parent / 'source_payload'
            if digest(source.read_bytes()) != e['source_payload_sha256']:
                raise ValueError('Archived source payload hash mismatch')
            captured = timestamp(e['captured_at'])
            # A current capture cannot certify a past cutoff or unknown publication time.
            if captured.date() > cutoff:
                continue
            records = [r for r in packet['records'] if r['observation_date'] <= as_of]
            if records:
                latest = max(records, key=lambda r: r['observation_date'])
                age = (cutoff - date.fromisoformat(latest['observation_date'])).days
                max_age = MAX_AGE_DAYS[e['identity']['frequency']]
                histories.append({'series_id': e['identity']['series_id'], 'unit': e['identity']['unit'],
                                  'captured_at': e['captured_at'], 'latest_observation_date': latest['observation_date'],
                                  'latest_release_date': latest.get('release_date'),
                                  'age_days': age, 'frequency': e['identity']['frequency'],
                                  'max_age_days': max_age, 'refresh_due': age > max_age if max_age is not None else None,
                                  'packet_sha256': e['packet_sha256'], 'semantic_review_status': 'unverified'})
        capture_status = []
        for source_id in m['snapshot_source_ids']:
            e = public_latest.get(source_id)
            summary = e.get('summary') or {} if e else {}
            latest_data_date = summary.get('last_date_label')
            capture_status.append({'source_id': source_id, 'captured_at': e['retrieved_at'] if e else None,
                                   'accepted_snapshot': e['accepted_research_snapshot'] if e else None,
                                   'quarantine_reason': e['quarantined_reason'] if e else None,
                                   'latest_source_data_date': latest_data_date,
                                   'data_age_days': (cutoff-date.fromisoformat(latest_data_date)).days if latest_data_date else None,
                                   'not_a_verified_original_metric': True})
        rows.append({'metric': m['metric'], 'classification': m['classification'],
                     'refresh_mode': m['refresh_mode'], 'source_ids': m['snapshot_source_ids'],
                     'frozen_snapshot_available': m['classification'] != '未获取',
                     'frozen_review_evidence': m['evidence_ref'],
                     'public_snapshot_updates': capture_status,
                     'dated_imported_vintages': histories, 'needs_semantic_review': True,
                     'official_admitted': False, 'next_action': m['next_review_action']})
    return {'as_of_date': as_of, 'pool_review_date': registry['review_date'], 'counts': registry['counts'],
            'goal_status': registry['goal_status'], 'scheduler_installed': False,
            'full_support': False, 'metrics': rows}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pool-dir', type=Path, default=POOL)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('validate')
    imp = sub.add_parser('import-series')
    imp.add_argument('packet', type=Path)
    imp.add_argument('--raw', required=True, type=Path, help='Authorized original payload matching SHA256')
    imp.add_argument('--runtime-dir', type=Path, default=POOL / 'runtime')
    view = sub.add_parser('status')
    view.add_argument('--as-of', required=True)
    view.add_argument('--runtime-dir', type=Path, default=POOL / 'runtime')
    args = parser.parse_args()
    if args.command == 'validate':
        registry = validate_registry(args.pool_dir)
        result = {'metrics': len(registry['metrics']), 'counts': registry['counts'], 'valid': True}
    elif args.command == 'import-series':
        result = import_series(args.packet, args.raw, args.runtime_dir, args.pool_dir)
    else:
        result = status(args.runtime_dir, args.as_of, args.pool_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
