"""Trusted-host calendar preparation from reviewed, archived exchange rules.

No network, model, provider selection, prices or implicit holiday library. The
host owns completeness review; hashes prove archive integrity, not authenticity.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date,datetime,time,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .contracts import digest,validate_date
from .quant import timestamp


def _close(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',value):
        raise ValueError('invalid_calendar')
    return time.fromisoformat(value)


def session_calendar(rules_file,window_start,window_end,*,reference):
    """Return quant-spec-v1 calendar fields, with exact preceding close anchor.

    rules_file and all raw documents must be a reviewed host-owned bundle. No
    automatic substitute for missing dates or sources. Publication dates stay
    nullable; current capture does not certify historical calendar availability.
    """
    if not isinstance(reference,datetime) or reference.tzinfo is None:
        raise ValueError('invalid_calendar')
    path=Path(rules_file).resolve()
    rules=json.loads(path.read_text())
    fields={'schema_version','exchange','market_timezone','coverage_start','coverage_end',
            'regular_close','closed_dates','special_closes','source_documents','review_status','reviewed_at'}
    if (not isinstance(rules,dict) or set(rules)!=fields or rules['schema_version']!='exchange-calendar-rules-v1' or
            rules['review_status']!='host_reviewed' or not isinstance(rules['exchange'],str) or
            not re.fullmatch(r'[A-Z0-9_]{1,32}',rules['exchange'])):
        raise ValueError('invalid_calendar')
    zone=ZoneInfo(rules['market_timezone'])
    low,high=validate_date(rules['coverage_start']),validate_date(rules['coverage_end'])
    start,end=validate_date(window_start),validate_date(window_end)
    reviewed=timestamp(rules['reviewed_at'])
    regular=_close(rules['regular_close'])
    if (not low<start<=end<=high or reviewed>reference or
            (date.fromisoformat(high)-date.fromisoformat(low)).days>3660):
        raise ValueError('insufficient_calendar_coverage')
    if (not isinstance(rules['closed_dates'],list) or len(rules['closed_dates'])>2000 or
            any(not isinstance(v,str) for v in rules['closed_dates']) or
            len(set(rules['closed_dates']))!=len(rules['closed_dates']) or
            not isinstance(rules['special_closes'],dict) or len(rules['special_closes'])>2000):
        raise ValueError('invalid_calendar')
    closed={validate_date(v) for v in rules['closed_dates']}
    special={validate_date(day):_close(value) for day,value in rules['special_closes'].items()}
    if any(not low<=day<=high for day in closed|special.keys()):
        raise ValueError('insufficient_calendar_coverage')
    if any(day in closed or date.fromisoformat(day).weekday()>=5 or value>=regular for day,value in special.items()):
        raise ValueError('invalid_calendar')
    sources=rules['source_documents']
    if not isinstance(sources,list) or not 1<=len(sources)<=30:
        raise ValueError('calendar_source_required')
    labels=[];seen=set()
    for source in sources:
        keys={'url','source_file','sha256','publication_date','retrieved_at'}
        if (not isinstance(source,dict) or set(source)!=keys or
                not isinstance(source['url'],str) or not source['url'].startswith('https://') or
                not isinstance(source['source_file'],str) or not source['source_file'] or
                not isinstance(source['sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',source['sha256'])):
            raise ValueError('invalid_calendar')
        relative=Path(source['source_file'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid_calendar')
        document=(path.parent/relative).resolve()
        document.relative_to(path.parent)
        if document in seen or not document.is_file() or document.stat().st_size>20*1024*1024:
            raise ValueError('invalid_calendar')
        seen.add(document)
        if hashlib.sha256(document.read_bytes()).hexdigest()!=source['sha256']:
            raise ValueError('calendar_hash_mismatch')
        captured=timestamp(source['retrieved_at'])
        if captured>reviewed:
            raise ValueError('future_calendar_source')
        publication=source['publication_date']
        if publication is not None and validate_date(publication)>captured.astimezone(zone).date().isoformat():
            raise ValueError('future_calendar_source')
        labels.append(source['url']+'#sha256='+source['sha256'])

    def opened(day):return day.weekday()<5 and day.isoformat() not in closed
    def session(day):
        close=special.get(day.isoformat(),regular)
        return {'trade_date':day.isoformat(),'close_at':datetime.combine(day,close,zone).isoformat()}
    # The complete immediately preceding trading session is the return anchor.
    anchor=date.fromisoformat(start)-timedelta(days=1)
    while anchor.isoformat()>=low and not opened(anchor):anchor-=timedelta(days=1)
    if anchor.isoformat()<low:raise ValueError('insufficient_calendar_coverage')
    sessions=[];day=date.fromisoformat(start)
    while day.isoformat()<=end:
        if opened(day):sessions.append(session(day))
        day+=timedelta(days=1)
    if not 1<=len(sessions)<=2000:raise ValueError('insufficient_calendar_coverage')
    return {'market_timezone':rules['market_timezone'],
            'calendar_source':'host_reviewed '+rules['exchange']+'; rules_sha256='+digest(rules)+'; '+str(path)+'; '+' | '.join(labels),
            'anchor':session(anchor),'sessions':sessions}
