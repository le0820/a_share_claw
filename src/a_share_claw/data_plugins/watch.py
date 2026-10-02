"""Trusted host binds the frozen four-index core contract to primary source facts."""
from __future__ import annotations

from ..harness.contracts import validate_date
from ..harness.market_watch import WATCH_INDEXES
from ..harness.quant import checked_quant_spec
from .core import DataError
from .tdx import INDEXES


def watch_source_contract(spec,as_of_date):
    spec=checked_quant_spec(spec);day=validate_date(as_of_date)
    if [a['symbol'] for a in spec['assets']]!=list(WATCH_INDEXES):
        raise DataError('source_mismatch','Watch binding requires the exact frozen four-index universe')
    requirements=[]
    for i,a in enumerate(spec['assets']):
        native=INDEXES[a['symbol']]
        if any(a[k]!=native[k] for k in ('name','currency','unit','market_timezone')) or a['adjustment']!='none':
            raise DataError('source_mismatch','Core economic identity must match the independently reviewed native source')
        requirements.append({'requirement_id':'watch_'+str(i),'provider':'easytdx','capability':'market.index_daily_snapshot',
            'as_of_date':day,'params':{'symbol':a['symbol'],'provider_code':native['code'],
                'start_date':a['anchor']['trade_date'],'end_date':spec['window_end'],'count':600},'required':True})
    return {'source_plan':{'framework':'Native four-index daily visualization; fixed identity/calendars/window before acquisition; no ETFs or index-family substitutions.',
        'requirements':requirements},'bindings':[],
        'price_bindings':[{'symbol':r['params']['symbol'],'requirement_id':r['requirement_id']} for r in requirements],
        'cutoff_timestamp':spec['cutoff_timestamp']}
