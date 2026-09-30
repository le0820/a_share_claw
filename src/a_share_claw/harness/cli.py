"""Core execution and scoped trace commands; model execution is explicit opt-in."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import RunRequest, RunStatus, Scope
from .engine import Harness
from .policy import PolicyBundle
from .trace import TraceRepository


def scope_options(parser):
    parser.add_argument("--platform", default="local")
    parser.add_argument("--user", default="local-user")
    parser.add_argument("--chat", default="local-chat")
    parser.add_argument("--agent-key", default="default")


def add_harness_parser(sub):
    trace = sub.add_parser("trace", help="Read an authorized run summary or list")
    trace.add_argument("run_id", nargs="?")
    trace.add_argument("--list", action="store_true")
    trace.add_argument("--full", action="store_true")
    scope_options(trace)
    harness = sub.add_parser("harness", help="Provider-free planning, scoring and replay")
    commands = harness.add_subparsers(dest="harness_command", required=True)
    for name in ("plan", "run", "replay", "state"):
        command = commands.add_parser(name)
        scope_options(command)
        if name == "replay":
            command.add_argument("run_id")
        else:
            command.add_argument("--workflow", choices=["macro", "ai", "company", "industry", "mixed", "quant", "outlook", "general"], default="macro")
            command.add_argument("--date", required=name == "run")
            if name in {"plan", "run"}:
                command.add_argument("--research-spec", type=Path)
                command.add_argument("--quant-spec", type=Path)
                command.add_argument("--outlook-spec", type=Path)
                command.add_argument("--mixed-spec", type=Path)
                command.add_argument("--question")
            if name == "run":
                command.add_argument("packet_file", type=Path)
                command.add_argument("--mode", choices=["replay", "research", "official"], default="replay")
                command.add_argument("--current-ai-pct", type=float, default=57.5)
                command.add_argument("--model-executor", choices=["configured"], help="Use configured endpoint for research roles and independent review")


def run_harness(args, config, storage):
    context = storage.get_or_create_context(args.platform, args.user, args.chat, agent_key=args.agent_key)
    scope = Scope.from_context(config.root_dir, context)
    repo = TraceRepository(storage)
    if args.command == "trace":
        try:
            if args.list:
                result = {"runs": repo.list_runs(scope)}
            elif args.run_id:
                result = repo.read(args.run_id, scope)
                if not args.full:
                    result = {**{key: result[key] for key in ("run_id", "status", "started_at", "finished_at", "request", "outcome")},
                              "scope_key": scope.key,
                              "steps": [{"stage": row["stage"], "status": row["status"], "detail": row["detail"]} for row in result["run_steps"]],
                              "tool_calls": len(result["tool_calls"]), "model_calls": len(result["model_calls"]),
                              "artifacts": result["artifacts"], "evaluations": result["evaluations"]}
            else:
                raise ValueError("Provide a run_id or --list")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (LookupError, ValueError):
            print(json.dumps({"ok": False, "error_code": "trace_not_found"}))
            return 2
    if args.harness_command == "state":
        try:
            state = repo.read_state(scope, args.workflow, as_of_date=args.date)
            if state is None:
                raise LookupError("No scoped official state at this cutoff")
            report = state.get("report", {})
            path = Path(report["path"]).resolve()
            path.relative_to((config.data_dir / "harness_runs" / scope.key / state["run_id"]).resolve())
            if report.get("scope_key") != scope.key or report.get("run_id") != state["run_id"] or hashlib.sha256(path.read_bytes()).hexdigest() != report["sha256"]:
                raise ValueError("Official report integrity failure")
            print(json.dumps({"ok": True, "scope_key": scope.key, "publication_status": "published", "state": state}, ensure_ascii=False, indent=2))
            return 0
        except (ValueError, OSError, KeyError, TypeError):
            print(json.dumps({"ok": False, "scope_key": scope.key, "error_code": "official_state_integrity_error"}))
            return 2
        except LookupError:
            print(json.dumps({"ok": False, "scope_key": scope.key, "error_code": "official_state_not_found"}))
            return 2
    engine = Harness(config.root_dir, storage, config.data_dir / "harness_runs", config.market_timezone)
    if args.harness_command == "replay":
        try:
            source = repo.read(args.run_id, scope)
            if source["status"] != "succeeded":
                raise ValueError("Replay requires a successful archived computation")
            snapshot = next(row["detail"] for row in source["run_steps"] if row["stage"] == "policy_snapshot")
            if snapshot["version"] != PolicyBundle(config.root_dir).version:
                raise ValueError("Pinned policy changed")
            facts = []
            for artifact in source["artifacts"]:
                detail = artifact["detail"]
                path = Path(detail["path"]).resolve()
                path.relative_to((config.data_dir / "harness_runs" / scope.key / args.run_id).resolve())
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != detail["sha256"]:
                    raise ValueError("Artifact hash mismatch")
                obj = json.loads(raw)
                if "capability" in obj:
                    facts.append(obj)
            workflow = next(row["detail"]["workflow"] for row in source["run_steps"] if row["stage"] == "route")
            if workflow not in {"macro", "ai", "quant"}:
                raise ValueError("This workflow has no offline replay executor")
            parameters = next((row["detail"] for row in source["run_steps"] if row["stage"] == "workflow_parameters"), {})
            request = RunRequest(scope, "Offline replay", source["request"]["as_of_date"], "replay", workflow, host="cli")
            outcome = engine.run(request, {"as_of_date": request.as_of_date, "facts": facts}, replay_of=args.run_id,
                                 current_ai_pct=parameters.get("current_ai_pct", 57.5), quant_spec=parameters.get("quant_spec"))
        except (LookupError, ValueError, KeyError, StopIteration, OSError):
            print(json.dumps({"ok": False, "error_code": "replay_unavailable"}))
            return 2
    else:
        try:
            packet = json.loads(args.packet_file.read_text()) if args.harness_command == "run" else None
            research_spec = json.loads(args.research_spec.read_text()) if getattr(args, "research_spec", None) else None
            quant_spec = json.loads(args.quant_spec.read_text()) if getattr(args, "quant_spec", None) else None
            outlook_spec = json.loads(args.outlook_spec.read_text()) if getattr(args, "outlook_spec", None) else None
            mixed_spec = json.loads(args.mixed_spec.read_text()) if getattr(args, "mixed_spec", None) else None
            request = RunRequest(scope, getattr(args, "question", None) or "Provider-independent " + args.workflow, args.date,
                                 "plan" if args.harness_command == "plan" else args.mode, args.workflow, host="cli")
        except (ValueError, OSError):
            print(json.dumps({"ok": False, "error_code": "invalid_request"}))
            return 2
        adapter = None
        if getattr(args, "model_executor", None):
            if args.workflow not in {"company", "industry", "outlook", "mixed"}:
                print(json.dumps({"ok": False, "error_code": "unsupported_model_workflow"}))
                return 2
            from ..sdk_research import SDKResearchAdapter
            adapter = SDKResearchAdapter(config)
        outcome = engine.run(request, packet, current_ai_pct=getattr(args, "current_ai_pct", 57.5),
                             research_spec=research_spec, research_adapter=adapter, quant_spec=quant_spec, outlook_spec=outlook_spec, mixed_spec=mixed_spec)
    print(outcome.output)
    return 0 if outcome.status == RunStatus.SUCCEEDED else 2
