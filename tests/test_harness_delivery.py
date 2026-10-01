"""Readable delivery acceptance after implementation; all inputs are synthetic."""
import argparse
import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_claw.agent import InvestmentAgent
from a_share_claw.harness.cli import run_harness
from a_share_claw.harness.contracts import RunRequest, RunStatus, Scope, canonical
from a_share_claw.harness.delivery import read_report
from a_share_claw.harness.markdown import render_markdown
from a_share_claw.harness.trace import TraceRepository
from test_harness import DAY, ROOT, environment, packet, ai_packet
from test_harness_hosts import host
from test_harness_research import spec, research_packet, role_reply, fixture_review
from test_harness_quant import AS_OF, quant_spec, quant_packet, outlook_spec, outlook_packet, outlook_reply, outlook_review
from test_harness_mixed import mixed_spec, mixed_packet


def compute(environment, workflow="macro", mode="research"):
    storage,scope,engine,_=environment
    day=AS_OF if workflow in {"quant","outlook"} else DAY
    kwargs={}
    if workflow=="macro": facts=packet(scope)
    elif workflow=="ai": facts=ai_packet(scope)
    elif workflow in {"industry","company"}:
        facts=research_packet(scope);kwargs={"research_spec":spec(True,True),"role_runner":role_reply,"semantic_reviewer":fixture_review}
    elif workflow=="quant": facts=quant_packet(scope);kwargs={"quant_spec":quant_spec()}
    elif workflow=="outlook":
        facts=outlook_packet(scope);kwargs={"outlook_spec":outlook_spec(),"role_runner":outlook_reply,"semantic_reviewer":outlook_review}
    else:
        facts=mixed_packet(scope);kwargs={"mixed_spec":mixed_spec(),"role_runner":role_reply,"semantic_reviewer":fixture_review}
    return engine.run(RunRequest(scope,"Synthetic report acceptance",day,mode,workflow),facts,**kwargs)


@pytest.mark.parametrize("workflow",["macro","ai","company","industry","quant","outlook","mixed"])
def test_readable_reports_roundtrip_canonical_json_and_preserve_no_action(environment,workflow):
    storage,scope,engine,_=environment
    out=compute(environment,workflow)
    assert out.status==RunStatus.SUCCEEDED,out.output
    delivered=read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)
    result=json.loads(out.output)
    archived=Path(result["report_markdown"]["path"]).read_text()
    report=json.loads(Path(result["report"]["path"]).read_text())
    assert render_markdown(report)==archived
    assert delivered["publication_status"]=="research" and delivered["action"]=="NO_ACTION"
    assert not delivered["official_output_allowed"] and delivered["markdown"].endswith(archived)
    assert "来源表" in archived and "fallback" in archived and "staged" in archived
    assert report["as_of_date"] in archived and out.run_id in archived
    if workflow in {"company","industry","outlook"}:
        assert "准入事实" in archived and "角色推断" in archived and "未知项" in archived and "风险与监控" in archived
    if workflow=="industry":
        assert archived.index("多方 / initial") < archived.index("空方 / initial") < archived.index("多方 / rebuttal") < archived.index("风险 / final")
    if workflow=="outlook":
        assert "条件基准情景" in archived and "核心计算统计" in archived
        assert "Composite 保持" not in archived
    if workflow=="quant": assert "声明会话数" in archived and "ratio" in archived


def test_exact_published_report_remains_readable_after_same_day_newer_run(environment):
    storage,scope,engine,_=environment
    first=compute(environment,mode="official");second=compute(environment,mode="official")
    assert first.status==second.status==RunStatus.SUCCEEDED
    repo=TraceRepository(storage)
    assert repo.read_state(scope,"macro")["run_id"]==second.run_id
    earlier=read_report(repo,scope,first.run_id,engine.artifact_root,as_of_date=DAY)
    assert earlier["publication_status"]=="published" and earlier["official_output_allowed"]
    assert earlier["action"]=="POSITION_BAND" and "读取状态：已正式发布" in earlier["markdown"]
    with pytest.raises(ValueError): read_report(repo,scope,first.run_id,engine.artifact_root,as_of_date="2026-07-12")
    other=Scope(scope.workspace,"other-principal",scope.session)
    with pytest.raises(LookupError):read_report(repo,other,first.run_id,engine.artifact_root)


@pytest.mark.parametrize("file",["report.json","report.md","computed_output.json"])
def test_corrupt_artifact_is_never_delivered(environment,file):
    storage,scope,engine,_=environment
    out=compute(environment,mode="official")
    report_dir=engine.artifact_root/scope.key/out.run_id
    (report_dir/file).write_text("Corrupted report fixture")
    with pytest.raises(ValueError):read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)


def test_failed_run_cannot_deliver_a_staged_report(environment):
    storage,scope,engine,_=environment
    archive=engine._archive
    def failed(session,name,obj):
        if name=="computed_output":raise OSError("fixture final archive failure")
        return archive(session,name,obj)
    with patch.object(engine,"_archive",side_effect=failed):out=compute(environment,mode="official")
    assert out.status==RunStatus.FAILED
    assert list((engine.artifact_root/scope.key/out.run_id).glob("report.md"))
    with pytest.raises(LookupError):read_report(TraceRepository(storage),scope,out.run_id,engine.artifact_root)
    assert TraceRepository(storage).read_state(scope,"macro") is None


def test_markdown_archive_failure_preserves_prior_official_state(environment):
    storage,scope,engine,_=environment
    good=compute(environment,mode="official");assert good.status==RunStatus.SUCCEEDED
    repo=TraceRepository(storage);previous=repo.read_state(scope,"macro")
    archive=engine._archive_content
    def failed(session,filename,raw):
        if filename=="report.md":raise OSError("fixture Markdown failure")
        return archive(session,filename,raw)
    with patch.object(engine,"_archive_content",side_effect=failed):bad=compute(environment,mode="official")
    assert bad.status==RunStatus.FAILED
    result=json.loads(bad.output)
    assert "report" not in result and "report_markdown" not in result and not bad.official_output_allowed
    assert repo.read_state(scope,"macro")==previous


def test_renderer_cannot_introduce_unchecked_action_prose(environment):
    storage,scope,_,_=environment
    with patch("a_share_claw.harness.engine.render_markdown",return_value="# Official BUY 70%"):
        out=compute(environment,mode="official")
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="report_contract_failure"
    assert TraceRepository(storage).read_state(scope,"macro") is None


def test_source_and_model_text_are_literal_and_cannot_inject_markdown(environment):
    _,_,_,_=environment
    out=compute(environment,"industry")
    report=json.loads(Path(json.loads(out.output)["report"]["path"]).read_text())
    malicious="[false label](https://fixture.invalid) | ![remote image](https://fixture.invalid/x)\n# fake heading <script>bad</script>"
    report["data"]["role_outputs"]["jia_zhi:initial"]["answers"][0]["inference"]=malicious
    report["source_table"][0]["source"]=malicious
    text=render_markdown(report)
    assert "\n# fake heading" not in text and "<script>" not in text and "![remote image]" not in text
    assert "&lt;script&gt;" in text and "\\[false label\\]" in text
    assert "\\|" in text and "\\!\\[remote image" in text


def test_cli_and_host_share_scope_gate_and_state_checks_markdown(host,capsys):
    config,storage,context,path=host
    scope=Scope.from_context(ROOT,context)
    from a_share_claw.harness.engine import Harness
    out=Harness(ROOT,storage,path/"harness_runs").run(RunRequest(scope,"Synthetic delivery",DAY,"official","macro"),packet(scope))
    assert out.status==RunStatus.SUCCEEDED
    common={"platform":"local","user":"local-user","chat":"local-chat","agent_key":"default"}
    args=argparse.Namespace(command="harness",harness_command="report",run_id=out.run_id,date=DAY,format="markdown",**common)
    assert run_harness(args,config,storage)==0
    text=capsys.readouterr().out
    assert text.startswith("读取状态：已正式发布") and "宏观评分报告" in text
    agent=InvestmentAgent(config,storage)
    assert agent.read_core_report(context,out.run_id,as_of_date=DAY)==text.rstrip("\n")+"\n"
    args.format="json"
    assert run_harness(args,config,storage)==0
    value=json.loads(capsys.readouterr().out)
    assert value["official_output_allowed"] and value["publication_status"]=="published"
    args.user="other-user"
    assert run_harness(args,config,storage)==2
    assert json.loads(capsys.readouterr().out)["error_code"]=="report_not_found"
    args.user="local-user"
    Path(json.loads(out.output)["report_markdown"]["path"]).write_text("tampered")
    assert run_harness(args,config,storage)==2
    assert json.loads(capsys.readouterr().out)["error_code"]=="report_integrity_error"
    args=argparse.Namespace(command="harness",harness_command="state",workflow="macro",date=DAY,**common)
    assert run_harness(args,config,storage)==2
    assert json.loads(capsys.readouterr().out)["error_code"]=="official_state_integrity_error"


def test_publisher_cannot_replace_business_evaluators_with_one_passed_permission_check(environment):
    from a_share_claw.harness.contracts import EvalResult
    from a_share_claw.harness.runtime import RunSession
    storage,scope,engine,_=environment
    session=RunSession(storage,RunRequest(scope,"Synthetic bypass attempt",DAY,"official","macro"))
    session.step("route",{"workflow":"macro"})
    report=engine._archive(session,"report",{"fixture":"not evaluated"})
    markdown=engine._archive_content(session,"report.md",b"fixture unchecked report")
    session.evaluate(EvalResult("tool_permission",True))
    state={"run_id":session.run_id,"workflow":"macro","as_of_date":DAY,"report":report,"report_markdown":markdown}
    with pytest.raises(ValueError,match="promotion"):
        session.finish("unchecked candidate",official=True,action="POSITION_BAND",state=state)
    assert TraceRepository(storage).read_state(scope,"macro") is None
    session.finish("NO_ACTION",RunStatus.BLOCKED)


def test_actual_cli_parser_reads_and_replays_without_models(tmp_path):
    import os
    import subprocess
    import sys
    from a_share_claw.db import Storage
    from a_share_claw.harness.engine import Harness
    storage=Storage(tmp_path/"a_share_claw.sqlite3");storage.init()
    context=storage.get_or_create_context("local","local-user","local-chat")
    scope=Scope.from_context(ROOT,context)
    out=Harness(ROOT,storage,tmp_path/"harness_runs").run(RunRequest(scope,"Synthetic CLI delivery",DAY,"research","macro"),packet(scope))
    storage.close()
    assert out.status==RunStatus.SUCCEEDED
    env={**os.environ,"ASCLAW_DATA_DIR":str(tmp_path),"ASCLAW_DATA_PROVIDERS":""}
    command=[sys.executable,"-m","a_share_claw","harness","report",out.run_id,"--format","json","--date",DAY]
    process=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
    assert process.returncode==0,process.stderr
    delivered=json.loads(process.stdout)
    assert delivered["publication_status"]=="research" and delivered["action"]=="NO_ACTION"
    process=subprocess.run(command+["--user","other-user"],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
    assert process.returncode==2 and json.loads(process.stdout)["error_code"]=="report_not_found"
    process=subprocess.run([sys.executable,"-m","a_share_claw","harness","replay",out.run_id],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
    assert process.returncode==0,process.stderr
    replay=json.loads(process.stdout)
    assert replay["action"]=="NO_ACTION" and replay["data"]==json.loads(out.output)["data"]
