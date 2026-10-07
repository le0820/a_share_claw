"""Automatic terminal HTML and state/terminal atomicity, synthetic inputs."""
import json
from pathlib import Path
from unittest.mock import patch
import pytest
from a_share_claw.harness.contracts import RunRequest,RunStatus,Scope
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.harness.terminal_view import read_terminal_html,TerminalView
from test_harness import environment,DAY
from test_harness_delivery import compute


@pytest.mark.parametrize('kind',['plan','gap','success','failed','cancelled','undated'])
def test_each_core_terminal_has_automatic_hash_bound_html(environment,kind):
    storage,scope,engine,_=environment
    if kind=='success':out=compute(environment,'quant')
    elif kind=='failed':
        with patch.object(engine,'_evidence_impl',side_effect=OSError('synthetic failure')):
            out=engine.run(RunRequest(scope,'Synthetic failed output',DAY,'research','macro'))
    elif kind=='cancelled':
        with patch.object(engine,'_evidence_impl',side_effect=KeyboardInterrupt()):
            out=engine.run(RunRequest(scope,'Synthetic interrupted output',DAY,'research','macro'))
    else:out=engine.run(RunRequest(scope,'Synthetic terminal HTML',None if kind=='undated' else DAY,'plan' if kind=='plan' else 'research','macro'))
    repo=TraceRepository(storage);text,descriptor=read_terminal_html(repo,scope,out.run_id,engine.artifact_root)
    assert Path(descriptor['path']).read_text()==text and '<!doctype html>' in text and '<script' not in text
    assert out.status.value in text and 'ReAct 执行时间轴' in text
    assert all('id="'+cid+'"' in text for cid in ('cn_consumption','cn_ai','us_ai','banks_rates','us_real_inflation'))
    if kind=='success':assert 'href="report.html"' in text
    else:assert '打开已校验报告与图表' not in text
    other=Scope(scope.workspace,'other',scope.session)
    with pytest.raises(LookupError):read_terminal_html(repo,other,out.run_id,engine.artifact_root)
    with pytest.raises(LookupError):read_terminal_html(repo,scope,out.run_id,engine.artifact_root,as_of_date='2026-07-01')
    Path(descriptor['path']).write_text('tampered')
    with pytest.raises(ValueError):read_terminal_html(repo,scope,out.run_id,engine.artifact_root)


def test_terminal_render_failure_rolls_back_official_state_and_delivers_minimal_failed_html(environment):
    storage,scope,engine,_=environment;repo=TraceRepository(storage)
    first=compute(environment,mode='official');previous=repo.read_state(scope,'macro')
    with patch('a_share_claw.harness.terminal_view.render_run',side_effect=ValueError('synthetic render fault')):
        bad=compute(environment,mode='official')
    assert bad.status==RunStatus.FAILED and bad.action=='NO_ACTION'
    assert json.loads(bad.output)['error_code']=='terminal_view_failure' and 'report_html' not in json.loads(bad.output)
    assert repo.read_state(scope,'macro')==previous and repo.published_state(bad.run_id,scope) is None
    text,_=read_terminal_html(repo,scope,bad.run_id,engine.artifact_root)
    assert '最小终态' in text and '业务交付未完成' in text
    trace=repo.read(bad.run_id,scope)
    assert len([r for r in trace['artifacts'] if Path(r['detail']['path']).name=='run.html'])==1
    assert not any(r['stage']=='react_phase' and r['status']=='succeeded' for r in trace['run_steps'])


def test_terminal_file_io_failure_never_promotes_or_strands_a_running_run(environment):
    storage,scope,engine,_=environment
    with patch.object(TerminalView,'prepare',side_effect=OSError('synthetic disk unavailable')):
        # mark attempted exactly as a failing writer would
        original=TerminalView.__init__
        def init(self,*a,**kw):original(self,*a,**kw);self.attempted=True
        with patch.object(TerminalView,'__init__',new=init):out=compute(environment,mode='official')
    assert out.status==RunStatus.FAILED and json.loads(out.output)['terminal_html_unavailable']
    repo=TraceRepository(storage)
    assert repo.read_state(scope,'macro') is None and repo.read(out.run_id,scope)['status']=='failed'


def test_sqlite_rejection_after_html_creation_removes_orphan_and_rolls_back_state(environment):
    storage,scope,engine,_=environment;repo=TraceRepository(storage)
    previous=compute(environment,mode='official');old=repo.read_state(scope,'macro')
    with storage._lock,storage._conn:
        storage._conn.execute("CREATE TEMP TRIGGER reject_terminal_html BEFORE INSERT ON artifacts WHEN json_extract(NEW.detail_json,'$.path') LIKE '%/run.html' BEGIN SELECT RAISE(ABORT,'synthetic terminal insertion failure'); END")
    try:bad=compute(environment,mode='official')
    finally:
        with storage._lock,storage._conn:storage._conn.execute('DROP TRIGGER reject_terminal_html')
    assert bad.status==RunStatus.FAILED and repo.read_state(scope,'macro')==old
    assert repo.published_state(bad.run_id,scope) is None
    assert json.loads(bad.output)['terminal_html_unavailable']
    folder=engine.artifact_root/scope.key/bad.run_id
    assert not (folder/'run.html').exists() and not (folder/'run_view.json').exists()
    assert not any(Path(a['detail']['path']).name in {'run.html','run_view.json'} for a in repo.read(bad.run_id,scope)['artifacts'])
