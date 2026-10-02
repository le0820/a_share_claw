import argparse
import asyncio
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from a_share_claw.agent import InvestmentAgent
from a_share_claw.config import AppConfig
from a_share_claw.db import Storage
from a_share_claw.harness.cli import run_harness
from a_share_claw.harness.contracts import RunStatus, Scope
from a_share_claw.harness.trace import TraceRepository
from a_share_claw.research_tools import ResearchRuntime
from a_share_claw.scheduler import Scheduler
from test_harness import DAY, ROOT, ai_packet, packet


@pytest.fixture
def host(tmp_path):
    config = dataclasses.replace(AppConfig.from_env(ROOT), data_dir=tmp_path,
                                 database_path=tmp_path / "trace.sqlite", model_base_url=None, model_api_key=None, fake_ai=False)
    storage = Storage(config.database_path)
    storage.init()
    context = storage.get_or_create_context("local", "local-user", "local-chat")
    yield config, storage, context, tmp_path
    storage.close()


def test_rule_tool_keeps_policy_and_removes_historical_observations(host):
    config, _, context, _ = host
    output = asyncio.run(ResearchRuntime(config, context).get_compiled_rule("l1", "us"))
    assert "_current_data" not in output and "~70% (Q1 2026加权)" not in output
    assert "scoring_rules" in output
    output = asyncio.run(ResearchRuntime(config, context).get_compiled_rule("l3"))
    assert "_example_values" not in output


def test_agent_blocks_unsafe_prose_and_records_each_model_request(host):
    from test_harness_framework import FrameworkEndpoint
    from test_sdk_research import configured
    config,storage,context,_=host;agent=InvestmentAgent(configured(config),storage);endpoint=FrameworkEndpoint()
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client):
        outcome=asyncio.run(agent.run_result(context,"2026-07-13 分析合成供应商产业链，不得虚构加仓"))
    assert outcome.status==RunStatus.BLOCKED and outcome.action=="NO_ACTION"
    trace=TraceRepository(storage).read(outcome.run_id,Scope.from_context(ROOT,context))
    assert len(trace["model_calls"])==2 and not trace["tool_calls"]
    assert all(row["status"]=="ok" for row in trace["model_calls"])
    assert all(row["detail"]["cached_read_tokens"] is None and row["detail"]["cache_hit"] is None for row in trace["model_calls"])
    assert trace["request"]["as_of_date"]==DAY
    assert json.loads(outcome.output)["error_code"]=="missing_required_data"
    assert not outcome.official_output_allowed


def test_fake_failure_and_cancellation_all_have_terminal_runs(host):
    from test_harness_framework import FrameworkEndpoint
    from test_sdk_research import configured
    config,storage,context,_=host
    async def exercise():
        result=await InvestmentAgent(dataclasses.replace(config,fake_ai=True),storage).run_result(context,"fake offline request")
        assert result.status==RunStatus.SUCCEEDED
        failed=InvestmentAgent(configured(config),storage)
        async def failure(*args,**kwargs):raise RuntimeError("api_key=credential-marker")
        with patch("agents.Runner.run",new=failure):
            result=await failed.run_result(context,"2026-07-13 分析合成产业链")
        assert result.status==RunStatus.FAILED and "credential-marker" not in result.output
        with patch("a_share_claw.sdk_research.SDKResearchAdapter._framework",new=failure):
            # A model exception is normalized by core, not rethrown into the host.
            result=await failed.run_result(context,"2026-07-13 分析合成产业链")
        assert result.status==RunStatus.FAILED
        async def cancelled(*args,**kwargs):raise asyncio.CancelledError()
        with patch("agents.Runner.run",new=cancelled):
            result=await failed.run_result(context,"2026-07-13 分析合成产业链")
        assert result.status==RunStatus.CANCELLED
        statuses={row["status"] for row in TraceRepository(storage).list_runs(Scope.from_context(ROOT,context))}
        assert statuses=={"succeeded","failed","cancelled"}
    asyncio.run(exercise())


def test_model_declared_empty_plan_cannot_complete_macro_workflow(host):
    from test_harness_framework import review
    from test_sdk_research import configured
    config,storage,context,_=host;agent=InvestmentAgent(configured(config),storage)
    async def runner(sdk_agent,message,**kwargs):
        entry=json.loads(message)
        if "candidate" in entry:
            from a_share_claw.harness.research import RoleRequest
            result=review(RoleRequest(message,30))
        else:result={"framework":"Synthetic macro framework","parameters":{},"unresolved_constraints":[]}
        assert not sdk_agent.tools
        return SimpleNamespace(final_output=json.dumps(result))
    with patch("agents.Runner.run",new=runner):
        outcome=asyncio.run(agent.run_result(context,"2026-07-13 正式宏观评分"))
    assert outcome.status==RunStatus.BLOCKED and outcome.action=="NO_ACTION"
    result=json.loads(outcome.output)
    assert result["error_code"]=="missing_required_data" and result["mode"]=="research"
    assert set(result["gaps"])=={"cn_macro","us_macro","market_history"}
    assert TraceRepository(storage).read_state(Scope.from_context(ROOT,context),"macro") is None


def test_scheduler_does_not_announce_failed_runs_as_completed(host):
    config,storage,context,_=host
    storage.add_task(context.user_id,context.conversation_id,context.chat_id,"macro","data unavailable","2000-01-01T00:00:00+00:00")
    sent=[]
    async def notify(chat,text):sent.append(text)
    asyncio.run(Scheduler(config,storage,InvestmentAgent(config,storage),notify).run_once())
    row=storage.list_tasks(context.user_id,include_done=True)[0]
    assert row["status"]=="failed" and "run_id=" in row["last_error"]
    assert "定时任务未完成" in sent[0] and "定时任务完成：" not in sent[0]


def test_cli_run_trace_replay_and_tampered_archive(host, capsys):
    config, storage, context, tmp = host
    scope = Scope.from_context(ROOT, context)
    facts = tmp / "facts.json"
    facts.write_text(json.dumps(packet(scope)))
    common = dict(platform="local", user="local-user", chat="local-chat", agent_key="default")
    args = argparse.Namespace(command="harness", harness_command="run", packet_file=facts, workflow="macro", date=DAY,
                              mode="replay", current_ai_pct=57.5, **common)
    assert run_harness(args, config, storage) == 0
    first = json.loads(capsys.readouterr().out)
    trace_args = argparse.Namespace(command="trace", run_id=first["run_id"], list=False, full=False, **common)
    assert run_harness(trace_args, config, storage) == 0
    trace = json.loads(capsys.readouterr().out)
    assert trace["status"] == "succeeded"
    assert {Path(row["detail"]["path"]).name for row in trace["artifacts"]} >= {"report.json", "report.md", "report.html", "computed_output.json"}
    assert Path(trace["artifacts"][0]["detail"]["path"]).name == "plan.json"
    replay_args = argparse.Namespace(command="harness", harness_command="replay", run_id=first["run_id"], **common)
    assert run_harness(replay_args, config, storage) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["run_id"] != first["run_id"] and repeated["data"] == first["data"]
    assert repeated["action"] == "NO_ACTION"
    source = TraceRepository(storage).read(first["run_id"], scope)
    Path(source["artifacts"][0]["detail"]["path"]).write_text("tampered")
    assert run_harness(replay_args, config, storage) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "replay_unavailable"


def test_macro_strategy_matches_retained_pipeline(host):
    import csv
    import os
    import subprocess
    import sys
    from a_share_claw.harness.engine import Harness
    from a_share_claw.harness.contracts import RunRequest
    config, storage, context, tmp = host
    scope = Scope.from_context(ROOT, context)
    facts = packet(scope)
    raw = tmp / "legacy/raw"
    market = raw / "market"
    market.mkdir(parents=True)
    for fact in facts["facts"]:
        capability, data = fact["capability"], fact["data"]
        if capability in {"cn_macro", "us_macro"}:
            prefix = "raw_macro" if capability == "cn_macro" else "raw_macro_us"
            meta = {"as_of_date": "20260713", "source": "synthetic fixture", "generated_at": DAY + "T15:30:00Z",
                    "source_timestamp": DAY + "T15:30:00Z", "data_period": "fixture", "fallback_status": "none"}
            (raw / (prefix + "_20260713.json")).write_text(json.dumps({**data, "as_of_date": "20260713", "source": "synthetic fixture", "_meta": meta}))
        else:
            for symbol, rows in data.items():
                with (market / (symbol.replace(".", "_") + "_daily.csv")).open("w") as stream:
                    writer = csv.DictWriter(stream, fieldnames=["trade_date", "close"])
                    writer.writeheader()
                    writer.writerows(rows)
    env = {**os.environ, "ASCLAW_DATA_DIR": str(tmp / "legacy")}
    for script in ("p1_upgrade.py", "run_scoring.py"):
        proc = subprocess.run([sys.executable, str(ROOT / "src/pipeline" / script), "--date", "20260713"], cwd=ROOT, env=env, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    previous = json.loads((tmp / "legacy/scores/scores_composite_20260713.json").read_text())
    outcome = Harness(ROOT, storage, tmp / "core").run(RunRequest(scope, "macro", DAY, "replay", "macro"), facts)
    current = json.loads(outcome.output)["data"]
    assert {key: current[key] for key in ("L1", "L2", "L3", "composite")} == {key: previous[key] for key in ("L1", "L2", "L3", "composite")}


def test_ai_cli_replay_preserves_explicit_current_position(host, capsys):
    config, storage, context, tmp = host
    facts = tmp / "ai-facts.json"
    facts.write_text(json.dumps(ai_packet(Scope.from_context(ROOT, context))))
    common = dict(platform="local", user="local-user", chat="local-chat", agent_key="default")
    args = argparse.Namespace(command="harness", harness_command="run", packet_file=facts, workflow="ai", date=DAY,
                              mode="replay", current_ai_pct=63, **common)
    assert run_harness(args, config, storage) == 0
    source = json.loads(capsys.readouterr().out)
    args = argparse.Namespace(command="harness", harness_command="replay", run_id=source["run_id"], **common)
    assert run_harness(args, config, storage) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["data"] == source["data"] and repeated["action"] == "NO_ACTION"
    trace = TraceRepository(storage).read(repeated["run_id"], Scope.from_context(ROOT, context))
    assert next(r["detail"] for r in trace["run_steps"] if r["stage"] == "workflow_parameters")["current_ai_pct"] == 63


@pytest.mark.parametrize("message", ["宏观每日评分", "分析 HBM 产业链", "每日评分并研究半导体产业链"])
def test_sdk_context_loads_versioned_protocol_without_deployment_archive(host, message):
    from a_share_claw.research_context import build_research_context
    config, _, context, _ = host
    bundle = build_research_context(config, context, message, include_state=False)
    assert "src/a_share_claw/RESEARCH_OPERATIONS.md" in bundle.loaded_files
    assert "PLAN -> EVIDENCE_GATE" in bundle.instructions
    assert "data/deepresearch/OPERATIONS.md" not in bundle.loaded_files
    assert bundle.state_scope == "withheld_plugin_only"


def test_sdk_missing_protocol_blocks_before_model_and_preserves_context_trace(host):
    import shutil
    from a_share_claw.harness.policy import PolicyBundle
    config,storage,context,tmp=host;checkout=tmp/"incomplete"
    for name in PolicyBundle(ROOT).hashes:
        target=checkout/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    (checkout/"src/a_share_claw/RESEARCH_OPERATIONS.md").unlink()
    agent=InvestmentAgent(dataclasses.replace(config,root_dir=checkout),storage)
    with patch("agents.Runner.run") as runner:
        outcome=asyncio.run(agent.run_result(context,"2026-07-13 分析 HBM 产业链"))
    runner.assert_not_called();assert outcome.status==RunStatus.BLOCKED
    assert "policy_context_missing" in outcome.output
    trace=TraceRepository(storage).read(outcome.run_id,Scope.from_context(checkout,context))
    detail=next(row["detail"] for row in trace["run_steps"] if row["stage"]=="context")
    assert detail["missing_files"]==["src/a_share_claw/RESEARCH_OPERATIONS.md"]
    assert not trace["model_calls"] and not trace["tool_calls"]


def test_cli_reads_only_scoped_official_state_at_cutoff(host, capsys):
    config, storage, context, tmp = host
    from a_share_claw.harness.engine import Harness
    from a_share_claw.harness.contracts import RunRequest
    scope = Scope.from_context(ROOT, context)
    outcome = Harness(ROOT, storage, tmp / "harness_runs").run(RunRequest(scope, "Fixed macro", DAY, "official", "macro"), packet(scope))
    assert outcome.status == RunStatus.SUCCEEDED
    args = argparse.Namespace(command="harness", harness_command="state", workflow="macro", date=DAY,
                              platform="local", user="local-user", chat="local-chat", agent_key="default")
    assert run_harness(args, config, storage) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved["state"]["run_id"] == outcome.run_id and saved["scope_key"] == scope.key
    args.date = "2026-07-12"
    assert run_harness(args, config, storage) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "official_state_not_found"
    args.date, args.user = DAY, "other-user"
    assert run_harness(args, config, storage) == 2
    assert "data" not in json.loads(capsys.readouterr().out)
    args.user = "local-user"
    Path(saved["state"]["report"]["path"]).write_text("tampered report")
    assert run_harness(args, config, storage) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "official_state_integrity_error"
