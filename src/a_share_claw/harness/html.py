"""Self-contained report UI from evaluated core outputs, without remote assets."""
from __future__ import annotations

import html
import json
import re

from .contracts import canonical, digest
from .markdown import render_markdown, TITLES

VERSION = "html-v1"


def literal(text):
    # Undo only the escaping performed by our own deterministic Markdown renderer.
    return html.escape(html.unescape(re.sub(r"\\([\\`*_{}\[\]()#+.!|>~])", r"\1", text)), quote=True)


def markdown_body(report):
    lines = render_markdown(report).splitlines()
    parts, index = [], 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("| "):
            rows = []
            while index < len(lines) and lines[index].startswith("| "):
                rows.append(re.split(r"(?<!\\) \| ", lines[index][2:-2]))
                index += 1
            parts.append('<div class="table-scroll"><table><thead><tr>' + ''.join('<th>'+literal(v)+'</th>' for v in rows[0]) + '</tr></thead><tbody>')
            parts.extend('<tr>'+''.join('<td>'+literal(v)+'</td>' for v in row)+'</tr>' for row in rows[2:])
            parts.append('</tbody></table></div>')
            continue
        heading = re.match(r"^(#{1,6}) (.*)$", line)
        if heading:
            level = len(heading[1])
            parts.append(f'<h{level}>{literal(heading[2])}</h{level}>')
        elif line.startswith('- '):
            parts.append('<p class="bullet">• '+literal(line[2:])+'</p>')
        elif line:
            parts.append('<p>'+literal(line)+'</p>')
        index += 1
    return '\n'.join(parts)


def line_chart(title, points, unit, note):
    """Date/value observations; exact values remain accessible beside the plot."""
    if not points:
        return ''
    values = [v for _, v in points]
    low, high = min(values), max(values)
    pad = (high-low)*.1 if high != low else max(abs(high)*.01, .01)
    low, high = low-pad, high+pad
    coords = [(60 + i*700/max(len(points)-1, 1), 220-(v-low)*180/(high-low)) for i, (_, v) in enumerate(points)]
    path = ' '.join(f'{x:.3f},{y:.3f}' for x, y in coords)
    escape = lambda v: html.escape(str(v), quote=True)
    dots = ''.join(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="3"><title>{escape(d)}: {escape(v)} {escape(unit)}</title></circle>' for (d,v),(x,y) in zip(points, coords))
    rows = ''.join(f'<tr><td>{escape(d)}</td><td>{escape(v)}</td></tr>' for d,v in points)
    return f'''<section class="chart"><h3>{escape(title)}</h3><p>{escape(note)}</p>
<svg viewBox="0 0 820 270" role="img" aria-label="{escape(title)}，单位 {escape(unit)}">
<text x="6" y="40">{high:.4g}</text><text x="6" y="220">{low:.4g}</text>
<path d="M60 40 V220 H760" fill="none" stroke="#7a8b9c"/>
<polyline points="{path}" fill="none" stroke="#175cd3" stroke-width="2"/>{dots}
<text x="60" y="250">{escape(points[0][0])}</text><text x="760" y="250" text-anchor="end">{escape(points[-1][0])}</text></svg>
<details><summary>原始图表数值（{escape(unit)}）</summary><table><thead><tr><th>日期/期间</th><th>数值</th></tr></thead><tbody>{rows}</tbody></table></details></section>'''


def flow_chart(data):
    c=data['flow_chart'];values=c['values'];scale=max([abs(v['value']) for v in values]+[1]);escape=lambda v:html.escape(str(v),quote=True)
    pending=sum(len(v['unfinalized_native_zero_quotes']) for v in data['series_audit'].values())
    disclosure=('<p>当前捕获快照：'+str(pending)+' 条原生零值未确认收盘更新；全市场汇总未认证最终收盘成交完整性，逐条审计见下文。</p>') if pending else ''
    zeros=[m for m,v in data['series_audit'].items() if v['uniform_zero_net_with_positive_turnover']]
    if zeros:disclosure+='<p>'+escape(','.join(zeros))+' 全部主力净额为原生零值但有成交额；字段支持待确认，不代表真实买卖平衡，ALL同受此限制。</p>'
    scope_name='沪深两市' if data['specification'].get('markets')==['SH','SZ'] else '三市'
    aria_label='沪深两市及观察范围汇总主力净额估计' if scope_name=='沪深两市' else '三市及全市场主力净额估计'
    bars=[];rows=[]
    for i,row in enumerate(values):
        y=42+i*48;v=row['value'];width=abs(v)/scale*280;x=410 if v>=0 else 410-width;color='#c7383e' if v>=0 else '#11856a'
        bars.append(f'<text x="8" y="{y+17}">{escape(row["market"])}</text><rect x="{x}" y="{y}" width="{width}" height="26" fill="{color}"><title>{escape(v)} CNY</title></rect><text x="710" y="{y+17}">{v/1e8:.3f}亿</text>')
        rows.append(f'<tr><td>{escape(row["market"])}</td><td>{escape(v)}</td></tr>')
    return '<section class="chart"><h3>资金流向：供应商主力净额估计</h3><p>'+escape(c['observation_date'])+' · 单位亿元；正值向右，负值向左。ALL为'+scope_name+'汇总，不能与分市场重复相加。</p>'+disclosure+'<svg viewBox="0 0 840 260" role="img" aria-label="'+aria_label+'"><path d="M410 25 V245" stroke="#7a8b9c"/>'+''.join(bars)+'</svg><details><summary>原始金额（元）</summary><table><thead><tr><th>范围</th><th>元</th></tr></thead><tbody>'+''.join(rows)+'</tbody></table></details></section>'


def charts(workflow, data):
    rendered = []
    if workflow == 'mixed':
        return ''.join(charts(c['workflow'], c['data']) for c in data['slices'])
    quant = data if workflow == 'quant' else data.get('market_statistics', {})
    if quant.get("schema_version")=="flow-output-v1":rendered.append(flow_chart(quant))
    for symbol, series in quant.get('chart_series', {}).items():
        rendered.append(line_chart(symbol+' / '+series['name'], [(r['trade_date'],r['close']) for r in series['rows']], series['unit'],
            '本地币种原生收盘价；来源：'+series['source_file']+'。当前捕获版本不证明历史 PIT；无分红、费用或汇率换算。'))
    for group in data.get('monthly_history', {}).get('comparisons', []):
        rendered.append(line_chart(group['entity']+'/'+group['metric'], [(r['period'],r['value']) for r in group['observations']], 'percent',
            group['basis']+'；'+group['comparison_scope']+'。相邻公布率对照；两点不能认证持续趋势。'))
    return ''.join(rendered)


def render_html(report):
    report = json.loads(canonical(report))
    if report.get('html_version') != VERSION:
        raise ValueError('report_contract_failure')
    title = html.escape(('资金流向（主力口径）' if report['data'].get('schema_version')=='flow-output-v1' else TITLES[report['workflow']])+'报告', quote=True)
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; base-uri 'none'; form-action 'none'">
<title>{title}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#edf2f7;color:#172b4d;font:16px/1.6 system-ui,sans-serif}}main{{max-width:1200px;margin:auto;padding:24px}}header,.chart{{background:white;padding:24px;border-radius:12px;margin:20px 0}}h1,h2,h3{{line-height:1.3}}h2{{margin-top:32px;border-bottom:2px solid #d8e3ef;padding-bottom:10px}}.table-scroll{{overflow-x:auto;background:white;margin:16px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{border:1px solid #d8e3ef;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}}th{{background:#e7eff9}}p{{overflow-wrap:anywhere}}.badge{{color:#174ea6;font-weight:600}}svg{{width:100%;height:auto;display:block}}svg text{{font:12px system-ui;fill:#172b4d}}circle{{fill:#175cd3}}details{{margin-top:12px}}@media print{{body{{background:white}}main{{padding:0}}.table-scroll{{overflow:visible}}details{{display:block}}.chart{{break-inside:avoid}}}}
</style></head><body><main><header><h1>{title}</h1><div class="badge">投研 Harness · 归档产物 staged</div><p>本文件以已评估的 JSON 报告生成；发布权限以授权读取的运行终态为准。</p><p>报告哈希：{digest(report)}</p></header>
{charts(report['workflow'],report['data'])}{markdown_body(report)}</main></body></html>'''


def validate_html(rendered, report):
    if not isinstance(rendered, str) or rendered != render_html(report):
        raise ValueError('report_contract_failure')
