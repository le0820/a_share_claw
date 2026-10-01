"""Implementation acceptance only: fake source JSON/clocks/prices, no real market case."""
import asyncio
import copy
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.providers import FRED
from a_share_claw.harness.contracts import RunRequest, RunStatus, digest
from a_share_claw.harness.macro_derivation import SERIES, native_requirement
from a_share_claw.harness.trace import TraceRepository
from test_data_plugins import requirement
from test_harness import environment
from test_harness_quant import quant_packet, outlook_review
from test_source_core_handoff import clock, DAY, CAPTURE, planned, fetch_all, specification, source_plan, bindings, reply

VINTAGE="2026-07-13"  # capture is July 14 in China, July 13 in Chicago
DAYS=("2026-06-01","2026-05-01","2025-06-01")


class NativeTransport:
    def __init__(self):self.calls=[];self.values={DAYS[0]:120,DAYS[1]:110,DAYS[2]:100};self.title=None
    def get(self,url,params=None,headers=None):
        self.calls.append((url,params,headers))
        series=params["series_id"]
        if url.endswith("/observations"):
            obj={"count":3,"observations":[{"date":day,"value":"." if value is None else str(value),
                "realtime_start":VINTAGE,"realtime_end":VINTAGE} for day,value in self.values.items()]}
        else:
            obj={"seriess":[{"id":series,"title":self.title or SERIES[series]["title"],"units":"Index 2017=100","frequency":"Monthly",
                "seasonal_adjustment":"Seasonally Adjusted","last_updated":"2026-07-13 07:43:00-05",
                "notes":"Synthetic revision note. Not an original observation release clock.","realtime_start":VINTAGE,"realtime_end":VINTAGE}]}
        return json.dumps(obj).encode()


def fred_spec():
    spec=specification();raw=[];derived=[]
    for series,prefix in (("PCEPI","pce"),("PCEPILFE","core_pce")):
        raw.extend(native_requirement(series,day) for day in DAYS)
        derived.extend({"fact_id":prefix+"."+suffix,"entity":"US","metric":prefix+"_"+suffix,"unit":"percent","value_type":"number",
            "data_period":"2026-06-01/2026-06-30","observation_start":DAYS[0],"observation_end":DAYS[0]} for suffix in ("mom","yoy"))
    spec["research_spec"]["required_facts"].extend(raw+derived)
    for q in spec["research_spec"]["questions"]:
        if q["question_id"]!="market_comparison":q["required_fact_ids"].extend(f["fact_id"] for f in derived)
    return spec


def fred_plan():
    plan=source_plan()
    for series in SERIES:
        plan["requirements"].append(requirement("fred","macro.series_snapshot",{"series_id":series,"start_date":DAYS[-1],"end_date":DAYS[0],
            "vintage_date":VINTAGE},day=DAY,rid=series).json())
        plan["requirements"].append(requirement("fred","macro.series_metadata_snapshot",{"series_id":series,"vintage_date":VINTAGE},day=DAY,rid=series+"-metadata").json())
    return plan


def fred_bindings():
    result=bindings()
    for series in SERIES:
        result.extend({"fact_id":native_requirement(series,day)["fact_id"],"requirement_id":series,"metadata_requirement_id":series+"-metadata",
            "selector":{"series_id":series,"observation_date":day,"units":"Index 2017=100","frequency":"Monthly","seasonal_adjustment":"Seasonally Adjusted"}} for day in DAYS)
    return result


def prepared(tmp,scope,*,spec=None,plan=None,bind=None,transport=None):
    base,_=planned(tmp/"base",scope)
    transport=transport or NativeTransport();providers=dict(base.providers);providers["fred"]=FRED({"FRED_API_KEY":"synthetic-key"},transport)
    run=DataRun(providers,tmp/"sources",scope)
    run.plan_core_outlook(plan or fred_plan(),spec or fred_spec(),bind or fred_bindings())
    return run,transport


def core_run(environment,run,spec=None):
    _,scope,engine,_=environment
    packet=quant_packet(scope);packet["facts"].append(run.core_macro_evidence());seen=[]
    def runner(payload):seen.append(payload.json());return reply(payload)
    out=engine.run(RunRequest(scope,"Synthetic monthly PCE implementation check",DAY,"research","outlook"),packet,
        outlook_spec=spec or fred_spec(),role_runner=runner,semantic_reviewer=outlook_review,clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    return out,seen


def test_native_levels_to_core_monthly_changes_and_dual_reports(environment,clock):
    storage,scope,_,tmp=environment;run,transport=prepared(tmp,scope);fetch_all(run)
    envelope=run.core_macro_evidence();native=[f for f in envelope["data"]["facts"] if f["source"]=="fred"]
    assert len(native)==6 and not any(f["unit"]=="percent" for f in native)
    assert all(f["publication_date"] is None and f["availability"]["publication_time_precision"]=="unknown" for f in native)
    assert all(f["available_at"]==CAPTURE.isoformat() and f["availability"]["fred_series"]["vintage_date"]==VINTAGE for f in native)
    assert len(transport.calls)==4 and all(call[1]["realtime_start"]==VINTAGE==call[1]["realtime_end"] for call in transport.calls)
    assert all("units" not in call[1] and "frequency" not in call[1] for call in transport.calls)
    assert "synthetic-key" not in "".join(f.read_text() for f in run.directory.rglob("*.json"))
    out,seen=core_run(environment,run);assert out.status==RunStatus.SUCCEEDED,out.output
    result=json.loads(out.output);facts={f["fact_id"]:f for f in result["data"]["confirmed_facts"]}
    for prefix in ("pce","core_pce"):
        assert facts[prefix+".mom"]["value"]==pytest.approx((120/110-1)*100)
        assert facts[prefix+".yoy"]["value"]==pytest.approx(20)
        for suffix in ("mom","yoy"):
            fact=facts[prefix+"."+suffix];d=fact["derivation"]
            assert d["input_hashes"]==[digest(facts[key]) for key in d["input_fact_ids"]]
            assert fact["publication_date"] is None and fact["source"]=="core_macro_v1"
            audit=json.loads(Path(fact["source_file"]).read_text())
            assert audit["schema_version"]=="core-macro-v1" and audit["input_facts"]
            assert any(item["fact_id"]==fact["fact_id"] and item["value"]==fact["value"] for item in audit["calculations"])
    assert seen and "derivation" in seen[0]["packet"]["facts"]["pce.mom"]
    md=Path(result["report_markdown"]["path"]).read_text()
    assert "原始发布日期未知" in md and "核心月度指数变化计算" in md
    assert not out.official_output_allowed and out.action=="NO_ACTION"
    assert TraceRepository(storage).read_state(scope,"macro") is None


@pytest.mark.parametrize("field,value",[("unit","percent"),("metric","pce_yoy"),("entity","CN"),("observation_start","2026-06-02")])
def test_native_contract_is_frozen_before_acquisition(environment,clock,field,value):
    _,scope,_,tmp=environment;spec=fred_spec();next(f for f in spec["research_spec"]["required_facts"] if f["fact_id"]=="fred.PCEPI.2026-06-01")[field]=value
    with pytest.raises(ValueError):prepared(tmp,scope,spec=spec)
    assert not list((tmp/"sources").rglob("*.raw"))


def test_missing_last_year_comparison_is_a_planning_gap(environment,clock):
    _,scope,_,tmp=environment;spec=fred_spec()
    spec["research_spec"]["required_facts"]=[f for f in spec["research_spec"]["required_facts"] if f["fact_id"]!="fred.PCEPI.2025-06-01"]
    with pytest.raises(ValueError,match="invalid_outlook_spec"):prepared(tmp,scope,spec=spec)


@pytest.mark.parametrize("mutation",["no_metadata","metadata_series","metadata_vintage","bind_core_metric"])
def test_frozen_handoff_cannot_substitute_metadata_or_fill_core_output(environment,clock,mutation):
    _,scope,_,tmp=environment;plan=fred_plan();bind=fred_bindings()
    if mutation=="no_metadata":del bind[2]["metadata_requirement_id"]
    elif mutation=="metadata_series":plan["requirements"][3]["params"]["series_id"]="PCEPILFE"
    elif mutation=="metadata_vintage":plan["requirements"][3]["params"]["vintage_date"]="2026-07-12"
    else:bind[2]["fact_id"]="pce.mom"
    with pytest.raises(DataError):prepared(tmp,scope,plan=plan,bind=bind)
    assert not list((tmp/"sources").rglob("*.raw"))


@pytest.mark.parametrize("series_date,core_day",[("2026-07-12",DAY),(VINTAGE,"2026-07-13")])
def test_current_snapshot_refuses_old_vintage_or_backdated_capture(tmp_path,clock,series_date,core_day):
    transport=NativeTransport();run=DataRun({"fred":FRED({"FRED_API_KEY":"test"},transport)},tmp_path,"legacy")
    req=requirement("fred","macro.series_snapshot",{"series_id":"PCEPI","start_date":DAYS[-1],"vintage_date":series_date},day=core_day)
    run.plan({"framework":"Synthetic snapshot policy","requirements":[req.json()]})
    result=asyncio.run(run.fetch("input"));assert result["error_code"]=="historical_unavailable" and not transport.calls


@pytest.mark.parametrize("value",[None,0,-1])
def test_missing_or_nonpositive_comparison_never_reaches_roles(environment,clock,value):
    _,scope,_,tmp=environment;transport=NativeTransport();transport.values[DAYS[-1]]=value
    run,_=prepared(tmp,scope,transport=transport);fetch_all(run)
    if value is None:
        with pytest.raises(DataError) as error:run.core_macro_evidence()
        assert error.value.code=="missing_observation"
    else:
        out,seen=core_run(environment,run);assert not seen and out.status!=RunStatus.SUCCEEDED
        assert json.loads(out.output)["error_code"]=="insufficient_coverage:monthly_index"
        assert not out.official_output_allowed


def test_wrong_native_title_is_not_a_pce_substitute(environment,clock):
    _,scope,_,tmp=environment;transport=NativeTransport();transport.title="Synthetic nominal consumption"
    run,_=prepared(tmp,scope,transport=transport);fetch_all(run)
    with pytest.raises(DataError) as error:run.core_macro_evidence()
    assert error.value.code=="source_mismatch"


def test_metadata_archive_is_part_of_the_source_selection_hash(environment,clock):
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run)
    (run.directory/"results/PCEPI-metadata.json").write_text("{}")
    with pytest.raises(DataError) as error:run.core_macro_evidence()
    assert error.value.code=="hash_mismatch"


@pytest.mark.parametrize("mutation",["release_date","capture_vintage","frequency","different_run","core_from_source"])
def test_core_independently_rejects_mutated_snapshot_or_precomputed_source_metrics(environment,clock,mutation):
    _,scope,engine,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run);env=run.core_macro_evidence()
    raw=next(f for f in env["data"]["facts"] if f["fact_id"]=="fred.PCEPI.2026-05-01")
    if mutation=="release_date":raw["publication_date"]="2026-07-13"
    elif mutation=="capture_vintage":raw["availability"]["fred_series"]["vintage_date"]="2026-07-12"
    elif mutation=="frequency":raw["availability"]["fred_series"]["frequency"]="Quarterly"
    elif mutation=="different_run":raw["availability"]["source_run_id"]="0"*32
    else:raw["source"]="core_macro_v1"
    env["provenance"]["sha256"]=digest(env["data"]);packet=quant_packet(scope);packet["facts"].append(env)
    out=engine.run(RunRequest(scope,"Synthetic invalid monthly source",DAY,"research","outlook"),packet,
        outlook_spec=fred_spec(),role_runner=lambda _:pytest.fail("Invalid monthly source reached role"),semantic_reviewer=outlook_review,
        clock=datetime(2026,7,14,2,tzinfo=timezone.utc))
    assert out.status!=RunStatus.SUCCEEDED and not out.official_output_allowed
    assert "report" not in json.loads(out.output)


@pytest.mark.parametrize("mutation",["computed_value","dependency_hash","dependency_value"])
def test_role_catalog_recomputes_core_monthly_values_from_pinned_inputs(environment,clock,mutation):
    from a_share_claw.harness.research import catalog
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope);fetch_all(run);out,_=core_run(environment,run)
    facts=json.loads(out.output)["data"]["confirmed_facts"]
    derived=next(f for f in facts if f["fact_id"]=="pce.mom")
    if mutation=="computed_value":derived["value"]+=1
    elif mutation=="dependency_hash":
        derived["derivation"]["input_hashes"][0]="0"*64
        derived["availability"]["selection_hash"]=digest(derived["derivation"])
    else:next(f for f in facts if f["fact_id"]=="fred.PCEPI.2026-06-01")["value"]+=1
    with pytest.raises(ValueError,match="research_fact_contract_mismatch"):
        catalog({"schema_version":"research-facts-v2","facts":facts},DAY)


def test_current_availability_waits_for_both_observation_and_metadata_capture(environment,clock,monkeypatch):
    import a_share_claw.data_plugins.core as core
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope)
    asyncio.run(run.fetch("PCEPI"))
    later=CAPTURE+timedelta(minutes=30)
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):return later.astimezone(tz) if tz else later.replace(tzinfo=None)
    monkeypatch.setattr(core,"datetime",Later);asyncio.run(run.fetch("PCEPI-metadata"))
    obs=run.select("PCEPI",fred_bindings()[2]["selector"],metadata_requirement_id="PCEPI-metadata")["observation"]
    assert obs["available_at"]==later.isoformat()
    assert obs["available_at"]!=run.results["PCEPI"]["provenance"]["retrieved_at"]


def test_source_midnight_between_capture_and_metadata_is_a_gap(environment,clock,monkeypatch):
    import a_share_claw.data_plugins.core as core
    _,scope,_,tmp=environment;run,_=prepared(tmp,scope)
    asyncio.run(run.fetch("PCEPI"));later=CAPTURE+timedelta(hours=6)
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):return later.astimezone(tz) if tz else later.replace(tzinfo=None)
    monkeypatch.setattr(core,"datetime",Later);asyncio.run(run.fetch("PCEPI-metadata"))
    with pytest.raises(DataError) as error:
        run.select("PCEPI",fred_bindings()[2]["selector"],metadata_requirement_id="PCEPI-metadata")
    assert error.value.code=="future_data"
