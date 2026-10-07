"""Frozen four-index visualization contract. Calendars are trusted host inputs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .calendar import session_calendar
from .contracts import validate_date
from .quant import checked_quant_spec, timestamp

WATCH_INDEXES = ('SPX.SP500','NDX.NASDAQ','000300.SH','399006.SZ')
# Core identity and economic meaning are owned here, independently of the provider.
ASSETS = {
    'SPX.SP500':{'name':'S&P 500','currency':'USD','market_timezone':'America/New_York','exchange':'XNYS'},
    'NDX.NASDAQ':{'name':'NASDAQ 100','currency':'USD','market_timezone':'America/New_York','exchange':'XNAS'},
    '000300.SH':{'name':'沪深300','currency':'CNY','market_timezone':'Asia/Shanghai','exchange':'XSHG'},
    '399006.SZ':{'name':'创业板指','currency':'CNY','market_timezone':'Asia/Shanghai','exchange':'XSHE'},
}


def freeze_watch(as_of_date,window_start,window_end,cutoff_timestamp,calendar_files,*,reference=None):
    """Create host-owned spec/source bindings before any SDK/network activity."""
    day=validate_date(as_of_date);start=validate_date(window_start);end=validate_date(window_end)
    cutoff=timestamp(cutoff_timestamp)
    if not start<=end<=day or set(calendar_files)!={'XNYS','XNAS','XSHG','XSHE'}:
        raise ValueError('invalid_watch_contract')
    assets=[]
    for symbol in WATCH_INDEXES:
        identity=ASSETS[symbol]
        rules_file=Path(calendar_files[identity['exchange']])
        if json.loads(rules_file.read_text())['exchange']!=identity['exchange']:
            raise ValueError('watch_calendar_identity_mismatch')
        calendar=session_calendar(rules_file,start,end,reference=reference or datetime.now(timezone.utc))
        if calendar['market_timezone']!=identity['market_timezone'] or identity['exchange'] not in calendar['calendar_source']:
            raise ValueError('watch_calendar_identity_mismatch')
        if timestamp(calendar['sessions'][-1]['close_at'])>cutoff:
            raise ValueError('price_before_close')
        assets.append({'symbol':symbol,**{k:identity[k] for k in ('name','currency','market_timezone')},
            'unit':'index_points','adjustment':'none',**calendar})
    spec=checked_quant_spec({'schema_version':'quant-spec-v1','operation':'price_statistics','frequency':'daily',
        'window_start':start,'window_end':end,'cutoff_timestamp':cutoff.isoformat(),'assets':assets,'benchmark':'000300.SH',
        'metrics':['period_return','max_drawdown','annualized_volatility','excess_return'],'annualization_factor':252})
    return {'schema_version':'market-watch-host-v1','as_of_date':day,'workflow':'quant','mode':'research',
            'quant_spec':spec,'action':'NO_ACTION',
            'limitations':['S&P 500 calendar uses the host-reviewed XNYS regular session as the selected daily reference; this does not certify the index publisher timestamp.',
                'US and China closes are not synchronous; local-currency price returns exclude dividends, FX and fees.',
                'Current native captures do not prove historical availability or revision vintage.']}
