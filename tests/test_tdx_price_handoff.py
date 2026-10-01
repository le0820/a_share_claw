"""Implemented source/core index checks. Synthetic SDK JSON and calendars only."""
import asyncio
import copy
import json
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from a_share_claw.data_plugins import DataRun,default_registry
from a_share_claw.data_plugins.core import DataError,Requirement
from a_share_claw.data_plugins.tdx import EasyTDX,INDEXES,WorkerTransport
from a_share_claw.harness.contracts import RunRequest,RunStatus,digest
from a_share_claw.harness.outlook import derived_requirements
from a_share_claw.harness.trace import TraceRepository
from test_data_plugins import requirement
from test_harness import environment
from test_source_core_handoff import clock,Clock,DAY,CAPTURE,source_plan,bindings,planned,reply,fetch_all,specification
from test_harness_quant import quant_spec,outlook_review


@pytest.fixture(autouse=True)
def index_clock(clock,monkeypatch):
    monkeypatch.setattr("a_share_claw.data_plugins.tdx.datetime",Clock)


class IndexTransport:
    def __init__(self):self.calls=[];self.omit=None;self.bad_name=None;self.duplicate=False;self.version="1.20.4"
    def request(self,p):
        self.calls.append(p)
        if p["operation"]=="catalog":return json.dumps({"sdk_version":self.version,"endpoint":"tcp://116.205.135.205:7727",
            "identity":[{"market":12,"code":"SYNCOMP","name":"NASDAQ Composite"}],"bars":[]}).encode()
        international=p["kind"]=="international";market=p["market"];code=p["code"]
        name="NASDAQ Composite" if international else "科创50" if code=="000688" else "创业板指"
        rows=[{"datetime":day+"T00:00:00.000","open":close,"high":close+1,"low":close-1,"close":close}
            for day,close in [("2026-07-09",100),("2026-07-10",110),("2026-07-13",105)] if day!=self.omit]
        if self.duplicate:rows.append(rows[-1])
        return json.dumps({"sdk_version":self.version,"endpoint":"tcp://116.205.135.205:7727" if international else "tcp://121.36.248.138:7709",
            "identity":[{"market":market,"code":code,"name":self.bad_name or name}],"bars":rows}).encode()


def index_spec():
    spec=quant_spec();spec["cutoff_timestamp"]=DAY+"T01:00:00Z";spec["assets"]=[]
    for symbol,native in INDEXES.items():
        suffix="+08:00" if native["kind"]=="china" else "-04:00";hour="15" if native["kind"]=="china" else "16"
        spec["assets"].append({"symbol":symbol,**{key:native[key] for key in ("name","unit","currency","market_timezone")},
            "adjustment":"none","calendar_source":"synthetic explicitly declared sessions",
            "anchor":{"trade_date":"2026-07-09","close_at":f"2026-07-09T{hour}:00:00{suffix}"},
            "sessions":[{"trade_date":day,"close_at":f"{day}T{hour}:00:00{suffix}"} for day in ("2026-07-10","2026-07-13")]})
    spec["benchmark"]="COMP.NASDAQ"
    return spec


def price_plan():
    return {"framework":"Synthetic exact index contracts before acquisition","requirements":[
        requirement("easytdx","market.index_daily_snapshot",{"symbol":symbol,"provider_code":native["code"] or "SYNCOMP",
            "start_date":"2026-07-09","end_date":"2026-07-13","count":10},day=DAY,rid="price"+str(i)).json()
        for i,(symbol,native) in enumerate(INDEXES.items())]}


def price_bindings():return [{"symbol":r["params"]["symbol"],"requirement_id":r["requirement_id"]} for r in price_plan()["requirements"]]


def prepared(tmp,scope,*,plan=None,spec=None,transport=None):
    transport=transport or IndexTransport();run=DataRun({"easytdx":EasyTDX({},transport)},tmp,scope)
    run.plan_core_quant(plan or price_plan(),spec or index_spec(),price_bindings());return run,transport


def test_latest_market_policy_selects_easytdx_and_keeps_tickflow_auxiliary():
    registry=default_registry();active=registry.snapshot({})
    assert set(active)=={"nbs","pbc","easytdx","bea","sec"} and "tickflow" not in active
    assert set(registry.snapshot({"ASCLAW_DATA_PROVIDERS":"tickflow"}))=={"tickflow"}
    assert registry.snapshot({"ASCLAW_DATA_PROVIDERS":""})=={}


def test_three_native_indices_to_core_statistics_and_dual_report(environment,clock):
    storage,scope,engine,tmp=environment;run,t=prepared(tmp/"source",scope);fetch_all(run);env=run.core_price_evidence()
    assert run.core_price_evidence()==env and len(t.calls)==3
    assert env["data"]["schema_version"]=="price-series-v2" and not run.summary()["official_output_allowed"]
    assert {s["symbol"] for s in env["data"]["series"]}==set(INDEXES)
    assert all(row["available_at"]==CAPTURE.isoformat() for s in env["data"]["series"] for row in s["rows"])
    out=engine.run(RunRequest(scope,"Synthetic exact-index implementation check",DAY,"research","quant"),{"as_of_date":DAY,"facts":[env]},
        quant_spec=index_spec(),clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    result=json.loads(out.output)
    assert all(value["period_return"]==pytest.approx(.05) for value in result["data"]["metrics"].values())
    assert all(value["max_drawdown"]==pytest.approx(1-105/110) for value in result["data"]["metrics"].values())
    assert "价格版本口径" in Path(result["report_markdown"]["path"]).read_text()
    assert out.action=="NO_ACTION" and not out.official_output_allowed and TraceRepository(storage).read_state(scope,"macro") is None


def test_same_source_run_freezes_macro_and_index_bindings_for_core_outlook(environment,clock):
    _,scope,engine,tmp=environment;base,_=planned(tmp/"base",scope);providers=dict(base.providers);providers["easytdx"]=EasyTDX({},IndexTransport())
    spec=specification();spec["quant_spec"]=index_spec()
    spec["research_spec"]["required_facts"]=[f for f in spec["research_spec"]["required_facts"] if not f["fact_id"].startswith("price.")]+derived_requirements(index_spec())
    next(q for q in spec["research_spec"]["questions"] if q["question_id"]=="market_comparison")["required_fact_ids"]=[f["fact_id"] for f in derived_requirements(index_spec())]
    plan=source_plan();plan["requirements"]+=price_plan()["requirements"]
    run=DataRun(providers,tmp/"sources",scope);run.plan_core_outlook(plan,spec,bindings(),price_bindings=price_bindings());fetch_all(run)
    packet={"as_of_date":DAY,"facts":[run.core_macro_evidence(),run.core_price_evidence()]}
    out=engine.run(RunRequest(scope,"Synthetic same-run source outlook",DAY,"research","outlook"),packet,outlook_spec=spec,
        role_runner=reply,semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status==RunStatus.SUCCEEDED,out.output
    assert not out.official_output_allowed and out.action=="NO_ACTION"


@pytest.mark.parametrize("field,value",[("name","QQQ"),("unit","CNY"),("adjustment","forward"),("market_timezone","UTC")])
def test_core_index_identity_is_frozen_before_network(environment,clock,field,value):
    _,scope,_,tmp=environment;spec=index_spec();spec["assets"][0][field]=value
    with pytest.raises((DataError,ValueError)):prepared(tmp,scope,spec=spec)
    assert not list(tmp.rglob("*.raw"))


def test_tickflow_cannot_be_promoted_into_primary_index_contract(environment,clock):
    _,scope,_,tmp=environment;plan=price_plan();plan["requirements"][0]["provider"]="tickflow"
    with pytest.raises(DataError) as error:prepared(tmp,scope,plan=plan)
    assert error.value.code=="source_denied"


@pytest.mark.parametrize("name",["NASDAQ 100","QQQ","纳斯达克"])
def test_ambiguous_or_proxy_index_identity_is_a_gap(environment,clock,name):
    _,scope,_,tmp=environment;t=IndexTransport();t.bad_name=name;run,_=prepared(tmp,scope,transport=t);fetch_all(run)
    assert all(r["error_code"]=="source_mismatch" for r in run.results.values())
    with pytest.raises(DataError):run.core_price_evidence()


@pytest.mark.parametrize("mutation",["missing_session","duplicate_bar","sdk_version"])
def test_missing_duplicate_and_wrong_sdk_are_never_core_prices(environment,clock,mutation):
    _,scope,_,tmp=environment;t=IndexTransport()
    if mutation=="missing_session":t.omit="2026-07-10"
    elif mutation=="duplicate_bar":t.duplicate=True
    else:t.version="1.20.5"
    run,_=prepared(tmp,scope,transport=t);fetch_all(run)
    with pytest.raises(DataError) as error:run.core_price_evidence()
    assert error.value.code in {"insufficient_coverage","source_disagreement","source_mismatch"}


@pytest.mark.parametrize("target",["raw","result","contract"])
def test_source_archive_hashes_are_rechecked_at_handoff(environment,clock,target):
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run)
    path=run.directory/run.results["price0"]["provenance"]["artifact"] if target=="raw" else run.directory/"results/price0.json" if target=="result" else run.directory/"core-contract.json"
    path.write_text("{}")
    with pytest.raises(DataError) as error:run.core_price_evidence()
    assert error.value.code=="hash_mismatch"


@pytest.mark.parametrize("mutation",["snapshot_day","invent_vintage","backdate_row","change_spec"])
def test_core_rechecks_price_dates_and_frozen_spec(environment,clock,mutation):
    _,scope,engine,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run);env=run.core_price_evidence();s=env["data"]["series"][0];spec=index_spec()
    if mutation=="snapshot_day":s["current_snapshot"]["snapshot_as_of_date"]="2026-07-13"
    elif mutation=="invent_vintage":s["current_snapshot"]["historical_vintage_certified"]=True
    elif mutation=="backdate_row":s["rows"][0]["available_at"]="2026-07-09T07:00:00Z"
    else:spec["annualization_factor"]=250
    env["provenance"]["sha256"]=digest(env["data"])
    out=engine.run(RunRequest(scope,"Synthetic invalid index dates",DAY,"research","quant"),{"as_of_date":DAY,"facts":[env]},quant_spec=spec,
        clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status!=RunStatus.SUCCEEDED and not out.official_output_allowed


def test_worker_uses_isolated_sdk_state_without_inherited_credentials(monkeypatch):
    seen=[]
    def fake(args,**kwargs):
        seen.append((args,kwargs));assert Path(kwargs["env"]["EASY_TDX_CONFIG_DIR"]).is_dir()
        return SimpleNamespace(returncode=0,stdout='{"sdk_version":"1.20.4"}',stderr="")
    monkeypatch.setenv("FRED_API_KEY","synthetic-secret");monkeypatch.setenv("EASY_TDX_HOST","untrusted-host")
    monkeypatch.setattr("a_share_claw.data_plugins.tdx.subprocess.run",fake)
    assert json.loads(WorkerTransport().request({"operation":"catalog","kind":"international"}))["sdk_version"]=="1.20.4"
    args,kw=seen[0];assert "-I" in args and "FRED_API_KEY" not in kw["env"] and "EASY_TDX_HOST" not in kw["env"]
    assert not Path(kw["env"]["EASY_TDX_CONFIG_DIR"]).exists()


def test_catalog_is_discovery_only_not_core_or_official_prices(tmp_path,clock):
    t=IndexTransport();run=DataRun({"easytdx":EasyTDX({},t)},tmp_path,"legacy")
    req=requirement("easytdx","market.index_catalog",{},day=DAY);run.plan({"framework":"Synthetic instrument discovery","requirements":[req.json()]})
    out=asyncio.run(run.fetch("input"));assert out["status"]=="unverified" and not run.summary()["official_output_allowed"]
    assert out["data"]["instruments"][0]["code"]=="SYNCOMP"


def test_historical_capture_request_never_opens_sdk(tmp_path,clock):
    t=IndexTransport();run=DataRun({"easytdx":EasyTDX({},t)},tmp_path,"legacy")
    req=requirement("easytdx","market.index_catalog",{},day="2026-07-13");run.plan({"framework":"Synthetic old capture","requirements":[req.json()]})
    assert asyncio.run(run.fetch("input"))["error_code"]=="historical_unavailable" and not t.calls
