"""Independent, non-publishing slices; only the reviewed parent can publish."""
from __future__ import annotations

import asyncio
import json
import re

from .contracts import EvalResult, RunRequest, RunStatus, canonical
from .outlook import checked_outlook_spec
from .quant import checked_quant_spec
from .research import checked_spec, review_candidate


def workflow_parameters(workflow, *, current_ai_pct=57.5, research_spec=None, quant_spec=None, outlook_spec=None, mixed_spec=None):
    if workflow == "ai":
        if type(current_ai_pct) not in {int, float} or not 0 <= current_ai_pct <= 100:
            raise ValueError("invalid_current_position")
        return {"current_ai_pct": current_ai_pct}
    if workflow in {"company", "industry"}:
        return {"research_spec": checked_spec(research_spec) if research_spec is not None else None}
    if workflow == "quant":
        return {"quant_spec": checked_quant_spec(quant_spec) if quant_spec is not None else None}
    if workflow == "outlook":
        return checked_outlook_spec(outlook_spec) if outlook_spec is not None else {}
    if workflow == "mixed":
        return {"mixed_spec": checked_mixed_spec(mixed_spec) if mixed_spec is not None else None}
    return {}


def checked_mixed_spec(spec):
    if (not isinstance(spec, dict) or set(spec) != {"slices"} or
            not isinstance(spec["slices"], list) or not 2 <= len(spec["slices"]) <= 8):
        raise ValueError("invalid_mixed_spec")
    ids = set()
    for item in spec["slices"]:
        if (not isinstance(item, dict) or set(item) != {"slice_id", "workflow", "question", "parameters"} or
                not isinstance(item["slice_id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", item["slice_id"]) or
                item["slice_id"] in ids or not isinstance(item["question"], str) or not item["question"].strip() or
                not isinstance(item["parameters"], dict)):
            raise ValueError("invalid_mixed_spec")
        expected = {"macro": set(), "ai": {"current_ai_pct"}, "company": {"research_spec"},
                    "industry": {"research_spec"}, "quant": {"quant_spec"}, "outlook": {"outlook_spec"}}
        if item["workflow"] not in expected or set(item["parameters"]) != expected[item["workflow"]]:
            raise ValueError("invalid_mixed_spec")
        if any(v is None for v in item["parameters"].values()):
            raise ValueError("invalid_mixed_spec")
        workflow_parameters(item["workflow"], **item["parameters"])
        ids.add(item["slice_id"])
    return json.loads(canonical(spec))


def mixed_plan(policy, spec):
    declared = policy.plan("mixed")
    if spec is not None:
        children = [policy.plan(item["workflow"]) for item in spec["slices"]]
        required = sorted({v for child in children for v in child["required_capabilities"]})
        optional = sorted({v for child in children for v in child["optional_capabilities"]} - set(required))
        declared.update(required_capabilities=required, optional_capabilities=optional)
    return declared


def execute_mixed(engine, session, plan, packet, reference, output, runner, reviewer, adapter):
    spec = plan["parameters"].get("mixed_spec")
    if spec is None:
        raise ValueError("mixed_spec_required")
    packets = {}
    if packet is not None:
        if (not isinstance(packet, dict) or set(packet) != {"as_of_date", "slices"} or
                packet["as_of_date"] != session.request.as_of_date or not isinstance(packet["slices"], list)):
            raise ValueError("invalid_mixed_packet")
        allowed = {item["slice_id"] for item in spec["slices"]}
        for entry in packet["slices"]:
            if (not isinstance(entry, dict) or set(entry) != {"slice_id", "packet"} or
                    not isinstance(entry["slice_id"], str) or entry["slice_id"] not in allowed or entry["slice_id"] in packets):
                raise ValueError("invalid_mixed_packet")
            packets[entry["slice_id"]] = entry["packet"]
    output["slice_status"], output["partial_reports"] = [], []
    results, sources, failures = [], [], []
    for item in spec["slices"]:
        if session.remaining <= 0:
            raise TimeoutError("budget_exceeded")
        request = RunRequest(session.request.scope,
            session.request.message + "\nScope of this slice: " + item["question"],
            session.request.as_of_date, "replay" if session.request.mode == "replay" else "research",
            item["workflow"], wall_clock_seconds=session.remaining,
            max_tool_calls=session.request.max_tool_calls, host=session.request.host)
        child = engine.run(request, packets.get(item["slice_id"]), clock=reference,
            role_runner=runner, semantic_reviewer=reviewer, research_adapter=adapter,
            require_official_close=session.request.mode == "official",
            parent_run_id=session.run_id, slice_id=item["slice_id"], **item["parameters"])
        child_output = json.loads(child.output)
        if child.status == RunStatus.FAILED:
            failures.append(child.category)
        summary = {"slice_id": item["slice_id"], "workflow": item["workflow"], "run_id": child.run_id,
                   "status": child.status.value, "disposition": "waiting" if child_output.get("error_code") in {"WAIT_FOR_CLOSE", "WAIT_FOR_TRADING_DAY", "window_not_closed"} else child.status.value,
                   "error_code": child_output.get("error_code"), "action": "NO_ACTION", "official_output_allowed": False}
        output["slice_status"].append(summary)
        session.step("slice_result", summary)
        if child.status == RunStatus.SUCCEEDED:
            output["partial_reports"].append({"slice_id": item["slice_id"], "report": child_output["report"]})
            results.append({**summary, "data": child_output["data"], "report": child_output["report"]})
            sources += [{**p, "slice_id": item["slice_id"], "run_id": child.run_id} for p in child_output["data_audit"]["source_files"]]
        if child.status == RunStatus.CANCELLED:
            raise asyncio.CancelledError()
    engine._archive(session, "mixed_progress", {"slice_status": output["slice_status"], "partial_reports": output["partial_reports"],
                                               "publication_status": "staged", "action": "NO_ACTION"})
    output["gaps"] = [{"slice_id": v["slice_id"], "error_code": v["error_code"]} for v in output["slice_status"] if v["status"] != "succeeded"]
    if output["gaps"]:
        if failures:
            session.failure = failures[0]
        raise ValueError("mixed_incomplete")
    session.evaluate(EvalResult("mixed_slices", True))
    candidate = {"schema_version": "mixed-output-v1", "slice_status": output["slice_status"], "slices": results,
                 "combined_gaps": [], "risk_decision": "NO_ACTION"}
    engine._archive(session, "mixed_candidate", candidate)
    if adapter is not None:
        if runner is not None or reviewer is not None:
            raise ValueError("conflicting_model_adapters")
        _, reviewer = adapter.bind(session)
    review = review_candidate(session, plan, candidate, reviewer, engine._archive)
    return {**candidate, "semantic_review": review}, sources
