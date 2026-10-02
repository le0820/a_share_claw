"""Core execution and scoped trace commands; model execution is explicit opt-in."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import RunRequest, RunStatus, Scope
from .engine import Harness
from .policy import PolicyBundle
from .trace import TraceRepository
from .delivery import read_report


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
    report = commands.add_parser("report", help="Read a verified scoped report by run_id")
    scope_options(report)
    report.add_argument("run_id")
    report.add_argument("--format", choices=["markdown", "html", "json"], default="markdown")
    report.add_argument("--date", help="Reject reports later than this cutoff")
    watch = commands.add_parser("watch-plan", help="Freeze four-index spec with reviewed exchange calendars; no acquisition")
    scope_options(watch)
    watch.add_argument("--date", required=True)
    watch.add_argument("--window-start", required=True)
    watch.add_argument("--window-end", required=True)
    watch.add_argument("--cutoff", required=True)
    for name in ("nyse", "nasdaq", "sse", "szse"):
        watch.add_argument("--"+name+"-calendar", type=Path, required=True)
    ui = commands.add_parser("ui", help="Export an authorized offline report and ReAct workbench")
    scope_options(ui)
    ui.add_argument("--run-id")
    ui.add_argument("--date", help="Only include runs with an explicit date at or before cutoff")
    ui.add_argument("--limit", type=int, default=20)
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
            if name == "plan":
                command.add_argument("--model-executor",choices=["configured"],help="Propose and review a tool-free core framework")
                command.add_argument("--planning-constraints",type=Path,help="Trusted immutable planner constraints")
            if name == "run":
                command.add_argument("packet_file", type=Path,nargs="?")
                command.add_argument("--source-contract",type=Path,help="Explicit trusted-host plugin bindings; research mode only")
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
    if args.harness_command == "watch-plan":
        from .market_watch import freeze_watch
        from ..data_plugins.watch import watch_source_contract
        from ..data_plugins.core import DataError
        from .workbench import export_workbench
        try:
            host = freeze_watch(args.date,args.window_start,args.window_end,args.cutoff,
                {"XNYS":args.nyse_calendar,"XNAS":args.nasdaq_calendar,"XSHG":args.sse_calendar,"XSHE":args.szse_calendar})
            outcome = Harness(config.root_dir,storage,config.data_dir/"harness_runs",config.market_timezone).run(
                RunRequest(scope,"Freeze S&P500/Nasdaq100/CSI300/ChiNext daily visualization",args.date,"plan","quant"),quant_spec=host["quant_spec"])
            if outcome.status != RunStatus.SUCCEEDED:
                print(outcome.output)
                return 2
            view = export_workbench(repo,scope,config.data_dir/"harness_runs",config.data_dir/"harness_views",run_id=outcome.run_id)
            folder = Path(view["index"]).parent
            (folder/"quant_spec.json").write_text(json.dumps(host["quant_spec"],ensure_ascii=False,indent=2),encoding="utf-8")
            (folder/"source_contract.json").write_text(json.dumps(watch_source_contract(host["quant_spec"],args.date),ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps({"ok":True,"run_id":outcome.run_id,"index":view["index"],
                "quant_spec":str(folder/"quant_spec.json"),"source_contract":str(folder/"source_contract.json"),
                "completion":"framework_only","action":"NO_ACTION"},ensure_ascii=False,indent=2))
            return 0
        except (ValueError,OSError,KeyError,TypeError,DataError):
            print(json.dumps({"ok":False,"error_code":"invalid_watch_contract"}))
            return 2
    if args.harness_command == "ui":
        from .workbench import export_workbench
        try:
            result = export_workbench(repo,scope,config.data_dir/"harness_runs",config.data_dir/"harness_views",
                run_id=args.run_id,as_of_date=args.date,limit=args.limit)
            print(json.dumps({"ok":True,**result},ensure_ascii=False,indent=2))
            return 0
        except LookupError:
            print(json.dumps({"ok":False,"error_code":"view_not_found"}))
            return 2
        except (ValueError,OSError,KeyError,TypeError):
            print(json.dumps({"ok":False,"error_code":"view_integrity_error"}))
            return 2
    if args.harness_command == "report":
        try:
            delivery = read_report(repo, scope, args.run_id, config.data_dir / "harness_runs", as_of_date=args.date)
            if args.format == "html" and delivery["html"] is None:
                raise LookupError("Legacy report has no HTML artifact")
            print(delivery[args.format] if args.format in {"markdown", "html"} else json.dumps({k:v for k,v in delivery.items() if k not in {"markdown", "html"}}, ensure_ascii=False, indent=2))
            return 0
        except LookupError:
            print(json.dumps({"ok":False,"error_code":"report_not_found"}))
            return 2
        except (ValueError, OSError, KeyError, TypeError):
            print(json.dumps({"ok":False,"error_code":"report_integrity_error"}))
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
            if "report_markdown" in state:
                delivery = read_report(repo, scope, state["run_id"], config.data_dir / "harness_runs", as_of_date=args.date)
                if delivery["publication_status"] != "published":
                    raise ValueError("Official report is not published")
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
                if path.suffix in {".md", ".html"}:
                    continue  # the hash was checked; rendered reports are not FactPackets
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
            packet = json.loads(args.packet_file.read_text()) if args.harness_command == "run" and args.packet_file else None
            source_adapter=None
            if getattr(args,"source_contract",None):
                from ..data_plugins.adapter import PluginEvidenceAdapter
                source_adapter=PluginEvidenceAdapter(config.data_dir/"source_runs",json.loads(args.source_contract.read_text()))
            if args.harness_command=="run" and packet is None and source_adapter is None:raise ValueError("Missing evidence or source contract")
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
            if args.harness_command == "plan":
                from ..sdk_research import SDKResearchAdapter
                try:
                    constraints=json.loads(args.planning_constraints.read_text()) if getattr(args,"planning_constraints",None) else {}
                    for key,value in (("research_spec",research_spec),("quant_spec",quant_spec),("outlook_spec",outlook_spec),("mixed_spec",mixed_spec)):
                        if value is not None:
                            if key in constraints and constraints[key]!=value:
                                raise ValueError("Conflicting planner inputs")
                            constraints[key]=value
                except (ValueError,OSError,TypeError):
                    print(json.dumps({"ok":False,"error_code":"invalid_request"}))
                    return 2
                outcome=engine.run(request,framework_adapter=SDKResearchAdapter(config),planning_constraints=constraints)
                print(outcome.output)
                return 0 if outcome.status==RunStatus.SUCCEEDED else 2
            if args.workflow not in {"company", "industry", "outlook", "mixed"}:
                print(json.dumps({"ok": False, "error_code": "unsupported_model_workflow"}))
                return 2
            from ..sdk_research import SDKResearchAdapter
            adapter = SDKResearchAdapter(config)
        outcome = engine.run(request, packet, current_ai_pct=getattr(args, "current_ai_pct", 57.5),
                             research_spec=research_spec, research_adapter=adapter, quant_spec=quant_spec, outlook_spec=outlook_spec, mixed_spec=mixed_spec,evidence_adapter=source_adapter)
    print(outcome.output)
    return 0 if outcome.status == RunStatus.SUCCEEDED else 2
