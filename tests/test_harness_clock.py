"""Actual evaluation-clock gates across scoring and research, after implementation."""
import json
from datetime import datetime,timezone

import pytest

from a_share_claw.harness.contracts import RunRequest,RunStatus
from a_share_claw.harness.trace import TraceRepository
from test_harness import DAY,environment,packet,ai_packet
from test_harness_research import spec,research_packet,role_reply,fixture_review
from test_harness_mixed import mixed_spec,mixed_packet


@pytest.mark.parametrize("workflow",["macro","ai","industry"])
def test_same_date_future_capture_cannot_compute_or_publish(environment,workflow):
    storage,scope,engine,_=environment
    facts=packet(scope) if workflow=="macro" else ai_packet(scope) if workflow=="ai" else research_packet(scope)
    # 16:00 CN is after the A-share close, but still before fixture availability 23:30 CN.
    reference=datetime(2026,7,13,8,tzinfo=timezone.utc)
    calls=[]
    out=engine.run(RunRequest(scope,"Synthetic same-date availability",DAY,"official",workflow),facts,
        clock=reference,research_spec=spec() if workflow=="industry" else None,
        role_runner=lambda payload:calls.append(payload),semantic_reviewer=fixture_review)
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="future_data"
    assert out.action=="NO_ACTION" and not out.official_output_allowed and not calls
    repo=TraceRepository(storage);assert repo.read_state(scope,workflow) is None
    trace=repo.read(out.run_id,scope)
    assert not any(v["stage"] in {"compute","evidence","role_start"} for v in trace["run_steps"])
    assert next(v["detail"]["evaluation_timestamp"] for v in trace["run_steps"] if v["stage"]=="clock")==reference.isoformat()


def test_capture_exactly_at_evaluation_clock_is_admitted(environment):
    storage,scope,engine,_=environment
    reference=datetime(2026,7,13,15,30,tzinfo=timezone.utc)
    out=engine.run(RunRequest(scope,"Synthetic available research",DAY,"official","industry"),research_packet(scope),
        clock=reference,research_spec=spec(),role_runner=role_reply,semantic_reviewer=fixture_review)
    assert out.status==RunStatus.SUCCEEDED and out.official_output_allowed
    assert TraceRepository(storage).read_state(scope,"industry")["run_id"]==out.run_id


def test_mixed_future_macro_does_not_suppress_available_research(environment):
    storage,scope,engine,_=environment
    reference=datetime(2026,7,13,8,tzinfo=timezone.utc)
    out=engine.run(RunRequest(scope,"Synthetic intraday mixed",DAY,"official","mixed"),mixed_packet(scope),clock=reference,
        mixed_spec=mixed_spec(),role_runner=role_reply,semantic_reviewer=fixture_review)
    result=json.loads(out.output)
    assert out.status==RunStatus.BLOCKED and result["error_code"]=="mixed_incomplete"
    assert result["slice_status"][0]["error_code"]=="future_data" and result["slice_status"][1]["status"]=="succeeded"
    assert result["partial_reports"] and not out.official_output_allowed
    repo=TraceRepository(storage);assert all(repo.read_state(scope,w) is None for w in ("macro","industry","mixed"))
    for child in result["slice_status"]:
        trace=repo.read(child["run_id"],scope)
        assert next(v["detail"]["evaluation_timestamp"] for v in trace["run_steps"] if v["stage"]=="clock")==reference.isoformat()
