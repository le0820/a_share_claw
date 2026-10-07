"""Code-owned four-weight pressure model and scoped, script-free five-chart view.

This descriptive research view never supplies official policy/scoring inputs.
Provider access is exclusively owned by an explicitly configured host adapter.
"""
from __future__ import annotations
import math
from datetime import date,datetime
from html import escape
from statistics import median
from .contracts import canonical,digest

WEIGHTS={'demand':.4,'cash_flow':.3,'external_financing':.25,'external_constraints':.05}
CHARTS=(
 ('cn_consumption','消费行业、恒生科技与中国经济金融',('食品饮料','家用电器','商贸零售','纺织服饰','轻工制造','社会服务','美容护理','汽车','恒生科技','工业增加值同比','社零同比','固定资产投资累计同比','M2同比','GDP同比')),
 ('cn_ai','通信、半导体与 AI 四权重压力',('通信','半导体','AI压力')),
 ('us_ai','费城半导体、纳斯达克100与 AI 四权重压力',('费城半导体','纳斯达克100','AI压力')),
 ('banks_rates','银行、中美10年国债利差与10年 TIPS',('银行','中美10年国债利差','10年TIPS')),
 ('us_real_inflation','标普500、核心 PCE 指数与10年 TIPS',('标普500','核心PCE价格指数','10年TIPS')),
)

def finite(value):
 return type(value) in (int,float) and math.isfinite(value)

def score(value,history,*,polarity=1,minimum=8,window=12):
 """Prior observations only; zero dispersion or incomplete warm-up stays missing."""
 if not finite(value) or len(history)<minimum:return None
 history=history[-window:]
 if any(not finite(x) for x in history):raise ValueError('non_finite_pressure_history')
 center=median(history);scale=1.4826*median([abs(x-center) for x in history])
 if scale<=0:return None
 z=max(-3,min(3,(value-center)/scale))
 return max(0,min(100,50+50/3*polarity*z))

def pressure(scores):
 if set(scores)!=set(WEIGHTS):raise ValueError('pressure_components_required')
 if any(v is None for v in scores.values()):return {'total':None,'contributions':{k:None if scores[k] is None else scores[k]*w for k,w in WEIGHTS.items()}}
 if any(not finite(v) or not 0<=v<=100 for v in scores.values()):raise ValueError('invalid_pressure_score')
 c={k:scores[k]*w for k,w in WEIGHTS.items()}
 return {'total':sum(c.values()),'contributions':c}

def missing_packet(scope_key,run_id,as_of_date,reason='未配置授权的五图数据适配器'):
 return {'schema_version':'five-charts-v1','scope_key':scope_key,'run_id':run_id,'as_of_date':as_of_date,'generated_at':datetime.now().astimezone().isoformat(),'action':'NO_ACTION','historical_vintage_certified':False,'series':[], 'sources':[], 'gaps':[{'chart':cid,'metric':metric,'reason':reason} for cid,_,metrics in CHARTS for metric in metrics]}

def validate_packet(packet,scope_key,run_id,as_of_date):
 if packet.get('schema_version')!='five-charts-v1' or packet.get('scope_key')!=scope_key or packet.get('run_id')!=run_id or packet.get('as_of_date')!=as_of_date:raise ValueError('five_chart_scope_or_date_mismatch')
 if packet.get('action')!='NO_ACTION' or packet.get('historical_vintage_certified') is not False:raise ValueError('five_chart_research_only')
 if datetime.fromisoformat(packet['generated_at']).tzinfo is None:raise ValueError('five_chart_capture_clock_required')
 if packet.get('refresh_started_at'):
  started=datetime.fromisoformat(packet['refresh_started_at']);generated=datetime.fromisoformat(packet['generated_at'])
  if started.tzinfo is None or generated<started:raise ValueError('invalid_chart_refresh_clock')
  for source in packet['sources']:
   captured=datetime.fromisoformat(source['retrieved_at'])
   if source.get('acquisition_kind')=='scoped_archive':
    reused=datetime.fromisoformat(source['reused_at'])
    if source.get('scope_key')!=scope_key or not source.get('source_origin_run_id') or not captured<=reused or not started<=reused<=generated:raise ValueError('unauthorized_chart_archive_reuse')
   elif not started<=captured<=generated:raise ValueError('stale_chart_capture')
 sources={s['id']:s for s in packet['sources']}
 if len(sources)!=len(packet['sources']):raise ValueError('duplicate_chart_source')
 for s in sources.values():
  if len(s['sha256'])!=64 or datetime.fromisoformat(s['retrieved_at']).tzinfo is None:raise ValueError('invalid_chart_source')
 names=set()
 for series in packet['series']:
  if series['name'] in names:raise ValueError('duplicate_chart_series')
  names.add(series['name']);last=None
  if not series.get('unit') or not series.get('basis'):raise ValueError('chart_metric_basis_required')
  for row in series['points']:
   d=row['date'];date.fromisoformat(d)
   if as_of_date is None or d>as_of_date or d<'2025-01-01' or last is not None and d<=last:raise ValueError('invalid_chart_observation_date')
   if row['value'] is not None and not finite(row['value']):raise ValueError('invalid_chart_value')
   if row.get('release_date') and row['release_date']>as_of_date:raise ValueError('future_chart_release')
   if not row.get('source_ids') or any(x not in sources for x in row['source_ids']):raise ValueError('chart_source_binding_required')
   last=d
 gaps={(x['chart'],x['metric']) for x in packet['gaps']}
 for cid,_,metrics in CHARTS:
  for metric in metrics:
   series=next((s for s in packet['series'] if s['name']==metric),None)
   if (not series or not any(r['value'] is not None for r in series['points'])) and (cid,metric) not in gaps:raise ValueError('silent_chart_gap')
 canonical(packet)
 return packet

PALETTE=('#0369a1','#7c3aed','#059669','#db2777','#ca8a04','#dc2626','#0891b2','#4f46e5','#475569','#a16207','#0f766e','#9333ea','#be123c','#111827')

def svg_panel(series,start,end,*,reverse=False,fixed=None,secondary=()):
 """Real calendar-date x axis, separate native units, gaps break the path."""
 left,right,top,bottom=72,965,30,260
 usable=[r['value'] for s in series for r in s['points'] if r['value'] is not None]
 if not usable:return '<p class="gap">该面板缺少可绘制的原生观测。</p>'
 lo,hi=fixed or (min(usable),max(usable));pad=(hi-lo)*.06 or 1
 if fixed is None:lo-=pad;hi+=pad
 span=max(1,(date.fromisoformat(end)-date.fromisoformat(start)).days)
 def x(d):return left+(date.fromisoformat(d)-date.fromisoformat(start)).days/span*(right-left)
 secondary_values=[r['value'] for ss in secondary for r in ss['points'] if r['value'] is not None]
 secondary_pressure=bool(secondary) and secondary[0]['unit']=='pressure_0_100'
 secondary_lo,secondary_hi=(0,100) if secondary_pressure else (min(secondary_values),max(secondary_values)) if secondary_values else (0,1)
 if secondary_lo==secondary_hi:secondary_lo-=1;secondary_hi+=1
 def secondary_y(v):return top+(v-secondary_lo)/(secondary_hi-secondary_lo)*(bottom-top) if secondary_pressure else bottom-(v-secondary_lo)/(secondary_hi-secondary_lo)*(bottom-top)
 def y(v):return top+(v-lo)/(hi-lo)*(bottom-top) if reverse else bottom-(v-lo)/(hi-lo)*(bottom-top)
 lines=['<svg viewBox="0 0 1100 320" role="img" aria-label="按真实日期对齐的折线图">']
 for i in range(5):
  v=lo+(hi-lo)*i/4;py=y(v)
  lines.append(f'<line x1="{left}" y1="{py:.2f}" x2="{right}" y2="{py:.2f}" stroke="#e2e8f0"/><text x="65" y="{py+4:.2f}" text-anchor="end" font-size="12">{v:.2f}</text>')
 for i in range(7):
  ordinal=date.fromisoformat(start).toordinal()+round(span*i/6);d=date.fromordinal(ordinal).isoformat();px=x(d)
  lines.append(f'<text x="{px:.2f}" y="285" text-anchor="middle" font-size="11">{d}</text>')
 if secondary:
  for i in range(5):
   value=secondary_lo+(secondary_hi-secondary_lo)*i/4;py=secondary_y(value)
   lines.append(f'<text x="975" y="{py+4:.2f}" font-size="12">{value:.2f}</text>')
  lines.append('<text x="975" y="15" font-size="11">'+escape('压力（逆序）' if secondary_pressure else '收益率% / 利差百分点')+'</text>')
 for s in [*series,*secondary]:
  ordinate=secondary_y if s in secondary else y
  color=PALETTE[s.get('color_index',0)%len(PALETTE)];parts=[];pen=False;previous=None
  for r in s['points']:
   value=r['value']
   if value is None:pen=False;previous=None;continue
   if s.get('step') and pen:parts.append(f'L{x(r["date"]):.2f},{ordinate(previous):.2f}')
   parts.append(f'{"L" if pen else "M"}{x(r["date"]):.2f},{ordinate(value):.2f}');pen=True;previous=value
  lines.append(f'<path d="{" ".join(parts)}" fill="none" stroke="{color}" stroke-width="2"><title>{escape(s["name"])}</title></path>')
 lines.append('</svg>')
 return ''.join(lines)

def render(packet):
 esc=lambda x:escape(str(x));lookup={s['name']:s for s in packet['series']}
 body=['<section class="five-charts"><h1>五图联动研究</h1><p>起点 2025-01；截至 '+esc(packet['as_of_date'])+'；生成 '+esc(packet['generated_at'])+'。仅研究 · NO_ACTION · 当前捕获不证明历史修订版本；原生历史发布复用保留原捕获时间，并显式标注同Scope归档复用。</p>']
 body.append('<p>价格序列以各自首个有效观测=100，日期保留原市场日；经济数据在原生统计期间绘制，非日线插值。核心PCE保留 BEA 原生2017=100水平。AI压力越高表示资本周期压力越大；采用参考图40/30/25/5权重，自建财务代理公式，并非中金原指数复刻。</p>')
 body.append('<table><thead><tr><th>分项</th><th>权重</th><th>本框架指标</th><th>原生数源</th><th>压力方向</th></tr></thead><tbody><tr><td>需求</td><td>40%</td><td>NVIDIA数据中心TTM收入同比</td><td>NVIDIA原生财报发布</td><td>增速降低 → 压力增加</td></tr><tr><td>现金流</td><td>30%</td><td>AMZN/MSFT/GOOG合计现金Capex ÷ 合计OCF</td><td>SEC原生发行人财务概念</td><td>比例增加 → 压力增加</td></tr><tr><td>外部融资</td><td>25%</td><td>三家原生现金债务发行额TTM ÷ 合计现金Capex</td><td>SEC；现金发行包括再融资，GOOG为扣发行成本</td><td>比例增加 → 压力增加</td></tr><tr><td>外部约束</td><td>5%</td><td>美国10年TIPS平价实际收益率</td><td>美国财政部</td><td>收益率增加 → 压力增加</td></tr></tbody></table><p>该模型为美国代表性公司的资本压力代理：现金Capex和发行现金并非AI专属，不含未计入这些概念的SPV/租赁融资；实际收入不等同物理需求，不能据此推断全行业供需缺口。</p>')
 for cid,title,names in CHARTS:
  body.append(f'<section id="{cid}" class="chart-card"><h2>{esc(title)}</h2>')
  selected=[]
  for i,name in enumerate(names):
   if name in lookup:selected.append({**lookup[name],'color_index':i})
  gaps=[g for g in packet['gaps'] if g['chart']==cid]
  for gap in gaps:body.append('<p class="gap">'+esc(gap['metric'])+'：'+esc(gap['reason'])+'</p>')
  # Index/pressure and bank/rates use one dated graphic with explicit second axis.
  end=packet['as_of_date'] or '2025-01-01'
  if cid in {'cn_ai','us_ai','banks_rates'}:
   primary=[ss for ss in selected if ss['unit']=='price_first_observation_100'];secondary=[ss for ss in selected if ss['unit']!='price_first_observation_100']
   body.append('<p>左轴：各自首个观测=100；右轴：'+('AI压力0—100，逆序' if cid in {'cn_ai','us_ai'} else '收益率%／中美利差百分点')+'</p><div class="legend">'+''.join('<span style="color:'+PALETTE[ss['color_index']%len(PALETTE)]+'">● '+esc(ss['name'])+'</span>' for ss in selected)+'</div>')
   body.append(svg_panel(primary,'2025-01-01',end,secondary=secondary))
  else:
   for unit in dict.fromkeys(ss['unit'] for ss in selected):
    group=[ss for ss in selected if ss['unit']==unit]
    body.append('<h3>'+esc(unit)+'</h3><div class="legend">'+''.join('<span style="color:'+PALETTE[ss['color_index']%len(PALETTE)]+'">● '+esc(ss['name'])+'</span>' for ss in group)+'</div>')
    body.append(svg_panel(group,'2025-01-01',end))
  if cid in {'cn_ai','us_ai'} and 'AI压力' in lookup:
   body.append(contribution_svg(lookup['AI压力']['points'],'2025-01-01',end))
  body.append('<details><summary>数据、定义与日期</summary>')
  for s in selected:
   body.append('<h4>'+esc(s['name'])+'</h4><p>'+esc(s['basis'])+'</p><table><thead><tr><th>观测日期</th><th>数值</th><th>公布日期</th><th>来源</th></tr></thead><tbody>')
   for r in s['points']:body.append('<tr><td>'+esc(r['date'])+'</td><td>'+esc(r['value'] if r['value'] is not None else 'N/A')+'</td><td>'+esc(r.get('release_date',''))+'</td><td>'+esc(', '.join(r['source_ids']))+'</td></tr>')
   body.append('</tbody></table>')
  body.append('</details></section>')
 body.append('<details><summary>原始来源与捕获审计</summary><table><tr><th>来源</th><th>链接</th><th>捕获日期</th><th>SHA256</th></tr>')
 for s in packet['sources']:
  # URLs are textual audit evidence, never executable content.
  body.append('<tr><td>'+esc(s['id'])+'</td><td>'+esc(s.get('url',s.get('endpoint','')))+' </td><td>'+esc(s['retrieved_at'])+(' · 同Scope归档复用于 '+esc(s['reused_at']) if s.get('acquisition_kind')=='scoped_archive' else ' · 本次捕获')+'</td><td>'+esc(s['sha256'])+'</td></tr>')
 body.append('</table></details><p>Scope '+esc(packet['scope_key'])+' · Run '+esc(packet['run_id'])+' · 五图数据哈希 '+digest(packet)+'</p></section>')
 return '<style>.five-charts{max-width:1200px;margin:auto}.five-charts p{overflow-wrap:anywhere}.chart-card{background:white;padding:18px;margin:22px 0;border:1px solid #cbd5e1;border-radius:12px}.five-charts svg{width:100%;min-width:600px}.chart-card{overflow:auto}.legend{display:flex;flex-wrap:wrap;gap:12px}.gap{color:#9a3412}.five-charts table{font-size:12px;border-collapse:collapse}.five-charts td,.five-charts th{padding:5px;border-bottom:1px solid #e2e8f0}</style>'+''.join(body)


def contribution_svg(points,start,end):
 """Stacked weighted contributions sum exactly to the pressure total."""
 colors=('#bfdbfe','#1e3a8a','#d1d5db','#6b7280');labels=('需求40%','现金流30%','外部融资25%','外部约束5%');keys=tuple(WEIGHTS)
 span=max(1,(date.fromisoformat(end)-date.fromisoformat(start)).days)
 x=lambda d:72+(date.fromisoformat(d)-date.fromisoformat(start)).days/span*893
 y=lambda v:260-v/100*230
 groups=[];current=[]
 for row in points:
  if row['value'] is None:
   if current:groups.append(current);current=[]
  else:current.append(row)
 if current:groups.append(current)
 svg=['<h3>AI压力四项加权贡献</h3><p>'+ ' · '.join(labels)+'</p><svg viewBox="0 0 1100 320" role="img" aria-label="AI压力四项加权贡献堆叠图">']
 for i in range(5):
  v=i*25;svg.append(f'<line x1="72" y1="{y(v)}" x2="965" y2="{y(v)}" stroke="#e2e8f0"/><text x="65" y="{y(v)+4}" text-anchor="end" font-size="12">{v}</text>')
 for group in groups:
  lower=[0]*len(group)
  for k,color,label in zip(keys,colors,labels):
   upper=[base+row['contributions'][k] for base,row in zip(lower,group)];top_points=[];bottom_points=[]
   for j,row in enumerate(group):
    if j:top_points.append((x(row['date']),y(upper[j-1])));bottom_points.append((x(row['date']),y(lower[j-1])))
    top_points.append((x(row['date']),y(upper[j])));bottom_points.append((x(row['date']),y(lower[j])))
   polygon=top_points+list(reversed(bottom_points));svg.append('<polygon points="'+' '.join(f'{a:.2f},{b:.2f}' for a,b in polygon)+'" fill="'+color+'"><title>'+label+'</title></polygon>');lower=upper
  path=[]
  for j,row in enumerate(group):
   if j:path.append(f'L{x(row["date"]):.2f},{y(group[j-1]["value"]):.2f}')
   path.append(f'{"L" if j else "M"}{x(row["date"]):.2f},{y(row["value"]):.2f}')
  svg.append('<path d="'+' '.join(path)+'" fill="none" stroke="#dc2626" stroke-width="2"/>')
 svg.append('<text x="72" y="288" font-size="12">'+escape(start)+'</text><text x="965" y="288" text-anchor="end" font-size="12">'+escape(end)+'</text></svg>')
 return ''.join(svg)
