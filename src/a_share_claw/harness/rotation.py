"""Code-owned SW2021 level-1 weekly rotation over a complete frozen price universe."""
from __future__ import annotations
import json,re
from datetime import date
from .contracts import canonical,digest,validate_date


def checked_rotation(rotation,price_spec):
    keys={'schema_version','classification_version','level','market_scope','industries','windows','ranking'}
    if not isinstance(rotation,dict) or set(rotation)!=keys or rotation['schema_version']!='sw-rotation-v1' or rotation['classification_version']!='SW2021' or type(rotation['level']) is not int or rotation['level']!=1 or rotation['market_scope']!=['SH','SZ'] or rotation['ranking']!='competition_12_decimal':raise ValueError('invalid_rotation_spec')
    industries=rotation['industries'];windows=rotation['windows']
    if not isinstance(industries,list) or len(industries)!=31 or not isinstance(windows,list) or not 2<=len(windows)<=52:raise ValueError('invalid_rotation_spec')
    codes=set();symbols=set();names=set();assets={a['symbol']:a for a in price_spec['assets']}
    for row in industries:
        if not isinstance(row,dict) or set(row)!={'industry_code','index_symbol','name'} or not isinstance(row['industry_code'],str) or not re.fullmatch(r'[0-9]{6}',row['industry_code']) or not isinstance(row['index_symbol'],str) or not re.fullmatch(r'801[0-9]{3}\.SW',row['index_symbol']) or not isinstance(row['name'],str) or not row['name'].strip():raise ValueError('invalid_rotation_spec')
        if row['industry_code'] in codes or row['index_symbol'] in symbols or row['name'] in names:raise ValueError('invalid_rotation_spec')
        codes.add(row['industry_code']);symbols.add(row['index_symbol']);names.add(row['name'])
        a=assets.get(row['index_symbol'],{})
        if a.get('name')!=row['name'] or a.get('market_timezone')!='Asia/Shanghai' or a.get('currency')!='CNY' or a.get('unit')!='index_points' or a.get('adjustment')!='NONE':raise ValueError('rotation_identity_mismatch')
    if symbols!=assets.keys():raise ValueError('insufficient_coverage')
    reference=price_spec['assets'][0]
    signature=[reference['anchor'],*reference['sessions']]
    if any([a['anchor'],*a['sessions']]!=signature for a in assets.values()):raise ValueError('non_comparable_calendar')
    days=[s['trade_date'] for s in reference['sessions']];covered=[];prior=None;labels=set()
    for window in windows:
        if not isinstance(window,dict) or set(window)!={'week','start_date','end_date'}:raise ValueError('invalid_rotation_spec')
        start=validate_date(window['start_date']);end=validate_date(window['end_date']);iso=date.fromisoformat(start).isocalendar();label=f'{iso.year}-W{iso.week:02d}'
        if window['week']!=label or date.fromisoformat(end).isocalendar()[:2]!=iso[:2] or start>end or start<price_spec['window_start'] or end>price_spec['window_end'] or prior is not None and start<=prior or label in labels:raise ValueError('invalid_rotation_spec')
        selected=[d for d in days if start<=d<=end]
        if not selected:raise ValueError('insufficient_coverage')
        covered+=selected;labels.add(label);prior=end
    if covered!=days:raise ValueError('insufficient_coverage')
    return json.loads(canonical(rotation))


def checked_classification(rotation,fact,as_of_date):
    keys={'schema_version','classification_version','level','market_scope','industries','effective_date','publication_date','publisher','review_status','source_file','document_sha256'}
    if not isinstance(fact,dict) or set(fact)!=keys or fact['schema_version']!='industry-classification-v1' or type(fact['level']) is not int or any(fact[k]!=rotation[k] for k in ('classification_version','level','market_scope','industries')) or fact['publisher']!='申万宏源研究' or fact['review_status']!='host_reviewed' or not isinstance(fact['source_file'],str) or not fact['source_file'] or not isinstance(fact['document_sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',fact['document_sha256']):raise ValueError('rotation_classification_mismatch')
    if max(validate_date(fact['publication_date']),validate_date(fact['effective_date']))>as_of_date:raise ValueError('future_data')
    return fact


def compute_rotation(spec,prices,classification,as_of_date):
    rotation=checked_rotation(spec['rotation'],spec);classification=checked_classification(rotation,classification,as_of_date)
    if classification['effective_date']>spec['window_start']:raise ValueError('future_data')
    # compute_quant has already admitted every close and exact calendar. This
    # routine consumes that same observation set, never vendor ranks or summaries.
    received={s['symbol']:{r['trade_date']:r['close'] for r in s['rows']} for s in prices['series']}
    days=[s['trade_date'] for s in spec['assets'][0]['sessions']];anchor=spec['assets'][0]['anchor']['trade_date'];previous={};history={r['index_symbol']:[] for r in rotation['industries']}
    for window in rotation['windows']:
        selected=[d for d in days if window['start_date']<=d<=window['end_date']];end=selected[-1]
        returns={symbol:values[end]/values[anchor]-1 for symbol,values in received.items()}
        rounded={s:round(v,12) for s,v in returns.items()}
        ranks={s:1+sum(v>rounded[s] for v in rounded.values()) for s in rounded}
        for symbol in received:
            history[symbol].append({'week':window['week'],'anchor_date':anchor,'last_trade_date':end,'period_return':returns[symbol],'rank':ranks[symbol],'rank_change':None if symbol not in previous else previous[symbol]-ranks[symbol]})
        previous=ranks;anchor=end
    industries=[{**row,'observations':history[row['index_symbol']]} for row in rotation['industries']]
    industries.sort(key=lambda row:(row['observations'][-1]['rank'],row['industry_code']))
    return {'schema_version':'sw-rotation-output-v1','classification_version':'SW2021','level':1,'market_scope':['SH','SZ'],'windows':rotation['windows'],'industries':industries,'ranking':'competition_12_decimal','input_hash':digest(prices),'classification_hash':digest(classification),'classification_source':classification['source_file'],'classification_basis':'synthetic_fixture' if classification['source_file'].startswith('fixture:') else 'host_reviewed_publisher_document','document_sha256':classification['document_sha256'],
        'limitations':['周收益以该周最后交易日/前周最后交易日计算；首周用冻结锚点，短周按实际会话，不补价。','排名按周收益降序，四舍五入12位后同收益同排名；并列采用竞争排名，代码排序只用于展示。','rank_change为前周排名减本周排名，正值表示提升；首周未定义。行业排名仅比较当周31个已准入行业。','分类与指数身份须有宿主复核的申万2021一级原始文件；计算不证明文件真实性或历史PIT，不构成交易动作。']}
