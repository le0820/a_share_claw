"""Trusted source-to-core handoff; immutable contracts precede current-page acquisition."""
from __future__ import annotations

import calendar
import json

from ..harness.contracts import canonical, digest
from ..harness.outlook import checked_outlook_spec, derived_requirements
from ..harness.quant import timestamp
from ..harness.macro_derivation import contracts as macro_contracts, native_requirement, SERIES, NATIVE_UNIT
from .core import DataError, Requirement
from .macro_mapping import NBS_PATTERNS, PBC_PATTERNS


def freeze_outlook_contract(run, plan, outlook_spec, bindings):
    if run.core_scope_key is None:
        raise DataError("scope_mismatch","Core handoff requires a trusted four-field Scope at DataRun construction")
    spec=checked_outlook_spec(outlook_spec)
    if not isinstance(plan,dict) or set(plan)!={"framework","requirements"} or not isinstance(plan["requirements"],list):
        raise DataError("invalid_request","Use the explicit source plan before acquisition")
    requirements={r.requirement_id:r for r in (Requirement.parse(obj) for obj in plan["requirements"])}
    computed={f["fact_id"] for f in derived_requirements(spec["quant_spec"])} | {c["target"]["fact_id"] for c in macro_contracts(spec["research_spec"])}
    expected={f["fact_id"]:f for f in spec["research_spec"]["required_facts"] if f["fact_id"] not in computed}
    if not isinstance(bindings,list) or not 1<=len(bindings)<=100:
        raise DataError("invalid_request","Provide exact source bindings for every non-price outlook fact")
    seen=set();dates=set()
    for b in bindings:
        if (not isinstance(b,dict) or set(b) not in ({"fact_id","requirement_id","selector"},{"fact_id","requirement_id","selector","metadata_requirement_id"}) or
                not isinstance(b["fact_id"],str) or b["fact_id"] not in expected or b["fact_id"] in seen or
                not isinstance(b["requirement_id"],str) or b["requirement_id"] not in requirements or not isinstance(b["selector"],dict)):
            raise DataError("invalid_request","Each fixed core fact must bind exactly one planned source selection")
        seen.add(b["fact_id"]);r=requirements[b["requirement_id"]];target=expected[b["fact_id"]];sel=b["selector"]
        if r.provider=="sec":
            from .sec_handoff import checked_binding
            dates.add(checked_binding(requirements,b,target))
            continue
        if r.provider=="bea" and r.capability in {"macro.pce_release_snapshot","macro.pce_history_snapshot"}:
            from .bea import native_requirement as bea_requirement
            if (set(b)!={"fact_id","requirement_id","selector"} or set(sel)!={"metric","year","month","period_kind"} or
                    sel["period_kind"]!="month" or (r.capability=="macro.pce_release_snapshot" and (r.params.get("year")!=sel["year"] or r.params.get("month")!=sel["month"]))):
                raise DataError("invalid_request","BEA binding requires the exact planned monthly reported rate")
            if target!=bea_requirement(sel["metric"],sel["year"],sel["month"],b["fact_id"]):
                raise DataError("research_fact_contract_mismatch","BEA reported percent cannot fill raw index or recomputed rate requirements")
            dates.add(r.as_of_date)
            continue
        if r.provider=="fred" and r.capability=="macro.series_snapshot":
            keys={"series_id","observation_date","units","frequency","seasonal_adjustment"}
            if (set(sel)!=keys or any(not isinstance(v,str) for v in sel.values()) or
                    sel["series_id"] not in SERIES or sel["units"]!=NATIVE_UNIT or
                    sel["frequency"]!="Monthly" or sel["seasonal_adjustment"]!="Seasonally Adjusted"):
                raise DataError("invalid_request","FRED handoff requires fixed PCE native monthly identity and units")
            if not isinstance(b.get("metadata_requirement_id"),str):
                raise DataError("invalid_request","FRED binding needs an explicit planned metadata requirement ID")
            meta=requirements.get(b["metadata_requirement_id"])
            if (meta is None or meta.provider!="fred" or meta.capability!="macro.series_metadata_snapshot" or
                    meta.as_of_date!=r.as_of_date or meta.params.get("series_id")!=sel["series_id"] or
                    r.params.get("series_id")!=sel["series_id"] or
                    meta.params.get("vintage_date")!=r.params.get("vintage_date") or not r.params.get("vintage_date")):
                raise DataError("source_mismatch","FRED source/metadata must be separately planned with one explicit series/vintage/cutoff")
            if target!=native_requirement(sel["series_id"],sel["observation_date"]):
                raise DataError("research_fact_contract_mismatch","FRED raw facts retain exact monthly index identity, date and native unit")
            if not r.params.get("start_date","")<=sel["observation_date"]<=r.params.get("end_date",r.params["vintage_date"]):
                raise DataError("insufficient_coverage","Native monthly comparison is outside the planned window")
            dates.add(r.as_of_date)
            continue
        if r.provider not in {"nbs","pbc"} or r.capability!="macro.release_snapshot":
            raise DataError("mapping_unavailable","This handoff supports NBS/PBC pages, BEA reported PCE, FRED indices and SEC native current snapshots only")
        if "metadata_requirement_id" in b:
            raise DataError("invalid_request","Native prose bindings do not accept a metadata requirement")
        if (set(sel)!={"metric","year","month","period_kind"} or type(sel["year"]) is not int or not 1<=sel["year"]<=9999 or
                type(sel["month"]) is not int or not 1<=sel["month"]<=12 or not isinstance(sel["period_kind"],str) or sel["period_kind"] not in {"month","year_to_date"} or
                not isinstance(sel["metric"],str) or sel["metric"] not in (NBS_PATTERNS if r.provider=="nbs" else PBC_PATTERNS)):
            raise DataError("invalid_request","Use the versioned native metric and explicit monthly/cumulative selector")
        y,m=sel["year"],sel["month"]
        start=f"{y:04d}-{'01' if sel['period_kind']=='year_to_date' else f'{m:02d}'}-01"
        end=f"{y:04d}-{m:02d}-{calendar.monthrange(y,m)[1]:02d}"
        if (target["entity"]!="CN" or target["metric"]!=sel["metric"] or target["unit"]!="percent" or target["value_type"]!="number" or
                target["data_period"]!=start+"/"+end or not target["observation_start"]<=end<=target["observation_end"]):
            raise DataError("research_fact_contract_mismatch","Core requirements must match native entity/metric/unit/period, without relabeling")
        dates.add(r.as_of_date)
    if seen!=set(expected) or len(dates)!=1:
        raise DataError("insufficient_coverage","Bind every required non-price fact with one common cutoff date")
    contract={"schema_version":"source-core-outlook-v1","source_run_id":run.run_id,"scope_key":run.core_scope_key,
        "as_of_date":next(iter(dates)),"specification":spec,"bindings":bindings}
    return json.loads(canonical(contract))


def macro_evidence(run):
    if run._core_contract_document is None:
        raise DataError("plan_required","Freeze the source/core contract before fetch")
    contract=json.loads(run._core_contract_document)
    stored=json.loads((run.directory/"core-contract.json").read_text())
    if stored!=contract:
        raise DataError("hash_mismatch","The frozen source/core contract was modified")
    if contract["source_run_id"]!=run.run_id or contract["scope_key"]!=run.core_scope_key:
        raise DataError("scope_mismatch","Core contract belongs to another source run or scope")
    cutoff=timestamp(contract["specification"]["quant_spec"]["cutoff_timestamp"])
    facts=[];sources=[]
    for binding in contract["bindings"]:
        selected=run.select(binding["requirement_id"],binding["selector"],metadata_requirement_id=binding.get("metadata_requirement_id"))
        obs=selected["observation"];source=selected["provenance"][0]
        if selected["provider"]=="sec":
            from .sec_handoff import fact_from_selection
            facts.append(fact_from_selection(run,binding,selected,cutoff,contract["as_of_date"]))
            sources.append({"fact_id":binding["fact_id"],"requirement_id":binding["requirement_id"],
                "selection_hash":selected["selection_hash"],"input_hashes":selected["input_hashes"],"input_provenance":selected["provenance"],**source})
            continue
        if (obs.get("eligibility")!="verified_current_snapshot" or obs.get("availability_basis")!="observed_current_snapshot" or
                obs.get("historical_vintage_certified") is not False or source.get("as_of_date")!=contract["as_of_date"] or
                source.get("provider") not in {"nbs","pbc","fred","bea"}):
            raise DataError("unverified_evidence","Historical unverified pages cannot be promoted by handoff")
        if timestamp(obs["available_at"])>cutoff:
            raise DataError("future_data","Snapshot was captured after the frozen core availability cutoff")
        availability={"basis":"observed_current_snapshot","snapshot_as_of_date":obs["snapshot_as_of_date"],
            "historical_vintage_certified":False,"publisher_available_at":obs["publisher_available_at"],
            "publication_time_precision":obs["publication_time_precision"],"selection_hash":selected["selection_hash"],"source_run_id":run.run_id,"source_notes":obs["source_notes"]}
        if selected["provider"]=="fred":
            native=native_requirement(obs["series_id"],obs["observation_date"])
            if obs["series_title"]!=SERIES[obs["series_id"]]["title"]:
                raise DataError("source_mismatch","Native PCE title differs from the fixed core mapping")
            availability["fred_series"]={"series_id":obs["series_id"],"title":obs["series_title"],"vintage_date":obs["vintage_date"],
                "frequency":obs["frequency"],"seasonal_adjustment":obs["seasonal_adjustment"],
                "last_updated":obs["series_last_updated"],"source_date_timezone":obs["source_date_timezone"]}
            entity,metric,period,day=native["entity"],native["metric"],native["data_period"],native["observation_end"]
        else:
            entity,metric,period,day="US" if selected["provider"]=="bea" else "CN",obs["metric"],obs["period_start"]+"/"+obs["period_end"],obs["period_end"]
        fact={"fact_id":binding["fact_id"],"entity":entity,"metric":metric,"value":obs["value"],"unit":obs["unit"],
            "data_period":period,"observation_date":day,
            "publication_date":obs["publication_date"],"source":selected["provider"],"source_file":str(run.directory/source["artifact"]),
            "fallback_status":"none","available_at":obs["available_at"],"availability":availability}
        facts.append(fact);sources.append({"fact_id":binding["fact_id"],"requirement_id":binding["requirement_id"],
            "selection_hash":selected["selection_hash"],"input_hashes":selected["input_hashes"],"input_provenance":selected["provenance"],**source})
    data={"schema_version":"macro-release-facts-v2","facts":facts}
    path=run.directory/"core-macro-evidence.json"
    provenance={"source":"planned native macro current snapshots","source_file":str(path),
        "source_timestamp":max((f["available_at"] for f in facts),key=timestamp),"publication_date":contract["as_of_date"],
        "observation_date":max(f["observation_date"] for f in facts),"data_period":"explicit per-fact source periods",
        "sha256":digest(data),"availability_basis":"observed_current_snapshot","historical_vintage_certified":False,
        "publication_date_basis":"aggregate_snapshot_capture_date_not_original_release",
        "source_run_id":run.run_id,"source_contract_hash":digest(contract),
        "research_spec_hash":digest(contract["specification"]["research_spec"]),"source_selections":sources}
    envelope={"capability":"macro_release_facts","scope_key":run.core_scope_key,"data":data,"provenance":provenance,"fallback_status":"none"}
    if path.exists():
        if json.loads(path.read_text())!=envelope:
            raise DataError("hash_mismatch","A pinned source/core handoff cannot change")
    else:run._save(path.name,envelope)
    return envelope
