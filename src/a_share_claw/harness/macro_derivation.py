"""Core-owned fixed monthly PCE calculations from exact native FRED levels."""
from __future__ import annotations

import calendar
import json
import math
from datetime import date

from .contracts import canonical, digest, validate_date
from .quant import timestamp

VERSION="core-macro-v1"
SERIES={
    "PCEPI":{"metric":"pce_price_index","title":"Personal Consumption Expenditures: Chain-type Price Index"},
    "PCEPILFE":{"metric":"core_pce_price_index","title":"Personal Consumption Expenditures Excluding Food and Energy (Chain-Type Price Index)"},
}
NATIVE_UNIT="Index 2017=100"
DERIVED={"pce_mom":("PCEPI",1),"pce_yoy":("PCEPI",12),"core_pce_mom":("PCEPILFE",1),"core_pce_yoy":("PCEPILFE",12)}


def month(day,offset=0):
    d=date.fromisoformat(validate_date(day))
    n=d.year*12+d.month-1+offset;y,m=divmod(n,12);m+=1
    if not 1<=y<=9999:raise ValueError("invalid_outlook_spec")
    return f"{y:04d}-{m:02d}-01"


def period(day):
    d=date.fromisoformat(validate_date(day))
    if d.day!=1:raise ValueError("invalid_outlook_spec")
    return day+"/"+f"{d.year:04d}-{d.month:02d}-{calendar.monthrange(d.year,d.month)[1]:02d}"


def native_requirement(series,day):
    if series not in SERIES:raise ValueError("invalid_outlook_spec")
    return {"fact_id":f"fred.{series}.{day}","entity":"US","metric":SERIES[series]["metric"],
            "unit":NATIVE_UNIT,"value_type":"number","data_period":period(day),
            "observation_start":day,"observation_end":day}


def contracts(spec):
    """No values at planning: require exact current/comparison contracts up front."""
    facts={f["fact_id"]:f for f in spec["required_facts"]};result=[]
    for target in facts.values():
        if target["metric"] not in DERIVED:continue
        series,lag=DERIVED[target["metric"]];day=target["data_period"].split("/")[0]
        if (target["entity"]!="US" or target["unit"]!="percent" or target["value_type"]!="number" or
                target["data_period"]!=period(day) or target["observation_start"]!=day or target["observation_end"]!=day):
            raise ValueError("invalid_outlook_spec")
        inputs=[native_requirement(series,d) for d in (day,month(day,-lag))]
        if any(facts.get(i["fact_id"])!=i for i in inputs):raise ValueError("invalid_outlook_spec")
        result.append({"target":target,"series_id":series,"input_fact_ids":[i["fact_id"] for i in inputs]})
    return result


def calculate(facts,spec):
    result=[]
    for contract in contracts(spec):
        target=contract["target"];ids=contract["input_fact_ids"]
        if target["fact_id"] in facts:raise ValueError("research_fact_contract_mismatch")
        if any(i not in facts for i in ids):raise ValueError("insufficient_coverage:monthly_comparison")
        current,previous=(facts[i] for i in ids);metas=[]
        for fact in (current,previous):
            meta=fact.get("availability",{});native=meta.get("fred_series",{})
            if (fact["source"]!="fred" or native.get("series_id")!=contract["series_id"] or
                    native.get("title")!=SERIES[contract["series_id"]]["title"] or fact["unit"]!=NATIVE_UNIT or
                    native.get("frequency")!="Monthly" or native.get("seasonal_adjustment")!="Seasonally Adjusted"):
                raise ValueError("unit_or_metric_mismatch")
            if type(fact["value"]) not in (int,float) or not math.isfinite(fact["value"]) or fact["value"]<=0:
                raise ValueError("insufficient_coverage:monthly_index")
            metas.append(meta)
        if (metas[0]["fred_series"]!=metas[1]["fred_series"] or metas[0]["source_run_id"]!=metas[1]["source_run_id"]):
            raise ValueError("research_fact_contract_mismatch")
        value=(current["value"]/previous["value"]-1)*100
        if not math.isfinite(value):raise ValueError("insufficient_coverage:monthly_index")
        derivation={"version":VERSION,"formula":"(current / comparison - 1) * 100",
                    "input_fact_ids":ids,"input_hashes":[digest(current),digest(previous)]}
        meta=json.loads(canonical(metas[0]));meta["selection_hash"]=digest(derivation)
        result.append({"fact_id":target["fact_id"],"entity":"US","metric":target["metric"],"unit":"percent","value":value,
            "data_period":target["data_period"],"observation_date":target["observation_end"],"publication_date":None,
            "source":"core_macro_v1","fallback_status":"none",
            "available_at":max((current["available_at"],previous["available_at"]),key=timestamp),
            "availability":meta,"derivation":derivation})
    return result


def verify_calculations(facts):
    """Recheck reserved derived records and dependency hashes at role admission."""
    derived=[f for f in facts.values() if f["source"]=="core_macro_v1"]
    if not derived:return
    required=[]
    for fact in facts.values():
        required.append({"fact_id":fact["fact_id"],"entity":fact["entity"],"metric":fact["metric"],"unit":fact["unit"],
            "value_type":"number" if type(fact["value"]) in (int,float) else "text","data_period":fact["data_period"],
            "observation_start":fact["observation_date"],"observation_end":fact["observation_date"]})
    raw={key:fact for key,fact in facts.items() if fact["source"]!="core_macro_v1"}
    recomputed=calculate(raw,{"required_facts":required})
    expected={f["fact_id"]:f for f in recomputed}
    if any({key:value for key,value in f.items() if key!="source_file"}!=expected.get(f["fact_id"]) for f in derived):
        raise ValueError("research_fact_contract_mismatch")
