"""Authorized offline HTML workbench: terminal status, public ReAct trace and reports."""
from __future__ import annotations

import html
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4

from .contracts import canonical, digest, validate_date
from .delivery import read_artifact, read_report
from .trace import now

VERSION = 'workbench-v1'
STYLE = '''*{box-sizing:border-box}body{margin:0;background:#eef3f8;color:#152d4d;font:16px/1.6 system-ui,sans-serif}main{max-width:1160px;margin:auto;padding:24px}header,.card,details{background:white;border:1px solid #dbe5ef;border-radius:12px;padding:18px;margin:16px 0}h1,h2,h3{line-height:1.3}h2{margin-top:32px}a{color:#175cd3}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #dbe5ef;padding:10px;text-align:left;vertical-align:top;overflow-wrap:anywhere}.scroll{overflow:auto}th{background:#edf3fb}.badge{border-radius:8px;padding:3px 8px;background:#e8f0fb;font-weight:600}.failed,.blocked,.error,.rejected,.cancelled,.interrupted{color:#a12629;background:#ffefed}.running{color:#76530f;background:#fff6da}.succeeded,.ok{color:#076647;background:#e7f8f1}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}summary{cursor:pointer;font-weight:600}.decision{border-left:4px solid #175cd3;padding-left:14px}.muted{color:#52657c}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px}.card{margin:0}small{overflow-wrap:anywhere}.timeline{border-left:2px solid #bfd3ed;padding-left:18px}.end{margin-left:20px}@media(max-width:600px){main{padding:12px}th,td{padding:6px}}'''


def esc(value):
    return html.escape(str(value),quote=True)


def page(title, body):
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"><title>{esc(title)}</title><style>{STYLE}</style></head><body><main>{body}</main></body></html>'''


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+esc(v)+'</th>' for v in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'


def snapshot(repository, scope, run_id, artifact_root, *, as_of_date=None):
    trace = repository.read(run_id,scope)
    if as_of_date is not None:
        cutoff=validate_date(as_of_date)
        if trace['request']['as_of_date'] is None or trace['request']['as_of_date']>cutoff:
            raise LookupError('view_not_found')
    plan = None
    if any(Path(v['detail'].get('path','')).name=='plan.json' for v in trace['artifacts']):
        raw,_=read_artifact(trace,scope,artifact_root,'plan.json')
        plan=json.loads(raw)
        if plan.get('scope_key')!=scope.key or plan.get('as_of_date')!=trace['request']['as_of_date']:
            raise ValueError('view_integrity_error')
    delivery=None
    # Failed/staged output is never exposed as an evaluated business report.
    has_report=any(v['detail']['evaluator']=='report_contract' for v in trace['evaluations'])
    if trace['status']=='succeeded' and has_report:
        delivery=read_report(repository,scope,run_id,artifact_root,as_of_date=as_of_date)
    return {'schema_version':VERSION,'scope_key':scope.key,'captured_at':now(),
            'trace':trace,'plan':plan,'delivery':delivery}


def render_run(view):
    trace,plan,delivery=view['trace'],view['plan'],view['delivery']
    status=trace['status'];run_id=trace['run_id']
    outcome=trace['outcome'] or {'action':'NO_ACTION','official_output_allowed':False}
    public_status = '已正式发布' if delivery and delivery['official_output_allowed'] else '已评估研究报告' if delivery else '规划/缺口/运行诊断；未完成业务报告'
    body='<header><a href="index.html">← 运行列表</a><h1>投研运行工作台</h1><p><span class="badge '+esc(status)+'">'+esc(status)+'</span> '+esc(public_status)+'</p><p>run_id：'+esc(run_id)+'</p><p>Scope：'+esc(view['scope_key'])+'</p><p>快照时间：'+esc(view['captured_at'])+'；运行状态以本次授权读取为准。</p></header>'
    if delivery:
        body+='<p class="decision">发布状态：'+esc(delivery['publication_status'])+'；行动：'+esc(delivery['action'])+'</p>'
        if delivery['html'] is not None:
            body+='<p><a href="'+esc(run_id)+'.report.html">打开已校验报告与图表</a></p>'
        else:
            body+='<p>旧版归档没有 HTML 文件；已校验 Markdown 见下方。不会改写历史归档。</p>'
            body+='<details><summary>已校验 Markdown 报告</summary><pre>'+esc(delivery['markdown'])+'</pre></details>'
    else:
        body+='<p class="decision">此页呈现规划与缺口或失败诊断；行动 NO_ACTION。阶段成功、工具成功或规划完成均不表示业务研究完成。</p>'
    body+='<h2>运行口径</h2>'+table(['字段','值'],[(k,v) for k,v in trace['request'].items() if k not in {'message_hash'}]+[('started_at',trace['started_at']),('finished_at',trace['finished_at']),('terminal_action',outcome['action'])])
    if plan:
        body+='<h2>冻结框架与数据需求</h2><p>'+esc(plan['framework'])+'</p>'+table(['需求','能力','性质','日期'],[(r['requirement_id'],r['capability'],r['disposition'],r['as_of_date']) for r in plan['requirements']])
        if plan.get('unresolved_constraints'):
            body+='<h3>未解决的规划约束</h3><pre>'+esc(canonical(plan['unresolved_constraints']))+'</pre>'
        if plan.get('parameters'):
            body+='<details><summary>冻结计算规格：标的、窗口、日历、单位与指标</summary><pre>'+esc(json.dumps(plan['parameters'],ensure_ascii=False,indent=2))+'</pre></details>'
        body+='<p>policy-disabled：'+esc(canonical(plan['disabled_layers']))+'</p><p>plan_id：'+esc(plan['plan_id'])+'</p>'
    gaps=[r['detail'] for r in trace['run_steps'] if r['stage'] in {'gap_report','source_acquisition'}]
    body+='<h2>缺口与评价门禁</h2>'
    if gaps:
        body+='<pre>'+esc(json.dumps(gaps,ensure_ascii=False,indent=2))+'</pre>'
    body+=table(['评价','通过','硬门禁','代码'],[(r['detail']['evaluator'],bool(r['passed']),bool(r['hard_gate']),r['detail'].get('code')) for r in trace['evaluations']])
    body+='<h2>ReAct 执行时间轴</h2><p>公开决策摘要 → 行动 → 观测。模型原始候选与私有思维链不进入此时间轴；哈希可关联授权归档。</p><div class="timeline">'
    rows=[r for r in trace['run_steps'] if r['stage'] in {'react_phase','react_action'}]
    if not rows:
        body+='<p>旧版运行没有边界记录，不能从日志推造开始/结束时间。</p>'
    for row in rows:
        d=row['detail'];boundary=d['boundary'];label=d.get('operation',d.get('phase','unknown'))
        body+='<details class="'+('end' if boundary=='end' else 'start')+'"><summary>'+esc(row['recorded_at'])+' · '+esc(label)+' · '+esc(boundary)+' <span class="badge '+esc(row['status'])+'">'+esc(row['status'])+'</span></summary>'
        if boundary=='start':
            body+='<p class="decision">决策：'+esc(d.get('decision_code'))+'</p><p>输入哈希：'+esc(d.get('input_hash'))+'</p>'
        else:
            body+='<p>耗时：'+esc(d.get('duration_ms'))+' ms；观测：</p><pre>'+esc(json.dumps(d.get('observation',{}),ensure_ascii=False,indent=2))+'</pre>'
        body+='<small>phase='+esc(d.get('phase'))+'；kind='+esc(d.get('kind','phase'))+'；span='+esc(d.get('span_id',d.get('sequence')))+'；parent='+esc(d.get('parent_span_id'))+'</small></details>'
    body+='</div><h2>工具与模型记录</h2>'+table(['类型','操作','状态','开始/记录','结束'],[('tool',r['tool_name'],r['envelope']['status'],r['recorded_at'],'见行动边界') for r in trace['tool_calls']]+[('model',r['detail'].get('operation'),r['status'],r['started_at'],r['finished_at']) for r in trace['model_calls']])
    body+='<p class="muted">HTML 快照哈希对应的来源数据：'+esc(digest(view))+'。工作台不会重试取数、调用模型、执行交易或修改正式状态。</p>'
    return page('运行 '+run_id,body)


def export_workbench(repository,scope,artifact_root,output_root,*,run_id=None,as_of_date=None,limit=20):
    ids=[run_id] if run_id else [r['run_id'] for r in repository.list_runs(scope,limit)]
    if any(not re.fullmatch(r'[a-f0-9]{32}',r or '') for r in ids):
        raise LookupError('view_not_found')
    views=[]
    for rid in ids:
        try:view=snapshot(repository,scope,rid,artifact_root,as_of_date=as_of_date)
        except LookupError:
            if run_id:raise
            continue
        views.append(view)
    # Validate every selected report before creating any deliverable file.
    root=Path(output_root).resolve()
    directory=root/scope.key/uuid4().hex
    directory.parent.resolve().relative_to(root/scope.key)
    directory.mkdir(parents=True,exist_ok=False)
    directory.resolve().relative_to(root/scope.key)
    cards=[];descriptors=[]
    for view in views:
        rid=view['trace']['run_id'];files={rid+'.html':render_run(view),rid+'.json':canonical(view)}
        if view['delivery'] and view['delivery']['html'] is not None:
            files[rid+'.report.html']=view['delivery']['html']
        for name,content in files.items():
            path=directory/name
            with path.open('x',encoding='utf-8') as stream:stream.write(content)
            descriptors.append({'file':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        trace=view['trace'];cards.append('<article class="card"><p><span class="badge '+esc(trace['status'])+'">'+esc(trace['status'])+'</span> '+esc(trace['request']['workflow'])+'</p><h3>'+esc(trace['request']['as_of_date'])+'</h3><p>开始：'+esc(trace['started_at'])+'</p><a href="'+rid+'.html">打开运行 / 报告 / 缺口</a><p><small>'+rid+'</small></p></article>')
    body='<header><h1>投研 Harness 工作台</h1><p>当前授权 Scope：'+esc(scope.key)+'</p><p>离线只读快照 · '+esc(now())+' · '+str(len(views))+' 个运行</p></header><div class="grid">'+''.join(cards)+'</div><p>公开执行记录与报告均按 Scope 读取。运行列表成功不代表每项业务已完成。</p>'
    (directory/'index.html').write_text(page('投研 Harness 工作台',body),encoding='utf-8')
    descriptors.append({'file':'index.html','sha256':hashlib.sha256((directory/'index.html').read_bytes()).hexdigest()})
    manifest={'schema_version':VERSION,'scope_key':scope.key,'run_ids':[v['trace']['run_id'] for v in views],
              'index':str(directory/'index.html'),'files':descriptors}
    (directory/'manifest.json').write_text(canonical(manifest),encoding='utf-8')
    return manifest
