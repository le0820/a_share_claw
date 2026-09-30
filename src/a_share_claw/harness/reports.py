"""Host-owned reports rendered only from admitted facts and evaluated results."""
from __future__ import annotations

from .contracts import digest


def build_report(request, run_id, plan, data, audit, prior, generated_at):
    workflow = plan["workflow"]
    report = {"schema_version": "harness-report-v1", "run_id": run_id, "plan_id": plan["plan_id"],
              "scope_key": request.scope.key, "workflow": workflow, "as_of_date": request.as_of_date,
              "generated_at": generated_at, "mode": request.mode, "data_period": [p["data_period"] for p in audit["source_files"]],
              "source_table": audit["source_files"], "fallback_status": audit["fallback_status"],
              "policy_version": audit["policy_version"], "publication_status": "staged", "data": data,
              "action": (data["decision"]["action"] if workflow == "ai" else "POSITION_BAND" if workflow == "macro" else "NO_ACTION") if request.mode == "official" else "NO_ACTION", "prior_comparison": {"status": "unavailable"}}
    if prior and prior["as_of_date"] < request.as_of_date and prior["data_audit"]["policy_version"] == audit["policy_version"]:
        report["prior_comparison"] = {"status": "available", "as_of_date": prior["as_of_date"], "run_id": prior.get("run_id")}
        if workflow == "macro":
            report["prior_comparison"]["score_changes"] = {k: round(data[k] - prior["data"][k], 2) for k in ("L1", "L3", "composite")}
    if workflow == "macro":
        report["scores"] = {k: data[k] for k in ("L1", "L2", "L2_status", "L3", "composite")}
        report["drivers"] = {k: data[k] for k in ("us_factor_scores", "cn_factor_scores", "sentiment", "risk", "alerts")}
        report["omitted_factors"] = [group + "." + k for group in ("us_factor_scores", "cn_factor_scores", "sentiment") for k, v in data[group].items() if v is None]
        report["disabled_layers"] = ["L2"]
        report["position_discipline"] = {"position_band": data["position_band"], "action": "POSITION_BAND" if request.mode == "official" else "NO_ACTION"}
    elif workflow == "ai":
        report["factors"] = data["factors"]
        report["position_discipline"] = {**data["decision"], "action": data["decision"]["action"] if request.mode == "official" else "NO_ACTION"}
    else:
        report["risk_decision"] = data["risk_decision"]
        report["monitoring_triggers"] = data["monitoring_triggers"]
    return report


def validate_report(report, request, run_id, plan, data, audit, prior, generated_at):
    expected = build_report(request, run_id, plan, data, audit, prior, generated_at)
    if digest(report) != digest(expected):
        raise ValueError("report_contract_failure")
