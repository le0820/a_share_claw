"""Implementation checks for reviewed host calendars; synthetic documents only."""
import hashlib,json
from datetime import datetime,timezone

import pytest

from a_share_claw.harness.calendar import session_calendar


def bundle(tmp_path):
    raw=b'Synthetic exchange rules, not an official source'
    (tmp_path/'rules.txt').write_bytes(raw)
    rules={'schema_version':'exchange-calendar-rules-v1','exchange':'SYNTHETIC',
        'market_timezone':'America/New_York','coverage_start':'2026-10-01','coverage_end':'2026-12-31',
        'regular_close':'16:00','closed_dates':['2026-11-26'],'special_closes':{'2026-11-27':'13:00'},
        'source_documents':[{'url':'https://synthetic.invalid/calendar','source_file':'rules.txt',
            'sha256':hashlib.sha256(raw).hexdigest(),'publication_date':None,'retrieved_at':'2026-10-01T02:00:00Z'}],
        'review_status':'host_reviewed','reviewed_at':'2026-10-01T03:00:00Z'}
    path=tmp_path/'calendar.json';path.write_text(json.dumps(rules));return path,rules


def execute(path,start='2026-11-02',end='2026-11-30'):
    return session_calendar(path,start,end,reference=datetime(2026,10,1,4,tzinfo=timezone.utc))


def test_session_generation_preserves_dst_early_close_and_previous_anchor(tmp_path):
    path,_=bundle(tmp_path);out=execute(path)
    assert out['anchor']=={'trade_date':'2026-10-30','close_at':'2026-10-30T16:00:00-04:00'}
    rows={row['trade_date']:row['close_at'] for row in out['sessions']}
    assert len(rows)==20 and '2026-11-26' not in rows
    assert rows['2026-11-02']=='2026-11-02T16:00:00-05:00'
    assert rows['2026-11-27']=='2026-11-27T13:00:00-05:00'
    assert 'rules_sha256=' in out['calendar_source'] and 'synthetic.invalid' in out['calendar_source']


@pytest.mark.parametrize('mutation',['omitted_source','tampered_raw','escape','unreviewed','future_capture','future_review',
                                    'duplicate_closure','late_special_close','weekend_special','closed_special','short_coverage'])
def test_incomplete_or_changed_bundle_is_rejected(tmp_path,mutation):
    path,rules=bundle(tmp_path)
    if mutation=='omitted_source':rules['source_documents']=[]
    elif mutation=='tampered_raw':(tmp_path/'rules.txt').write_bytes(b'changed')
    elif mutation=='escape':rules['source_documents'][0]['source_file']='../other.txt'
    elif mutation=='unreviewed':rules['review_status']='unverified'
    elif mutation=='future_capture':rules['source_documents'][0]['retrieved_at']='2026-10-01T05:00:00Z'
    elif mutation=='future_review':rules['reviewed_at']='2026-10-01T05:00:00Z'
    elif mutation=='duplicate_closure':rules['closed_dates']*=2
    elif mutation=='late_special_close':rules['special_closes']['2026-11-27']='17:00'
    elif mutation=='weekend_special':rules['special_closes']['2026-11-28']='13:00'
    elif mutation=='closed_special':rules['special_closes']['2026-11-26']='13:00'
    else:rules['coverage_start']='2026-11-01'
    path.write_text(json.dumps(rules))
    with pytest.raises(ValueError):execute(path)


def test_holiday_and_weekend_start_use_the_last_actual_session(tmp_path):
    path,_=bundle(tmp_path);out=execute(path,'2026-11-26','2026-11-29')
    assert out['anchor']['trade_date']=='2026-11-25'
    assert [r['trade_date'] for r in out['sessions']]==['2026-11-27']
    with pytest.raises(ValueError):execute(path,'2026-11-28','2026-11-29')


def test_source_publication_after_capture_is_rejected(tmp_path):
    path,rules=bundle(tmp_path);rules['source_documents'][0]['publication_date']='2026-10-02'
    path.write_text(json.dumps(rules))
    with pytest.raises(ValueError,match='future_calendar_source'):execute(path)
