"""Model-assisted framework compilation before any provider or admitted facts."""
from __future__ import annotations

import json
import re

from .contracts import EvalResult, canonical, digest, validate_date
from .mixed import workflow_parameters, mixed_plan
from .outlook import checked_outlook_spec, derived_requirements
from .quant import checked_quant_spec
from .research import RoleRequest, bounded_call, checked_spec, review_candidate

PARAMETER_KEYS = {"macro":set(),"ai":{"current_ai_pct"},"company":{"research_spec"},"industry":{"research_spec"},
                  "quant":{"quant_spec"},"outlook":{"outlook_spec"},"mixed":{"mixed_spec"},"general":set()}
CONSTRAINT_KEYS = {**PARAMETER_KEYS,"outlook":{"outlook_spec","quant_spec","forecast_start","forecast_end"}}


def checked_constraints(workflow, constraints):
    if not isinstance(constraints,dict) or set(constraints)-CONSTRAINT_KEYS[workflow]:
        raise ValueError("invalid_planning_constraints")
    result=json.loads(canonical(constraints))
    if any(v is None for v in result.values()):
        raise ValueError("invalid_planning_constraints")
    if "quant_spec" in result: result["quant_spec"]=checked_quant_spec(result["quant_spec"])
    if "current_ai_pct" in result: workflow_parameters("ai",current_ai_pct=result["current_ai_pct"])
    if "research_spec" in result: result["research_spec"]=checked_spec(result["research_spec"])
    if "outlook_spec" in result: checked_outlook_spec(result["outlook_spec"])
    if "mixed_spec" in result: workflow_parameters("mixed",mixed_spec=result["mixed_spec"])
    for field in ("forecast_start","forecast_end"):
        if field in result: validate_date(result[field])
    if "forecast_start" in result and "forecast_end" in result:
        if result["forecast_start"]>result["forecast_end"] or ("quant_spec" in result and result["forecast_start"]<=result["quant_spec"]["window_end"]):
            raise ValueError("invalid_planning_constraints")
    return result


def require_host_constraints(workflow, arguments, constraints):
    # Calendars/adjustments/benchmark/metric definitions and actual position must
    # be supplied by the trusted host, never fabricated during framework planning.
    if workflow == "ai" and arguments and arguments.get("current_ai_pct") != constraints.get("current_ai_pct"):
        raise ValueError("planning_constraint_changed")
    if workflow == "quant" and arguments and arguments.get("quant_spec") != constraints.get("quant_spec"):
        raise ValueError("planning_constraint_changed")
    if workflow == "outlook" and arguments:
        value=arguments["outlook_spec"]
        protected=constraints.get("outlook_spec",constraints)
        if any(field not in protected or value[field]!=protected[field] for field in ("quant_spec","forecast_start","forecast_end")):
            raise ValueError("planning_constraint_changed")
        expected={f["fact_id"]:f for f in derived_requirements(value["quant_spec"])}
        declared={f["fact_id"]:f for f in value["research_spec"]["required_facts"]}
        if any(declared.get(key)!=contract for key,contract in expected.items()):
            raise ValueError("invalid_framework_spec")
    if workflow == "mixed" and arguments:
        # Unprotected pure macro/research slices may be proposed. Numerical windows
        # and positions require a complete protected mixed specification.
        if any(v["workflow"] in {"ai","quant","outlook"} for v in arguments["mixed_spec"]["slices"]) and arguments.get("mixed_spec")!=constraints.get("mixed_spec"):
            raise ValueError("planning_constraint_changed")
    for key,value in constraints.items():
        if key in PARAMETER_KEYS[workflow] and arguments.get(key)!=value:
            raise ValueError("planning_constraint_changed")


def checked_proposal(workflow, proposal, constraints, as_of_date):
    if (not isinstance(proposal,dict) or set(proposal)!={"framework","parameters","unresolved_constraints"} or
            not isinstance(proposal["framework"],str) or not proposal["framework"].strip() or len(proposal["framework"])>5000 or
            not isinstance(proposal["parameters"],dict) or
            not isinstance(proposal["unresolved_constraints"],list) or len(proposal["unresolved_constraints"])>30):
        raise ValueError("invalid_framework_spec")
    gaps=[]
    for value in proposal["unresolved_constraints"]:
        if (not isinstance(value,dict) or set(value)!={"constraint","reason"} or
                not isinstance(value["constraint"],str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}",value["constraint"]) or
                not isinstance(value["reason"],str) or not value["reason"].strip() or len(value["reason"])>1000):
            raise ValueError("invalid_framework_spec")
        gaps.append(json.loads(canonical(value)))
    arguments=json.loads(canonical(proposal["parameters"]))
    expected=PARAMETER_KEYS[workflow]
    if arguments and (set(arguments)!=expected or any(v is None for v in arguments.values())):
        raise ValueError("invalid_framework_spec")
    require_host_constraints(workflow,arguments,constraints)
    if arguments or not expected:
        parameters=workflow_parameters(workflow,**arguments)
    else:
        parameters={}
        gaps.append({"constraint":next(iter(expected)),"reason":"The required executable specification is absent; do not invent host constraints."})
    if as_of_date:
        def check_dates(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key in {"observation_start","observation_end","window_end"} and validate_date(item)>as_of_date:
                        raise ValueError("future_data")
                    check_dates(item)
            elif isinstance(value,list):
                for item in value:check_dates(item)
        check_dates(parameters)
    return {"framework":proposal["framework"],"arguments":arguments,"parameters":parameters,"planning_gaps":gaps}


def compile_framework(session, policy, workflow, constraints, proposer, reviewer, adapter, archive):
    constraints=checked_constraints(workflow,constraints or {})
    if adapter is not None:
        if proposer is not None or reviewer is not None:
            raise ValueError("conflicting_model_adapters")
        proposer,reviewer=adapter.bind_framework(session)
    if proposer is None:
        raise ValueError("framework_proposer_required")
    quant=constraints.get("quant_spec") or constraints.get("outlook_spec",{}).get("quant_spec")
    derived=derived_requirements(quant) if workflow=="outlook" and quant is not None else []
    payload=RoleRequest(canonical({"user_request":session.request.message,"workflow":workflow,
        "as_of_date":session.request.as_of_date,"scope_key":session.request.scope.key,"mode":session.request.mode,
        "host_constraints":constraints,"core_derived_requirements":derived,"policy_version":policy.version,"declared_policy":policy.plan(workflow),
        "instruction":"Propose only a research framework and typed requirements. No source selection, facts, state writes or action. Preserve host constraints. Missing calendar/window/actual position remains a declared gap."}),session.remaining)
    session.step("framework_start",{"workflow":workflow,"constraints_hash":digest(constraints)})
    raw=bounded_call(proposer,payload,session.remaining,session.control)
    try:
        checked=checked_proposal(workflow,raw,constraints,session.request.as_of_date)
    except ValueError:
        archive(session,"rejected_framework",raw)
        raise
    declared=mixed_plan(policy,checked["parameters"].get("mixed_spec")) if workflow=="mixed" else policy.plan(workflow)
    candidate={"schema_version":"framework-proposal-v1","workflow":workflow,"as_of_date":session.request.as_of_date,
               "scope_key":session.request.scope.key,"framework":checked["framework"],"parameters":checked["arguments"],
               "unresolved_constraints":checked["planning_gaps"],"policy_version":policy.version,
               "host_constraints_hash":digest(constraints),"required_capabilities":declared["required_capabilities"]}
    archive(session,"framework_candidate",candidate)
    session.evaluate(EvalResult("framework_contract",True))
    review=review_candidate(session,{**declared,"parameters":checked["parameters"],"planning_only":True},candidate,reviewer,archive,artifact_name="framework_review",evaluator="framework_semantic_review",
                            criteria=["original_request_satisfied","requirements_cover_question","host_constraints_preserved","conditional_debate","gaps_explicit","planning_evidence_separation","unit_consistency","no_irrelevant_host_constraints","no_admitted_facts_or_action"])
    if session.remaining<=0: raise TimeoutError("budget_exceeded")
    if not policy.unchanged(): raise ValueError("policy_changed")
    session.step("framework_end",{"candidate_hash":digest(candidate),"review_hash":digest(review),
                                 "unresolved_constraints":[v["constraint"] for v in checked["planning_gaps"]]})
    return checked
