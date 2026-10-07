"""Terminal HTML is prepared inside the terminal/state transaction, with rollback cleanup."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .contracts import canonical
from .trace import now
from .workbench import snapshot,render_run,page,esc


class TerminalView:
    def __init__(self,root,scope,run_id,*,minimal=False,checkpoint=None,chart_packet=None):
        self.root=Path(root).resolve();self.scope=scope;self.run_id=run_id;self.minimal=minimal;self.created=[];self.attempted=False;self.checkpoint=checkpoint;self.chart_packet=chart_packet

    def prepare(self,repository):
        self.attempted=True
        trace=repository.read(self.run_id,self.scope)
        view=({'schema_version':'terminal-diagnostic-v1','scope_key':self.scope.key,'captured_at':now(),
               'trace':trace,'plan':None,'delivery':None} if self.minimal else snapshot(repository,self.scope,self.run_id,self.root))
        if self.minimal:
            body='<h1>运行终态诊断</h1><p>运行：'+esc(self.run_id)+'</p><p>状态：'+esc(trace['status'])+'</p><p>行动 NO_ACTION；业务交付未完成。完整工作台渲染失败，仅保留最小终态与门禁诊断。</p><pre>'+esc(canonical(trace['outcome']))+'</pre>'
            text=page('运行诊断',body)
        else:
            text=render_run(view).replace('<a href="index.html">← 运行列表</a>','<span>单次运行归档</span>').replace('href="'+self.run_id+'.report.html"','href="report.html"')
        from .five_charts import missing_packet,validate_packet,render
        chart_packet=missing_packet(self.scope.key,self.run_id,trace["request"]["as_of_date"]) if self.minimal or self.chart_packet is None else self.chart_packet
        validate_packet(chart_packet,self.scope.key,self.run_id,trace["request"]["as_of_date"])
        chart_body=render(chart_packet)
        text=text.replace("</main>",chart_body+"</main>")
        chart_html=page("五图联动研究",chart_body)
        directory=self.root/self.scope.key/self.run_id
        directory.resolve().relative_to(self.root/self.scope.key/self.run_id)
        directory.mkdir(parents=True,exist_ok=True)
        descriptors=[]
        for name,content in [('run_view.json',canonical(view)),('five_charts.json',canonical(chart_packet)),('five_charts.html',chart_html),('run.html',text)]:
            path=directory/name;raw=content.encode('utf-8')
            with path.open('xb') as stream:
                self.created.append(path);stream.write(raw)
            if path.read_bytes()!=raw:raise ValueError('terminal_view_integrity_error')
            descriptors.append({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'scope_key':self.scope.key,
                'run_id':self.run_id,'as_of_date':trace['request']['as_of_date'],'mode':'diagnostic','publication_status':'terminal_diagnostic'})
        if self.checkpoint is not None:self.checkpoint()
        return descriptors

    def rollback(self):
        # Only files exclusively created by this transaction; never existing reports.
        for path in self.created:path.unlink(missing_ok=True)
        self.created.clear()


def read_terminal_html(repository,scope,run_id,root,*,as_of_date=None):
    from .delivery import read_artifact
    from .contracts import validate_date
    import json
    trace=repository.read(run_id,scope)
    if trace['status']=='running':raise LookupError('terminal_view_not_found')
    if as_of_date is not None and (trace['request']['as_of_date'] is None or trace['request']['as_of_date']>validate_date(as_of_date)):
        raise LookupError('terminal_view_not_found')
    raw,descriptor=read_artifact(trace,scope,root,'run.html')
    source,_=read_artifact(trace,scope,root,'run_view.json');view=json.loads(source)
    if (view['scope_key']!=scope.key or view['trace']['run_id']!=run_id or view['trace']['status']!=trace['status'] or
            view['trace']['outcome']!=trace['outcome']):raise ValueError('terminal_view_integrity_error')
    return raw.decode('utf-8'),descriptor
