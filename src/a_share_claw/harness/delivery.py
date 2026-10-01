"""Read only hash-verified reports belonging to an authorized successful run."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import validate_date
from .markdown import validate_markdown


def read_artifact(trace, scope, artifact_root, filename):
    candidates = [v["detail"] for v in trace["artifacts"] if Path(v["detail"].get("path", "")).name == filename]
    if len(candidates) != 1:
        raise ValueError("report_integrity_error")
    detail = candidates[0]
    path = Path(detail["path"]).resolve()
    # Do not resolve scope/run directories: an escaping symlink must fail containment.
    allowed = Path(artifact_root).resolve() / scope.key / trace["run_id"]
    path.relative_to(allowed)
    if (detail.get("scope_key") != scope.key or detail.get("run_id") != trace["run_id"] or
            detail.get("as_of_date") != trace["request"]["as_of_date"]):
        raise ValueError("report_integrity_error")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != detail["sha256"]:
        raise ValueError("report_integrity_error")
    return raw, detail


def read_report(repository, scope, run_id, artifact_root, *, as_of_date=None):
    trace = repository.read(run_id, scope)
    if trace["status"] != "succeeded":
        raise LookupError("report_not_found")
    if as_of_date is not None and trace["request"]["as_of_date"] > validate_date(as_of_date):
        raise ValueError("report_integrity_error")
    if not trace["evaluations"] or any(not v["passed"] for v in trace["evaluations"]):
        raise ValueError("report_integrity_error")
    required = {"report_contract", "report_markdown_contract"}
    if not required <= {v["detail"]["evaluator"] for v in trace["evaluations"] if v["passed"] and v["hard_gate"]}:
        raise LookupError("report_not_found")
    raw, descriptor = read_artifact(trace, scope, artifact_root, "report.json")
    md, md_descriptor = read_artifact(trace, scope, artifact_root, "report.md")
    plan_raw, _ = read_artifact(trace, scope, artifact_root, "plan.json")
    result_raw, _ = read_artifact(trace, scope, artifact_root, "computed_output.json")
    report, plan, result = json.loads(raw), json.loads(plan_raw), json.loads(result_raw)
    if (report["run_id"] != run_id or report["scope_key"] != scope.key or
            report["as_of_date"] != trace["request"]["as_of_date"] or report["mode"] != trace["request"]["mode"] or
            report["plan_id"] != plan["plan_id"] or report["workflow"] != plan["workflow"] or
            result["report"] != descriptor or result["report_markdown"] != md_descriptor or result["data"] != report["data"] or
            report["publication_status"] != "staged"):
        raise ValueError("report_integrity_error")
    text = md.decode("utf-8")
    validate_markdown(text, report)
    published = repository.published_state(run_id, scope)
    official = bool(trace["outcome"]["official_output_allowed"])
    if official:
        if (published is None or published["report"] != descriptor or published.get("report_markdown") != md_descriptor or
                published["workflow"] != report["workflow"] or published["as_of_date"] != report["as_of_date"]):
            raise ValueError("report_integrity_error")
        publication = "published"
        action = trace["outcome"]["action"]
    else:
        if published is not None or trace["outcome"]["action"] != "NO_ACTION":
            raise ValueError("report_integrity_error")
        publication, action = "research", "NO_ACTION"
    header = "读取状态：" + ("已正式发布" if official else "研究/回放产物") + "；行动：" + action + "。归档原文如下。\n\n"
    return {"run_id":run_id, "scope_key":scope.key, "publication_status":publication, "official_output_allowed":official,
            "action":action, "report":report, "report_descriptor":descriptor, "markdown_descriptor":md_descriptor,
            "markdown":header+text}
