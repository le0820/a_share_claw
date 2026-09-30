"""Price analysis and outlook core with fixed synthetic sessions, never live markets."""
import argparse
import copy
import dataclasses
import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from a_share_claw.harness.cli import run_harness
from a_share_claw.harness.contracts import RunRequest, RunStatus, Scope, canonical, digest
from a_share_claw.harness.outlook import derived_requirements
from a_share_claw.harness.trace import TraceRepository
from test_harness import ROOT, environment
from test_harness_hosts import host
from test_sdk_research import OfflineEndpoint, configured

AS_OF = "2026-07-14"
START, END, ANCHOR = "2026-07-10", "2026-07-13", "2026-07-09"


def quant_spec():
    assets = []
    for symbol, name, zone, currency, close in (("COMP", "Synthetic NASDAQ Composite", "America/New_York", "USD", "T16:00:00-04:00"),
                   ("399006", "Synthetic ChiNext", "Asia/Shanghai", "CNY", "T15:00:00+08:00"),
                   ("000688", "Synthetic STAR50", "Asia/Shanghai", "CNY", "T15:00:00+08:00")):
        assets.append({"symbol": symbol, "name": name, "unit": "index_points", "currency": currency, "adjustment": "NONE",
                       "market_timezone": zone, "calendar_source": "fixture:synthetic_calendar",
                       "anchor": {"trade_date": ANCHOR, "close_at": ANCHOR+close},
                       "sessions": [{"trade_date": day, "close_at": day+close} for day in (START, END)]})
    return {"schema_version":"quant-spec-v1", "operation":"price_statistics", "frequency":"daily", "window_start":START, "window_end":END,
            "cutoff_timestamp":END+"T21:00:00Z", "assets":assets, "benchmark":"COMP",
            "metrics":["period_return","max_drawdown","annualized_volatility","excess_return"], "annualization_factor":252}


def price_data():
    spec = quant_spec()
    values = {"COMP":[100,105,110], "399006":[100,110,88], "000688":[100,100,100]}
    series = []
    for a in spec["assets"]:
        series.append({**{k:a[k] for k in ("symbol","name","unit","currency","adjustment","market_timezone")},
                      "frequency":"daily", "source":"synthetic daily index fixture", "source_file":"fixture:"+a["symbol"],
                      "source_timestamp":spec["cutoff_timestamp"], "publication_date":END,
                      "rows":[{"trade_date":s["trade_date"],"available_at":s["close_at"],"close":v}
                              for s,v in zip([a["anchor"],*a["sessions"]],values[a["symbol"]])]})
    return {"schema_version":"price-series-v1", "series":series}


def envelope(scope, capability, data):
    return {"capability":capability,"scope_key":scope.key,"data":data,"fallback_status":"none",
            "provenance":{"source":"synthetic reviewed acceptance", "source_file":"fixture:"+capability,
                          "source_timestamp":END+"T21:00:00Z", "publication_date":END,"observation_date":END,
                          "data_period":START+"/"+END,"sha256":digest(data)}}


def quant_packet(scope):
    return {"as_of_date":AS_OF,"facts":[envelope(scope,"price_history",price_data())]}


def macro_facts():
    return {"schema_version":"macro-release-facts-v1", "facts":[
      {"fact_id":key,"entity":entity,"metric":key,"value":value,"unit":"percent","data_period":"synthetic prior month",
       "source":"synthetic macro release","source_file":"fixture:"+key,"publication_date":END,"observation_date":ANCHOR,
       "available_at":END+"T08:30:00-04:00","fallback_status":"none"}
      for key,entity,value in (("inflation","US",2.5),("activity","CN",5))]}


def outlook_spec():
    q = quant_spec()
    raw = macro_facts()["facts"]
    requirements = [{"fact_id":f["fact_id"],"entity":f["entity"],"metric":f["metric"],"unit":f["unit"],"data_period":f["data_period"],
                     "value_type":"number","observation_start":ANCHOR,"observation_end":ANCHOR} for f in raw] + derived_requirements(q)
    derived = [f["fact_id"] for f in derived_requirements(q)]
    r={"subject":"Synthetic macro outlook", "technical_required":False,"debate_required":False,"debate_reason":"",
       "required_facts":requirements,"questions":[
        {"question_id":"base_scenario","question":"Describe a conditional baseline.","role":"hong_guan","required_fact_ids":["inflation","activity"]},
        {"question_id":"market_comparison","question":"Compare the admitted indices.","role":"hong_guan","required_fact_ids":derived},
        {"question_id":"risk_monitoring","question":"State falsification and monitoring conditions.","role":"ping_heng","required_fact_ids":["inflation","activity"]}]}
    return {"quant_spec":q,"research_spec":r,"forecast_start":"2026-07-14","forecast_end":"2026-09-30"}


def outlook_packet(scope):
    p=quant_packet(scope)
    p["facts"].append(envelope(scope,"macro_release_facts",macro_facts()))
    return p


def outlook_reply(payload):
    e=payload.json()
    return {"role":e["role"],"phase":e["phase"],"packet_id":e["packet_id"],"packet_version":e["packet_version"],
            "answers":[{"question_id":q["question_id"],"fact_ids":q["required_fact_ids"],
                        "inference":"Conditional synthetic baseline; observed returns do not establish future returns."}
                       for q in e["plan"]["parameters"]["research_spec"]["questions"] if q["role"]==e["role"]],
            "responds_to":[],"unknowns":[],"monitoring_triggers":[{"condition":"Reassess on changes to admitted macro evidence.","fact_ids":["inflation","activity"]}] if e["role"]=="ping_heng" else []}


def outlook_review(payload):
    e=payload.json()
    return {"candidate_hash":e["candidate_hash"],"passed":True,"findings":[],"reviewer":"scripted_outlook_fixture_review","version":"fixture-v1"}


def test_quant_statistics_use_frozen_identity_window_and_anchor(environment):
    storage,scope,engine,_=environment
    out=engine.run(RunRequest(scope,"Synthetic independent index analysis",AS_OF,"research","quant"),quant_packet(scope),quant_spec=quant_spec())
    assert out.status==RunStatus.SUCCEEDED,out.output
    data=json.loads(out.output)["data"]
    cn=data["metrics"]["399006"]
    assert cn["period_return"]==pytest.approx(-.12) and cn["max_drawdown"]==pytest.approx(.2)
    assert cn["annualized_volatility"]==pytest.approx(math.sqrt(.045)*math.sqrt(252))
    assert cn["excess_return"]==pytest.approx(-22)
    assert set(data["metrics"])=={"COMP","399006","000688"}
    assert out.action=="NO_ACTION" and TraceRepository(storage).read_state(scope,"macro") is None
    assert all(s["coverage_status"]=="complete_against_declared_calendar" for s in data["series_audit"].values())


@pytest.mark.parametrize("mutation,code",[("proxy","price_contract_mismatch"),("missing","insufficient_coverage:price_sessions"),
      ("duplicate","price_contract_mismatch"),("placeholder","invalid_market_history"),("before_close","price_before_close"),
      ("future","future_data"),("future_publication","price_contract_mismatch"),("unit","price_contract_mismatch")])
def test_price_contract_blocks_partial_or_wrong_series(environment,mutation,code):
    storage,scope,engine,_=environment
    p=quant_packet(scope)
    series=p["facts"][0]["data"]["series"][0]
    if mutation=="proxy":series["symbol"]="QQQ.US"
    if mutation=="missing":series["rows"].pop()
    if mutation=="duplicate":series["rows"][-1]["trade_date"]=START
    if mutation=="placeholder":series["rows"][-1]["close"]=None
    if mutation=="before_close":series["rows"][-1]["available_at"]=END+"T12:00:00Z"
    if mutation=="future":series["rows"][-1]["available_at"]=END+"T23:00:00Z"
    if mutation=="future_publication":series["publication_date"]=AS_OF
    if mutation=="unit":series["unit"]="percent"
    p["facts"][0]["provenance"]["sha256"]=digest(p["facts"][0]["data"])
    out=engine.run(RunRequest(scope,"Synthetic invalid window",AS_OF,"official","quant"),p,quant_spec=quant_spec())
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]==code
    assert TraceRepository(storage).read_state(scope,"quant") is None


def test_undefined_correlation_is_disclosed_not_neutral(environment):
    _,scope,engine,_=environment
    s=quant_spec();s["metrics"].append("correlation")
    out=engine.run(RunRequest(scope,"Synthetic correlation",AS_OF,"research","quant"),quant_packet(scope),quant_spec=s)
    assert out.status==RunStatus.SUCCEEDED
    d=json.loads(out.output)["data"]
    assert d["metrics"]["000688"]["correlation"] is None
    assert {"symbol":"000688","metric":"correlation","reason":"zero_variance"} in d["unknowns"]


def test_future_cutoff_and_unimplemented_backtest_stay_blocked(environment):
    _,scope,engine,_=environment
    from datetime import datetime,timezone
    req=RunRequest(scope,"Synthetic analysis",AS_OF,"research","quant")
    out=engine.run(req,quant_packet(scope),quant_spec=quant_spec(),clock=datetime(2026,7,13,20,30,tzinfo=timezone.utc))
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="future_data"
    s=quant_spec();s["operation"]="strategy_backtest"
    out=engine.run(req,quant_packet(scope),quant_spec=s)
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="quant_operation_not_implemented"


def test_outlook_roles_share_release_and_core_derived_facts_without_daily_scores(environment):
    storage,scope,engine,_=environment
    seen=[]
    def runner(payload):
        e=payload.json();seen.append(e)
        return outlook_reply(payload)
    out=engine.run(RunRequest(scope,"Synthetic conditional outlook",AS_OF,"research","outlook"),outlook_packet(scope),
                   outlook_spec=outlook_spec(),role_runner=runner,semantic_reviewer=outlook_review)
    assert out.status==RunStatus.SUCCEEDED,out.output
    result=json.loads(out.output)
    assert [e["role"] for e in seen]==["hong_guan","ping_heng"] and len({e["packet_id"] for e in seen})==1
    assert any(f["fact_id"].startswith("price.COMP.") and f["source"]=="core_quant_v1" for f in result["data"]["confirmed_facts"])
    assert "base_scenario" in result["data"] and result["data"]["forecast_horizon"]["start"]==AS_OF
    assert not any(k in result["data"] for k in ("L1","L3","composite","position_band"))
    report=json.loads(Path(result["report"]["path"]).read_text())
    assert report["base_scenario"] and report["market_statistics"]["metrics"] and report["risk_decision"]=="NO_ACTION"
    assert not TraceRepository(storage).read(out.run_id,scope)["model_calls"]


def test_outlook_intraday_future_release_and_missing_semantic_review_block_delivery(environment):
    _,scope,engine,_=environment
    req=RunRequest(scope,"Synthetic outlook gate",AS_OF,"research","outlook")
    p=outlook_packet(scope)
    p["facts"][1]["data"]["facts"][0]["available_at"]=END+"T22:00:00Z"
    p["facts"][1]["provenance"]["sha256"]=digest(p["facts"][1]["data"])
    called=[]
    out=engine.run(req,p,outlook_spec=outlook_spec(),role_runner=lambda x:called.append(x),semantic_reviewer=outlook_review)
    assert not called and out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="future_data"
    out=engine.run(req,outlook_packet(scope),outlook_spec=outlook_spec(),role_runner=outlook_reply)
    assert out.status==RunStatus.BLOCKED and json.loads(out.output)["error_code"]=="semantic_review_required"


def test_quant_cli_plan_and_offline_replay_preserve_spec(host,capsys):
    config,storage,context,path=host
    scope=Scope.from_context(ROOT,context)
    facts,specfile=path/"facts.json",path/"quant.json"
    facts.write_text(canonical(quant_packet(scope)));specfile.write_text(canonical(quant_spec()))
    common={"platform":"local","user":"local-user","chat":"local-chat","agent_key":"default"}
    args=argparse.Namespace(command="harness",harness_command="run",packet_file=facts,quant_spec=specfile,workflow="quant",date=AS_OF,mode="research",**common)
    assert run_harness(args,config,storage)==0
    original=json.loads(capsys.readouterr().out)
    args=argparse.Namespace(command="harness",harness_command="replay",run_id=original["run_id"],**common)
    assert run_harness(args,config,storage)==0
    replay=json.loads(capsys.readouterr().out)
    assert replay["data"]["specification"]==quant_spec() and replay["data"]["metrics"]==original["data"]["metrics"]
    assert replay["action"]=="NO_ACTION"


def test_outlook_explicit_sdk_path_uses_macro_roles_and_independent_review(host):
    from a_share_claw.agent import InvestmentAgent
    config,storage,context,_=host
    scope=Scope.from_context(ROOT,context)
    endpoint=OfflineEndpoint()
    with patch("a_share_claw.agent.build_model_client",side_effect=endpoint.client), \
         patch("test_sdk_research.role_reply",side_effect=outlook_reply), patch("test_sdk_research.fixture_review",side_effect=outlook_review):
        out=InvestmentAgent(configured(config),storage).run_core_result(context,"Synthetic outlook",as_of_date=AS_OF,
                packet=outlook_packet(scope),outlook_spec=outlook_spec(),workflow="outlook")
    assert out.status==RunStatus.SUCCEEDED,out.output
    trace=TraceRepository(storage).read(out.run_id,scope)
    assert [c["detail"]["operation"] for c in trace["model_calls"]]==["hong_guan:initial","ping_heng:final","research_semantic_review"]
    assert not trace["tool_calls"] and not out.official_output_allowed


def test_market_outlook_routes_separately_from_daily_score_and_company_codes():
    from a_share_claw.research_context import classify_research_route,ResearchWorkflow
    assert classify_research_route("分析 NASDAQ Composite、399006、000688 三季度数据，输出四季度市场展望和基准情景").workflow == ResearchWorkflow.OUTLOOK
    assert classify_research_route("每日评分和四季度市场展望").workflow == ResearchWorkflow.MIXED
    assert classify_research_route("公司财报和基准情景").workflow == ResearchWorkflow.COMPANY
    assert classify_research_route("每日 composite 综合评分").workflow == ResearchWorkflow.MACRO
