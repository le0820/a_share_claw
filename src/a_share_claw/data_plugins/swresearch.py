"""Owner-authorized Shenwan endpoints; no SDK, substitute host or TLS bypass."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit
from datetime import datetime
from zoneinfo import ZoneInfo

from .core import DataError, Manifest, Payload, Provider, iso_date
from .providers import number, parameters

BASE = 'https://www.swsresearch.com/institute-sw/api/index_publish/'


def checked_params(r):
    p = parameters(r, {'symbol', 'start_date', 'end_date'}, {'symbol', 'start_date', 'end_date'})
    if not isinstance(p['symbol'], str) or not re.fullmatch(r'801[0-9]{3}\.SW', p['symbol']):
        raise DataError('invalid_request', 'Use an exact frozen Shenwan index identity')
    start, end = iso_date(p['start_date']), iso_date(p['end_date'])
    if not start <= end <= r.as_of_date or (datetime.fromisoformat(end)-datetime.fromisoformat(start)).days > 1100:
        raise DataError('invalid_request', 'Use a bounded daily window including the frozen anchor')
    return p


class SWResearch(Provider):
    manifest = Manifest('swresearch', ('industry.sw2021_level1_metadata', 'industry.publisher_document',
                                      'market.sw_index_daily_snapshot'),
                        ('www.swsresearch.com', 'wxweb.swsresearch.com'), version='0.2.0')

    def fetch(self, r):
        if r.as_of_date != datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat():
            raise DataError('historical_unavailable', 'Live Shenwan capture requires today; historical vintage is not certified')
        if r.capability == 'industry.publisher_document':
            p = parameters(r, {'url', 'purpose'}, {'url', 'purpose'})
            if not isinstance(p['url'], str) or p['purpose'] not in (
                    'classification_standard', 'index_methodology', 'index_effective_notice'):
                raise DataError('invalid_request', 'Freeze the original publisher PDF and review purpose')
            try:
                parsed = urlsplit(p['url'])
                allowed = (parsed.scheme == 'https' and parsed.hostname == 'wxweb.swsresearch.com'
                           and not parsed.username and not parsed.password and parsed.port in (None, 443)
                           and not parsed.query and not parsed.fragment
                           and re.fullmatch(r'/swsreport/[0-9]{4}_(?:0[1-9]|1[0-2])/[0-9]+\.pdf', parsed.path))
            except ValueError:
                allowed = False
            if not allowed:
                raise DataError('source_denied', 'Only the explicit official report PDF is allowed; no URL discovery or redirects')
            raw = self.transport.get(p['url'])
            if not isinstance(raw, bytes) or not 8 <= len(raw) <= 20_000_000 or not raw.startswith(b'%PDF-'):
                raise DataError('invalid_schema', 'Require bounded original PDF bytes; HTML or an access notice is not a document')
            return Payload({'schema_version': 'publisher-document-capture-v1', 'purpose': p['purpose'],
                            'document_sha256': hashlib.sha256(raw).hexdigest(), 'byte_count': len(raw),
                            'snapshot_as_of_date': r.as_of_date, 'review_status': 'unreviewed',
                            'publication_date': None, 'classification_effective_date': None,
                            'index_effective_date': None, 'classification_version_certified': False},
                           raw, p['url'], 'unverified',
                           ['Capture only, not a parsed or reviewed classification; independently verify every industry/index identity and SH/SZ universe',
                            'Classification launch, report publication and index adjustment dates are distinct; none is inferred from the URL or capture date'])
        if r.capability == 'industry.sw2021_level1_metadata':
            parameters(r, set())
            url = BASE+'current/'
            raw = self.transport.get(url, {'page': 1, 'page_size': 50, 'indextype': '一级行业'})
            obj = json.loads(raw); data = obj['data']
            if (not isinstance(data, dict) or type(data.get('count')) is not int or data['count'] != 31
                    or not isinstance(data.get('results'), list) or len(data['results']) != 31):
                raise DataError('insufficient_coverage', 'Require all 31 native level-one rows; no truncated catalog')
            return Payload({'native_catalog': data, 'snapshot_as_of_date': r.as_of_date,
                            'classification_version_certified': False, 'purpose': 'host_classification_review'},
                           raw, url, 'unverified', ['Current catalog does not prove SW2021 version, effective date, industry-to-index mapping or stock universe; a publisher document and host review remain required'])
        if r.capability != 'market.sw_index_daily_snapshot':
            raise DataError('unsupported_capability', 'Only the authorized classification, publisher-document and daily-index capabilities are supported')
        p = checked_params(r); code = p['symbol'][:-3]; url = BASE+'trend/'
        raw = self.transport.get(url, {'swindexcode': code, 'period': 'DAY'})
        rows = json.loads(raw)['data']
        if not isinstance(rows, list) or not 1 <= len(rows) <= 20000:
            raise DataError('insufficient_coverage', 'Native daily history is empty or exceeds the row bound')
        bars = []; seen = set()
        for row in rows:
            if not isinstance(row, dict) or str(row.get('swindexcode')) != code:
                raise DataError('source_mismatch', 'Native index code differs from the frozen request')
            day = iso_date(row['bargaindate'])
            if not p['start_date'] <= day <= p['end_date']:
                continue
            if day in seen:
                raise DataError('invalid_schema', 'Duplicate native trading day')
            seen.add(day)
            prices = {key: number(row[native]) for key, native in
                      [('open','openindex'), ('high','maxindex'), ('low','minindex'), ('close','closeindex')]}
            if min(prices.values()) <= 0 or not prices['low'] <= min(prices['open'], prices['close']) <= max(prices['open'], prices['close']) <= prices['high']:
                raise DataError('invalid_schema', 'Invalid native OHLC; no repair or zero fill')
            bars.append({'trade_date': day, **prices})
        bars.sort(key=lambda row: row['trade_date'])
        if not bars or bars[0]['trade_date'] != p['start_date'] or bars[-1]['trade_date'] != p['end_date']:
            raise DataError('insufficient_coverage', 'Frozen anchor and last session must both exist')
        return Payload({'symbol': p['symbol'], 'bars': bars, 'unit': 'index_points',
                        'snapshot_as_of_date': r.as_of_date, 'availability_basis': 'observed_current_snapshot',
                        'historical_vintage_certified': False, 'native_code': code}, raw, url, 'verified',
                       ['Native schema/window only; identity/classification and every session require the frozen core contract',
                        'Current capture of historical daily closes; not historical point-in-time availability'])
