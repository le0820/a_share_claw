"""Core-owned native filing identities; no economic rescaling or period inference."""
from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from .contracts import validate_date
from .quant import timestamp


def native_requirement(selector, fact_id):
    keys={"cik","concept","unit","period_start","period_end","filed","accession"}
    if (not isinstance(selector,dict) or set(selector)!=keys or not isinstance(fact_id,str) or not fact_id.strip() or
            not isinstance(selector["cik"],str) or not re.fullmatch(r"\d{10}",selector["cik"]) or
            not isinstance(selector["concept"],str) or not re.fullmatch(r"(?:us-gaap|ifrs-full|dei):[A-Za-z0-9_]+",selector["concept"]) or
            not isinstance(selector["unit"],str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_/-]{0,63}",selector["unit"]) or
            not isinstance(selector["accession"],str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}",selector["accession"])):
        raise ValueError("invalid_sec_fact_contract")
    end,filed=validate_date(selector["period_end"]),validate_date(selector["filed"])
    start=validate_date(selector["period_start"]) if selector["period_start"] is not None else None
    if end>filed or start is not None and start>end:raise ValueError("invalid_sec_fact_contract")
    return {"fact_id":fact_id,"entity":"CIK"+selector["cik"],"metric":selector["concept"],"unit":selector["unit"],
        "data_period":start+"/"+end if start else end,"value_type":"number","observation_start":end,"observation_end":end}


def checked_filing(fact,meta,available,cutoff):
    keys={"cik","concept","unit","period_start","period_end","filed","accession","form","report_date","primary_document",
        "acceptance_timestamp_raw","acceptance_timestamp_declared","public_dissemination_certified"}
    if not isinstance(meta,dict) or set(meta)!=keys:raise ValueError("invalid_snapshot_availability")
    target=native_requirement({key:meta[key] for key in ("cik","concept","unit","period_start","period_end","filed","accession")},fact["fact_id"])
    if (fact["source"]!="sec" or any(fact[k]!=target[k] for k in ("entity","metric","unit","data_period")) or
            fact["observation_date"]!=meta["period_end"] or fact["publication_date"]!=meta["filed"] or meta["public_dissemination_certified"] is not False or
            meta["form"] not in {"10-K","10-Q","10-K/A","10-Q/A","20-F","20-F/A","40-F","40-F/A"} or
            not meta["period_end"]<=validate_date(meta["report_date"])<=meta["filed"]<=cutoff or
            not isinstance(meta["primary_document"],str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,256}",meta["primary_document"])):
        raise ValueError("invalid_snapshot_availability")
    declared=timestamp(meta["acceptance_timestamp_declared"]);raw=timestamp(meta["acceptance_timestamp_raw"])
    if declared!=raw or declared>available or declared.astimezone(ZoneInfo("America/New_York")).date().isoformat()>cutoff:
        raise ValueError("invalid_snapshot_availability")


def checked_capture_provenance(fact,provenance):
    selections=provenance.get("source_selections")
    meta=fact.get("availability",{})
    if not isinstance(selections,list):raise ValueError("missing_provenance")
    matches=[s for s in selections if isinstance(s,dict) and s.get("fact_id")==fact["fact_id"]]
    if len(matches)!=1:raise ValueError("missing_provenance")
    selected=matches[0];inputs=selected.get("input_provenance");hashes=selected.get("input_hashes")
    if (meta.get("source_run_id")!=provenance.get("source_run_id") or selected.get("selection_hash")!=meta.get("selection_hash") or
            not isinstance(inputs,list) or len(inputs)!=2 or not isinstance(hashes,list) or len(hashes)!=2 or
            any(not isinstance(h,str) or not re.fullmatch(r"[a-f0-9]{64}",h) for h in hashes)):
        raise ValueError("missing_provenance")
    captures=[]
    for p in inputs:
        if (not isinstance(p,dict) or p.get("provider")!="sec" or p.get("version")!="1.2.0" or p.get("availability")!="verified" or
                p.get("as_of_date")!=meta.get("snapshot_as_of_date") or not isinstance(p.get("sha256"),str) or not re.fullmatch(r"[a-f0-9]{64}",p["sha256"])):
            raise ValueError("missing_provenance")
        capture=timestamp(p["retrieved_at"])
        if capture.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()!=meta["snapshot_as_of_date"]:
            raise ValueError("invalid_snapshot_availability")
        captures.append(capture)
    if timestamp(fact["available_at"])!=max(captures):raise ValueError("invalid_snapshot_availability")
