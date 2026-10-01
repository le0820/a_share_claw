"""Trusted SEC current-capture handoff with native identity frozen before acquisition."""
from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from ..harness.contracts import canonical,digest
from ..harness.research import checked_spec
from ..harness.sec_facts import native_requirement
from ..harness.quant import timestamp
from .core import DataError,Requirement


def checked_binding(requirements,binding,target):
    if set(binding)!={"fact_id","requirement_id","metadata_requirement_id","selector"}:
        raise DataError("invalid_request","SEC current facts require explicit fact and filing-metadata bindings")
    selector=binding["selector"]
    try:native=native_requirement(selector,binding["fact_id"])
    except ValueError:raise DataError("invalid_request","Pin exact SEC CIK/concept/native unit/duration/filed/accession") from None
    r=requirements.get(binding["requirement_id"]);m=requirements.get(binding["metadata_requirement_id"])
    if (r is None or m is None or r.provider!="sec" or m.provider!="sec" or r.capability!="company.facts_snapshot" or
            m.capability!="company.filing_metadata_snapshot" or r.as_of_date!=m.as_of_date or
            str(r.params.get("cik","")).zfill(10)!=selector["cik"] or str(m.params.get("cik","")).zfill(10)!=selector["cik"] or
            selector["concept"] not in r.params.get("concepts",[]) or m.params.get("accession")!=selector["accession"] or
            selector["filed"]>r.as_of_date or selector["period_end"]<r.params.get("start_date","1900-01-01")):
        raise DataError("source_mismatch","Bind current SEC fact and metadata from one planned company/accession/cutoff")
    if target!=native:
        raise DataError("research_fact_contract_mismatch","Core preserves native CIK/concept/unit/exact duration without economic aliases")
    return r.as_of_date


def freeze_research_contract(run,plan,spec,bindings,cutoff_timestamp,workflow):
    if run.core_scope_key is None:raise DataError("scope_mismatch","Core research handoff requires trusted four-field Scope")
    if workflow not in {"company","industry"}:raise DataError("invalid_request","Use company/industry research; outlook has its separate joint contract")
    spec=checked_spec(spec,workflow=workflow);cutoff=timestamp(cutoff_timestamp)
    if not isinstance(plan,dict) or set(plan)!={"framework","requirements"} or not isinstance(plan["requirements"],list):
        raise DataError("invalid_request","Freeze the explicit research source plan before acquisition")
    requirements={r.requirement_id:r for r in (Requirement.parse(obj) for obj in plan["requirements"])}
    expected={f["fact_id"]:f for f in spec["required_facts"]};seen=set();dates=set()
    if not isinstance(bindings,list) or len(bindings)!=len(expected):raise DataError("insufficient_coverage","Bind every research fact before acquisition")
    for b in bindings:
        if (not isinstance(b,dict) or not isinstance(b.get("fact_id"),str) or b["fact_id"] not in expected or b["fact_id"] in seen or
                any(not isinstance(b.get(k),str) for k in ("requirement_id","metadata_requirement_id"))):
            raise DataError("invalid_request","Bind each fixed research fact exactly once")
        dates.add(checked_binding(requirements,b,expected[b["fact_id"]]));seen.add(b["fact_id"])
    if len(dates)!=1 or cutoff.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()!=next(iter(dates)):
        raise DataError("source_mismatch","Current research cutoff must use the common current capture date")
    return json.loads(canonical({"schema_version":"source-core-research-v1","source_run_id":run.run_id,"scope_key":run.core_scope_key,
        "as_of_date":next(iter(dates)),"workflow":workflow,"cutoff_timestamp":cutoff_timestamp,
        "specification":{"research_spec":spec},"bindings":bindings}))


def fact_from_selection(run,binding,selected,cutoff,as_of_date):
    obs=selected["observation"];p=selected["provenance"][0]
    if (selected["provider"]!="sec" or obs.get("eligibility")!="verified_current_snapshot" or obs.get("availability_basis")!="observed_current_snapshot" or
            obs.get("historical_vintage_certified") is not False or obs.get("snapshot_as_of_date")!=as_of_date or
            timestamp(obs["available_at"])>cutoff or p["as_of_date"]!=as_of_date):
        raise DataError("unverified_evidence","Only paired current SEC captures before the frozen cutoff can enter core")
    native=native_requirement(binding["selector"],binding["fact_id"])
    return {"fact_id":binding["fact_id"],**{key:native[key] for key in ("entity","metric","unit","data_period")},
        "value":obs["value"],"observation_date":obs["period_end"],"publication_date":obs["filed"],"source":"sec",
        "source_file":str(run.directory/p["artifact"]),"fallback_status":"none","available_at":obs["available_at"],
        "availability":{"basis":"observed_current_snapshot","snapshot_as_of_date":as_of_date,"historical_vintage_certified":False,
            "publisher_available_at":None,"publication_time_precision":"day","selection_hash":selected["selection_hash"],
            "source_run_id":run.run_id,"source_notes":obs["source_notes"],"sec_filing":obs["sec_filing"]}}


def research_evidence(run):
    if run._core_contract_document is None:raise DataError("plan_required","Freeze source/core research bindings before fetch")
    contract=json.loads(run._core_contract_document)
    if json.loads((run.directory/"core-contract.json").read_text())!=contract:raise DataError("hash_mismatch","Frozen research contract changed")
    if contract["schema_version"]!="source-core-research-v1":raise DataError("plan_required","Use the frozen company/industry research contract")
    if contract["source_run_id"]!=run.run_id or contract["scope_key"]!=run.core_scope_key:raise DataError("scope_mismatch","Research contract belongs to another run/scope")
    cutoff=timestamp(contract["cutoff_timestamp"]);facts=[];selections=[]
    for b in contract["bindings"]:
        selected=run.select(b["requirement_id"],b["selector"],metadata_requirement_id=b["metadata_requirement_id"])
        facts.append(fact_from_selection(run,b,selected,cutoff,contract["as_of_date"]))
        selections.append({"fact_id":b["fact_id"],"selection_hash":selected["selection_hash"],"input_hashes":selected["input_hashes"],"input_provenance":selected["provenance"]})
    data={"schema_version":"research-facts-v2","facts":facts};path=run.directory/"core-research-evidence.json"
    provenance={"source":"planned SEC native current snapshots","source_file":str(path),"sha256":digest(data),
        "source_timestamp":max((f["available_at"] for f in facts),key=timestamp),"publication_date":contract["as_of_date"],
        "publication_date_basis":"aggregate_snapshot_capture_date_not_original_release","observation_date":max(f["observation_date"] for f in facts),
        "data_period":"explicit native filing durations","source_run_id":run.run_id,"source_contract_hash":digest(contract),
        "research_spec_hash":digest(contract["specification"]["research_spec"]),"source_selections":selections}
    envelope={"capability":"primary_documents","scope_key":run.core_scope_key,"data":data,"provenance":provenance,"fallback_status":"none"}
    if path.exists():
        if json.loads(path.read_text())!=envelope:raise DataError("hash_mismatch","Pinned research evidence cannot change")
    else:run._save(path.name,envelope)
    return envelope
