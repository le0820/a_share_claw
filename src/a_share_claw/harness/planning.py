"""Immutable provider-independent investigation contract, pinned before evidence."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .contracts import RunRequest, canonical, digest


REPORT_SECTIONS = {
    "macro": ["data_audit", "scores", "drivers", "prior_comparison", "position_discipline", "unknowns"],
    "ai": ["data_audit", "growth", "momentum", "sentiment", "liquidity", "risk_decision", "unknowns"],
    "company": ["classification", "source_table", "fundamentals", "value_capture", "arguments", "risk_decision", "monitoring_triggers"],
    "industry": ["classification", "source_table", "technical_baseline", "value_capture", "arguments", "risk_decision", "monitoring_triggers"],
    "mixed": ["slice_status", "macro_report", "research_report", "combined_gaps"],
    "outlook": ["source_table", "forecast_horizon", "market_statistics", "base_scenario", "unknowns", "risk_decision", "monitoring_triggers"],
    "quant": ["frozen_specification", "source_table", "metrics", "limitations", "unknowns"],
    "general": ["framework", "gaps"],
}


@dataclass(frozen=True)
class FrozenResearchPlan:
    """Canonical bytes are immutable; consumers receive independent JSON copies."""
    document: str

    @property
    def plan_id(self):
        return digest(json.loads(self.document))

    def json(self):
        return {**json.loads(self.document), "plan_id": self.plan_id}


def freeze_plan(request: RunRequest, declared_plan: dict, parameters: dict | None = None):
    workflow = declared_plan["workflow"]
    missing = [] if request.as_of_date else ["as_of_date"]
    if workflow in {"company", "industry"} and not (parameters or {}).get("research_spec"):
        missing += ["research_spec"]
    if workflow == "outlook" and not (parameters or {}).get("quant_spec"):
        missing += ["outlook_spec"]
    if workflow == "mixed" and not (parameters or {}).get("mixed_spec"):
        missing += ["mixed_spec"]
    if workflow == "quant" and not (parameters or {}).get("quant_spec"):
        missing += ["universe", "window", "adjustment", "benchmark", "metric_definitions"]
    missing += [v["constraint"] for v in declared_plan.get("planning_gaps", [])]
    if workflow == "ai" and "current_ai_pct" not in (parameters or {}):
        missing += ["current_ai_pct"]
    document = {
        **declared_plan, "schema_version": "research-plan-v1", "version": 1,
        "question_hash": digest(request.message), "scope_key": request.scope.key,
        "as_of_date": request.as_of_date, "mode": request.mode,
        "parameters": parameters or {}, "report_sections": REPORT_SECTIONS[workflow],
        "framework": declared_plan.get("framework", "Freeze question and evidence needs before acquisition; interpret admitted facts under core policy."),
        "requirements": [
            {"requirement_id": capability, "capability": capability, "as_of_date": request.as_of_date,
             "disposition": disposition, "provider": None}
            for disposition, capabilities in (("required", declared_plan["required_capabilities"]),
                                               ("optional", declared_plan["optional_capabilities"]))
            for capability in capabilities
        ],
        "unresolved_constraints": sorted(set(missing)),
        "completion_criteria": ["required_context_loaded", "frozen_plan_unchanged",
                                "required_evidence_admitted", "workflow_executed", "report_evaluated_and_archived"],
        "stopping_conditions": ["future_data", "scope_mismatch", "unverified_evidence", "missing_required_data",
                                "policy_changed", "unimplemented_execution", "report_or_archive_failure"],
        "debate_policy": "conditional_after_shared_evidence" if workflow in {"company", "industry", "mixed"} else "disabled",
        "state_publication": "core_only_after_evaluation" if request.mode == "official" else "disabled",
    }
    return FrozenResearchPlan(canonical(document))


def trace_parameters(parameters):
    """Keep private investigation text in scoped artifacts, not trace metadata."""
    result = json.loads(canonical(parameters))
    def private_text(value):
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key in {"subject", "debate_reason", "question"} and isinstance(item, str):
                    value[key] = {"sha256": digest(item), "chars": len(item)}
                else:
                    private_text(item)
        elif isinstance(value, list):
            for item in value:
                private_text(item)
    private_text(result)
    return result


def trace_plan(plan):
    result = json.loads(canonical(plan))
    result["parameters"] = trace_parameters(result["parameters"])
    result["framework"] = {"sha256": digest(result["framework"]), "chars":len(result["framework"])}
    if "planning_gaps" in result:
        result["planning_gaps"] = [{"constraint":v["constraint"],"reason_hash":digest(v["reason"])} for v in result["planning_gaps"]]
    return result
