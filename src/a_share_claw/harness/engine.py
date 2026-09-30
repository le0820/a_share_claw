"""Provider-free workflow: plan -> evidence -> deterministic policy -> gate -> publish."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..research_context import classify_research_route
from .contracts import EvalResult, FailureCategory as Failure, RunRequest, RunStatus, canonical, digest, validate_date
from .policy import PolicyBundle, PolicyContextError
from .planning import freeze_plan, trace_parameters, trace_plan
from .research import execute_research
from .mixed import workflow_parameters, mixed_plan, execute_mixed
from .reports import build_report, validate_report
from .quant import compute_quant, timestamp
from .outlook import outlook_facts
from .runtime import RunSession
from .trace import now


DATE_FIELDS = {"date", "as_of_date", "trade_date", "observation_date", "publication_date", "release_date", "filed", "end", "period_end", "effective_trade_date"}
MACRO_UNITS = {
    "us_macro": {"vix": "index_points", "spx": "index_points", "ust_2y": "percent", "ust_10y": "percent", "ust_30y": "percent",
                 "brent": "USD_per_barrel", "cpi_us_yoy": "percent", "core_cpi_yoy": "percent", "ppi_us_yoy": "percent",
                 "fed_rate": "percent", "nfp_actual": "thousand_persons", "unemployment": "percent", "credit_spread": "percentage_points",
                 "credit_spread_change_20d": "percentage_points", "jp_jgb_10y": "percent", "usdjpy": "JPY_per_USD", "put_call_ratio": "ratio"},
    "cn_macro": {"pmi_mfg": "index_points", "pmi_non_mfg": "index_points", "retail_sales_yoy": "percent", "m2_yoy": "percent", "m1_yoy": "percent"},
    "tech_capex": {"capex_pct_ocf": "ratio"},
}


def validate_values(value, cutoff):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "fallback_status" and item != "none":
                raise ValueError("unverified_evidence")
            if key in DATE_FIELDS and item is not None:
                if validate_date(item) > cutoff:
                    raise ValueError("future_data")
            validate_values(item, cutoff)
    elif isinstance(value, list):
        for item in value:
            validate_values(item, cutoff)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non_finite_value")


class Harness:
    def __init__(self, root: Path, storage, artifact_root: Path, market_timezone="Asia/Shanghai"):
        self.root, self.storage, self.artifact_root = root, storage, artifact_root
        self.market_timezone = market_timezone

    def run(self, request: RunRequest, packet: dict | None = None, *, current_ai_pct=57.5, clock=None, replay_of=None, research_spec=None, role_runner=None, semantic_reviewer=None, research_adapter=None, quant_spec=None, outlook_spec=None, mixed_spec=None, require_official_close=False, parent_run_id=None, slice_id=None):
        session = RunSession(self.storage, request)
        if replay_of:
            session.step("replay_source", {"run_id": replay_of})
        output = {"run_id": session.run_id, "scope_key": request.scope.key, "as_of_date": request.as_of_date, "mode": request.mode,
                  "action": "NO_ACTION", "official_output_allowed": False, "gaps": [], "data": None}
        status, state = RunStatus.BLOCKED, None
        try:
            if parent_run_id is not None:
                parent = session.repository.read(parent_run_id, request.scope)
                if (parent["status"] != "running" or parent["request"]["as_of_date"] != request.as_of_date or
                        request.mode not in {"research", "replay"} or
                        not any(row["stage"] == "route" and row["detail"].get("workflow") == "mixed" for row in parent["run_steps"])):
                    raise ValueError("invalid_mixed_link")
                session.step("parent_run", {"run_id": parent_run_id, "slice_id": slice_id})
            route = classify_research_route(request.message)
            workflow = request.workflow or route.workflow.value
            session.step("route", {"workflow": workflow, "reason": route.reason, "override": request.workflow is not None})
            policy = PolicyBundle(self.root)
            session.step("policy_snapshot", {"version": policy.version, "files": policy.hashes})
            session.step("context", {"kind": "policy_snapshot", "loaded_files": list(policy.hashes),
                                     "missing_files": [], "state_scope": request.scope.key,
                                     "state_injected": False})
            parameters = workflow_parameters(workflow, current_ai_pct=current_ai_pct,
                research_spec=research_spec, quant_spec=quant_spec, outlook_spec=outlook_spec, mixed_spec=mixed_spec)
            if require_official_close:
                parameters["official_close_required"] = True
            declared = mixed_plan(policy, parameters["mixed_spec"]) if workflow == "mixed" else policy.plan(workflow)
            frozen_plan = freeze_plan(request, declared, parameters)
            plan = frozen_plan.json()
            session.step("plan", trace_plan(plan))
            session.step("workflow_parameters", trace_parameters(parameters))
            self._archive(session, "plan", plan)
            output["plan"] = plan
            if request.mode == "plan":
                output["gaps"] = plan["required_capabilities"]
                status = RunStatus.SUCCEEDED
            else:
                reference = clock or datetime.now(timezone.utc)
                if reference.tzinfo is None:
                    raise ValueError("Clock must include timezone")
                market_now = reference.astimezone(ZoneInfo(self.market_timezone))
                cutoff = request.as_of_date
                if cutoff is None:
                    raise ValueError("explicit_date_required")
                if cutoff > market_now.date().isoformat():
                    raise ValueError("future_data")
                if workflow in {"macro", "ai"} and (request.mode == "official" or require_official_close) and datetime.fromisoformat(cutoff).weekday() >= 5:
                    raise ValueError("WAIT_FOR_TRADING_DAY")
                if workflow in {"macro", "ai"} and (request.mode == "official" or require_official_close) and cutoff == market_now.date().isoformat() and market_now.hour < 15:
                    raise ValueError("WAIT_FOR_CLOSE")
                if workflow == "mixed":
                    data, provenance = execute_mixed(self, session, plan, packet, reference, output,
                                                    role_runner, semantic_reviewer, research_adapter)
                else:
                    facts, provenance = self._evidence(session, policy, plan, packet, cutoff)
                    missing = [key for key in plan["required_capabilities"] if key not in facts]
                    output["gaps"] = missing
                    session.step("gap_report", {"missing": missing, "disabled_layers": ["L2"]})
                    if missing:
                        raise ValueError("missing_required_data")
                output["data_audit"] = {"as_of_date": cutoff, "source_files": provenance,
                                        "fallback_status": "none", "policy_version": policy.version}
                if workflow == "mixed":
                    pass  # child evidence and combined review have passed; publisher below is shared
                elif workflow == "macro":
                    data = policy.score_macro(facts)
                elif workflow == "ai":
                    if not isinstance(current_ai_pct, (int, float)) or isinstance(current_ai_pct, bool) or not 0 <= current_ai_pct <= 100:
                        raise ValueError("invalid_current_position")
                    data = policy.score_ai(facts, current_ai_pct)
                    if data["decision"]["action"] == "NO_ACTION":
                        raise ValueError("insufficient_coverage")
                elif workflow == "quant":
                    data = compute_quant(parameters["quant_spec"], facts["price_history"], reference, cutoff, self.market_timezone)
                elif workflow in {"company", "industry", "outlook"}:
                    if workflow == "outlook":
                        if not parameters:
                            raise ValueError("outlook_spec_required")
                        quant = compute_quant(parameters["quant_spec"], facts["price_history"], reference, cutoff, self.market_timezone)
                        quant_archive = self._archive(session, "quant_metrics", quant)
                        facts = outlook_facts(facts["macro_release_facts"], quant, quant_archive, cutoff)
                    if research_adapter is not None:
                        if role_runner is not None or semantic_reviewer is not None:
                            raise ValueError("conflicting_model_adapters")
                        role_runner, semantic_reviewer = research_adapter.bind(session)
                    data = execute_research(session, plan, facts, role_runner, semantic_reviewer, self._archive)
                    if workflow == "outlook":
                        data.update(market_statistics=quant, forecast_horizon=parameters["forecast_horizon"],
                                    base_scenario=next(a["inference"] for a in data["role_outputs"]["hong_guan:initial"]["answers"] if a["question_id"] == "base_scenario"))
                else:
                    # Evidence alone cannot implement an unsupported workflow.
                    raise ValueError("workflow_execution_pending")
                canonical(data)  # rejects NaN/Infinity before any artifact or state write
                computed_hash = digest(data)
                session.step("compute", {"data_hash": computed_hash, "policy_version": policy.version})
                if session.remaining <= 0:
                    raise TimeoutError("budget_exceeded")
                if plan != frozen_plan.json():
                    raise ValueError("plan_changed")
                if not policy.unchanged():
                    raise ValueError("policy_changed")
                session.evaluate(EvalResult("frozen_plan", True))
                session.evaluate(EvalResult("policy_snapshot", True))
                session.evaluate(EvalResult("required_evidence", True))
                session.evaluate(EvalResult("scope_and_date", True))
                prior = session.repository.read_state(request.scope, workflow, as_of_date=cutoff)
                report_timestamp = now()
                report = build_report(request, session.run_id, plan, data, output["data_audit"], prior, report_timestamp)
                validate_report(report, request, session.run_id, plan, data, output["data_audit"], prior, report_timestamp)
                if digest(data) != computed_hash:
                    raise ValueError("report_contract_failure")
                output["report"] = self._archive(session, "report", report)
                session.evaluate(EvalResult("report_contract", True))
                session.step("report", {"sha256": output["report"]["sha256"], "status": "staged"})
                output["data"] = data
                output["official_output_allowed"] = request.mode == "official"
                output["action"] = (data["decision"]["action"] if workflow == "ai" else "POSITION_BAND" if workflow == "macro" else "NO_ACTION") if request.mode == "official" else "NO_ACTION"
                status = RunStatus.SUCCEEDED
                if request.mode == "official":
                    state = {"run_id": session.run_id, "workflow": workflow, "as_of_date": cutoff, "generated_at": now(), "data": data, "data_audit": output["data_audit"], "report": output["report"]}
                self._archive(session, "computed_output", {**output, "publication_status": "staged", "official_output_allowed": False, "action": "NO_ACTION"})
                if session.remaining <= 0:
                    raise TimeoutError("budget_exceeded")
                if plan != frozen_plan.json():
                    raise ValueError("plan_changed")
                if not policy.unchanged():
                    raise ValueError("policy_changed")
        except PolicyContextError as exc:
            session.step("context", {"kind": "policy_snapshot", "loaded_files": exc.loaded_files,
                                     "missing_files": exc.missing_files, "state_scope": request.scope.key,
                                     "state_injected": False}, "error")
            session.attribution = Failure.CONTEXT_TRUNCATION_FAILURE
            session.evaluate(EvalResult("required_policy_context", False,
                             category=Failure.CONTEXT_TRUNCATION_FAILURE, code=str(exc)))
            output["error_code"] = str(exc)
        except (ValueError, KeyError, TypeError) as exc:
            known = {"future_data", "missing_required_data", "unverified_evidence", "scope_mismatch", "hash_mismatch",
                     "missing_provenance", "WAIT_FOR_CLOSE", "WAIT_FOR_TRADING_DAY", "explicit_date_required", "policy_changed", "plan_changed", "workflow_execution_pending",
                     "insufficient_coverage", "invalid_current_position", "invalid_evidence", "invalid_market_history",
                     "mixed_spec_required", "invalid_mixed_spec", "invalid_mixed_packet", "invalid_mixed_link", "mixed_incomplete",
                     "research_spec_required", "role_executor_required", "semantic_review_required", "semantic_review_failed",
                     "invalid_semantic_review", "invalid_research_spec", "model_configuration_required", "model_replay_not_supported", "invalid_model_output", "conflicting_model_adapters", "invalid_research_facts", "invalid_role_output",
                     "quant_spec_required", "outlook_spec_required", "invalid_quant_spec", "invalid_outlook_spec", "invalid_outlook_facts",
                     "quant_operation_not_implemented", "window_not_closed", "price_contract_mismatch", "price_before_close", "non_comparable_anchor",
                     "role_packet_mismatch", "invalid_evidence_reference", "missing_rebuttal", "report_contract_failure", "research_fact_contract_mismatch"}
            text = str(exc)
            code = text if text in known or text.startswith(("missing_required_field:", "insufficient_coverage:")) else "invalid_schema"
            if code in {"future_data", "scope_mismatch", "unverified_evidence", "role_packet_mismatch"}:
                category = Failure.STATE_CONTAMINATION_FAILURE
            elif code in {"policy_changed", "WAIT_FOR_CLOSE", "WAIT_FOR_TRADING_DAY", "plan_changed", "workflow_execution_pending", "research_spec_required", "role_executor_required", "semantic_review_required", "model_configuration_required", "model_replay_not_supported", "conflicting_model_adapters", "quant_spec_required", "outlook_spec_required", "mixed_spec_required", "mixed_incomplete", "invalid_mixed_link", "quant_operation_not_implemented", "window_not_closed", "price_before_close"}:
                category = Failure.PERMISSION_POLICY_FAILURE
            elif code == "semantic_review_failed":
                category = Failure.SYNTHESIS_OR_UNKNOWN_FAILURE
            elif code == "missing_required_data" or code.startswith("insufficient_coverage"):
                category = Failure.RETRIEVAL_INTERFACE_FAILURE
            else:
                category = Failure.TOOL_RETURN_FAILURE
            session.attribution = category
            session.evaluate(EvalResult("execution_gate", False, category=category, code=code))
            output["error_code"] = code
        except (KeyboardInterrupt, asyncio.CancelledError):
            status = RunStatus.CANCELLED
            output["error_code"] = "cancelled"
        except TimeoutError:
            session.failure = Failure.TIMEOUT_OR_BUDGET_FAILURE
            output["error_code"] = "budget_exceeded"
        except OSError as exc:
            session.failure = Failure.TOOL_RETURN_FAILURE
            output["error_code"] = "artifact_io_error"
            session.step("exception", {"type": type(exc).__name__}, "error")
        except Exception as exc:
            session.failure = Failure.SYNTHESIS_OR_UNKNOWN_FAILURE
            output["error_code"] = "execution_error"
            session.step("exception", {"type": type(exc).__name__}, "error")
        if status != RunStatus.SUCCEEDED or session.failure is not None:
            output.update(action="NO_ACTION", official_output_allowed=False, data=None)
            output.pop("report", None)
            state = None
        session.step("publish_gate", {"allowed": output["official_output_allowed"], "action": output["action"]})
        try:
            return session.finish(canonical(output), status, action=output["action"], official=output["official_output_allowed"], state=state)
        except ValueError:
            output.update(action="NO_ACTION", official_output_allowed=False, data=None, error_code="promotion_denied")
            output.pop("report", None)
            session.evaluate(EvalResult("official_promotion", False, category=Failure.STATE_CONTAMINATION_FAILURE, code="promotion_denied"))
            return session.finish(canonical(output), RunStatus.BLOCKED)

    def _evidence(self, session, policy, plan, packet, cutoff):
        if packet is None:
            return {}, []
        if not isinstance(packet, dict) or set(packet) != {"as_of_date", "facts"} or packet["as_of_date"] != cutoff or not isinstance(packet["facts"], list):
            raise ValueError("invalid_evidence")
        facts, sources = {}, []
        allowed = set(plan["required_capabilities"] + plan["optional_capabilities"])
        for item in packet["facts"]:
            if not isinstance(item, dict) or set(item) != {"capability", "scope_key", "data", "provenance", "fallback_status"}:
                raise ValueError("invalid_evidence")
            capability = item["capability"]
            if capability not in allowed or capability in facts or not isinstance(item["data"], dict):
                raise ValueError("invalid_evidence")
            if item["scope_key"] != session.request.scope.key:
                raise ValueError("scope_mismatch")
            if item["fallback_status"] != "none":
                raise ValueError("unverified_evidence")
            p = item["provenance"]
            required = {"source", "source_file", "source_timestamp", "publication_date", "observation_date", "data_period", "sha256"}
            if not isinstance(p, dict) or not required <= set(p) or any(not p[k] for k in required):
                raise ValueError("missing_provenance")
            if p["sha256"] != digest(item["data"]):
                raise ValueError("hash_mismatch")
            source_time = datetime.fromisoformat(p["source_timestamp"].replace("Z", "+00:00"))
            if source_time.tzinfo is None:
                raise ValueError("invalid_source_timestamp")
            if source_time.astimezone(ZoneInfo(self.market_timezone)).date().isoformat() > cutoff:
                raise ValueError("future_data")
            if plan["workflow"] in {"quant", "outlook"} and plan["parameters"].get("quant_spec"):
                if source_time > timestamp(plan["parameters"]["quant_spec"]["cutoff_timestamp"]):
                    raise ValueError("future_data")
            validate_values(item["data"], cutoff)
            if capability in MACRO_UNITS:
                expected = MACRO_UNITS[capability]
                if set(item["data"]) - set(expected) or any(p.get("units", {}).get(k) != expected[k] for k, v in item["data"].items() if v is not None):
                    raise ValueError("unit_or_metric_mismatch")
            if capability == "market_history":
                if (set(item["data"]) != set(policy.universe) or p.get("frequency") != "daily" or
                        p.get("adjustments") != {k: v["adjustment"] for k, v in policy.universe.items()} or
                        p.get("currencies") != {k: "USD" if k.endswith(".US") else "CNY" for k in policy.universe}):
                    raise ValueError("market_contract_mismatch")
                if max(row["trade_date"] for rows in item["data"].values() for row in rows) != p["observation_date"]:
                    raise ValueError("market_observation_mismatch")
                # Dated scores require the requested A-share close; holidays must
                # explicitly request the last completed trade date.
                if any(max(row["trade_date"] for row in rows) != cutoff for symbol, rows in item["data"].items() if not symbol.endswith(".US")):
                    raise ValueError("missing_required_field:market_history.requested_close")
            if capability.startswith("ai_") and p.get("schema_version") != "ai-inputs-v1":
                raise ValueError("ai_schema_mismatch")
            for field in ("publication_date", "observation_date"):
                if validate_date(p[field]) > cutoff:
                    raise ValueError("future_data")
            facts[capability] = json.loads(canonical(item["data"]))
            sources.append({"capability": capability, **p})
            self._archive(session, capability, item)
            session.step("evidence", {"capability": capability, "sha256": p["sha256"], "fallback_status": "none"})
        return facts, sources

    def _archive(self, session, name, obj):
        directory = self.artifact_root / session.request.scope.key / session.run_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ".json")
        raw = canonical(obj).encode()
        with path.open("xb") as stream:
            stream.write(raw)
        detail = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                  "as_of_date": session.request.as_of_date, "mode": "research", "publication_status": "staged",
                  "scope_key": session.request.scope.key, "run_id": session.run_id}
        session.repository.append("artifacts", session.run_id, session.request.scope,
                                  detail_json=canonical(detail), recorded_at=now())
        return detail
