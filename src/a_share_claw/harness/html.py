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


def charts(workflow, data):
    rendered = []
    if workflow == 'mixed':
        return ''.join(charts(c['workflow'], c['data']) for c in data['slices'])
    quant = data if workflow == 'quant' else data.get('market_statistics', {})
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
    title = html.escape(TITLES[report['workflow']]+'报告', quote=True)
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
