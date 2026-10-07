"""HTML delivery and execution-boundary acceptance from synthetic admitted inputs."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_claw.harness.contracts import RunStatus
from a_share_claw.harness.delivery import read_report
from a_share_claw.harness.html import render_html
from a_share_claw.harness.trace import TraceRepository
from test_harness import environment
from test_harness_delivery import compute


@pytest.mark.parametrize('workflow', ['macro','ai','company','industry','quant','outlook','mixed'])
def test_html_roundtrip_and_execution_boundaries(environment,workflow):
    storage,scope,engine,_=environment
    outcome=compute(environment,workflow)
    assert outcome.status==RunStatus.SUCCEEDED,outcome.output
    repo=TraceRepository(storage)
    delivery=read_report(repo,scope,outcome.run_id,engine.artifact_root)
    text=delivery['html']
    assert text==render_html(delivery['report'])
    assert text==Path(delivery['html_descriptor']['path']).read_text()
    assert '<table>' in text and '来源表' in text and 'staged' in text
    assert '<script' not in text and '<img' not in text
    if workflow in {'quant','outlook'}:
        assert '<svg' in text and '<polyline' in text and '原始图表数值' in text
    rows=[s for s in repo.read(outcome.run_id,scope)['run_steps'] if s['stage']=='react_phase']
    assert [r['detail']['boundary'] for r in rows]==['start','end']*6
    assert [r['detail']['phase'] for r in rows[::2]]==['context','planning','evidence','compute','output','publish']
    assert all(r['status'] in {'ok','succeeded'} for r in rows[1::2])


def test_html_literal_model_and_source_content(environment):
    out=compute(environment,'industry')
    report=json.loads(Path(json.loads(out.output)['report']['path']).read_text())
    report['data']['role_outputs']['jia_zhi:initial']['answers'][0]['inference']='<script>alert(1)</script><img src="https://evil.invalid"> | injected'
    text=render_html(report)
    assert '<script>' not in text and '<img src=' not in text
    assert '&lt;script&gt;' in text and '&lt;img src=' in text
    assert 'Content-Security-Policy' in text


def test_tampered_html_rejected_and_archive_failure_preserves_state(environment):
    storage,scope,engine,_=environment
    repo=TraceRepository(storage)
    first=compute(environment,mode='official')
    assert first.status==RunStatus.SUCCEEDED
    previous=repo.read_state(scope,'macro')
    Path(json.loads(first.output)['report_html']['path']).write_text('tampered')
    with pytest.raises(ValueError):read_report(repo,scope,first.run_id,engine.artifact_root)
    archive=engine._archive_content
    def fail(session,filename,raw):
        if filename=='report.html':raise OSError('synthetic failure')
        return archive(session,filename,raw)
    with patch.object(engine,'_archive_content',side_effect=fail):bad=compute(environment,mode='official')
    assert bad.status==RunStatus.FAILED
    assert 'report_html' not in json.loads(bad.output)
    assert repo.read_state(scope,'macro')==previous
    rows=[s for s in repo.read(bad.run_id,scope)['run_steps'] if s['stage']=='react_phase']
    assert rows[-1]['detail']['boundary']=='end' and rows[-1]['status']=='error'
    assert rows[-1]['detail']['phase']=='output'


def test_unvalidated_html_cannot_publish(environment):
    storage,scope,_,_=environment
    with patch('a_share_claw.harness.engine.render_html',return_value='<html>BUY</html>'):
        out=compute(environment,mode='official')
    assert out.status==RunStatus.BLOCKED
    assert TraceRepository(storage).read_state(scope,'macro') is None


def test_phase_order_cannot_skip_evidence_and_rejected_publish_is_not_success(environment):
    from a_share_claw.harness.runtime import RunSession
    from a_share_claw.harness.contracts import RunRequest
    from test_harness import DAY
    storage,scope,engine,_=environment
    session=RunSession(storage,RunRequest(scope,'Synthetic boundary violation',DAY,'research','macro'))
    session.phase('context','synthetic')
    session.phase('planning','synthetic')
    with pytest.raises(ValueError,match='invalid_execution_transition'):
        session.phase('compute','skip_evidence')
    session.finish('synthetic failure',RunStatus.BLOCKED)
    original=TraceRepository.finish
    def deny(self,outcome,scope,state=None,as_of_date=None,**kwargs):
        if state is not None:raise ValueError('synthetic promotion denial')
        return original(self,outcome,scope,state,as_of_date,**kwargs)
    with patch.object(TraceRepository,'finish',new=deny):out=compute(environment,mode='official')
    assert out.status==RunStatus.BLOCKED
    rows=[r for r in TraceRepository(storage).read(out.run_id,scope)['run_steps'] if r['stage']=='react_phase']
    assert rows[-1]['detail']['phase']=='publish' and rows[-1]['status']=='blocked'
    assert not any(r['status']=='succeeded' for r in rows)
