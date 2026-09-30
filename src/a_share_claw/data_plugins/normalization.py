"""Typed source selections, preserving eligibility; no scoring or publication rights."""
from __future__ import annotations

import json
import math

from ..harness.contracts import canonical, digest
from .core import DataError, iso_date

VERSION = "source-selection-v1"


def checked_result(result, provider, capability):
    provenance=result.get("provenance",{})
    if (not result.get("ok") or result.get("truncated") or result.get("status")!="ok" or
            result.get("fallback_status")!="none" or provenance.get("availability")!="verified"):
        raise DataError("unverified_evidence", "Only intact source-verified results may be selected; no eligibility promotion")
    if provenance.get("provider")!=provider or not isinstance(result.get("data"),dict):
        raise DataError("source_mismatch", "Selected source result does not match its declared provider")
    # DataRun supplies the declared capability, not a model-created source label.
    if result.get("capability")!=capability:
        raise DataError("source_mismatch", "Selected capability differs from the pinned acquisition requirement")
    return result["data"]


def numeric(value):
    if type(value) not in (int,float) or not math.isfinite(value):
        raise DataError("missing_observation", "Selected observation is missing or nonnumeric")
    return value


def fred_observation(result,metadata,*,series_id,observation_date,units,frequency,seasonal_adjustment):
    data=checked_result(result,"fred","macro.series")
    identity=checked_result(metadata,"fred","macro.series_metadata")
    if (data.get("series_id")!=series_id or identity.get("series_id")!=series_id or
            data.get("vintage_date")!=identity.get("vintage_date") or
            result["provenance"].get("as_of_date")!=metadata["provenance"].get("as_of_date") or
            data.get("vintage_date")!=result["provenance"].get("as_of_date")):
        raise DataError("source_mismatch", "Observation and metadata must use the same series and vintage")
    if any(identity.get(key)!=expected for key,expected in (("units",units),("frequency",frequency),("seasonal_adjustment",seasonal_adjustment))):
        raise DataError("unit_or_metric_mismatch", "Native series units, frequency or adjustment do not match the requirement")
    day=iso_date(observation_date)
    rows=[row for row in data.get("observations",[]) if row.get("date")==day]
    if len(rows)!=1:
        raise DataError("insufficient_coverage", "Select exactly one native observation; no latest-value fallback")
    row=rows[0]
    if not iso_date(row["realtime_start"])<=iso_date(data["vintage_date"])<=iso_date(row["realtime_end"]) or day>data["vintage_date"]:
        raise DataError("future_data", "Selected observation does not belong to the requested vintage")
    return _selection("fred",{"series_id":series_id,"observation_date":day,"vintage_date":data["vintage_date"],
        "value":numeric(row["value"]),"unit":units,"frequency":frequency,"seasonal_adjustment":seasonal_adjustment,
        "available_at":None,"availability_precision":"date_level_vintage_only",
        "series_last_updated":identity["last_updated"]},[result,metadata])


def sec_fact(result,*,cik,concept,unit,period_start,period_end,filed,accession):
    if any(not isinstance(v,str) or not v.strip() for v in (cik,concept,unit,accession)):
        raise DataError("invalid_request", "Company, concept, unit and accession must be explicit nonempty identifiers")
    data=checked_result(result,"sec","company.facts")
    if data.get("cik")!=cik or concept in data.get("missing_concepts",[]):
        raise DataError("source_mismatch", "Requested company/concept is unavailable")
    end,filing=iso_date(period_end),iso_date(filed)
    start=iso_date(period_start) if period_start is not None else None
    cutoff=iso_date(result["provenance"]["as_of_date"])
    if end>cutoff or filing>cutoff or start is not None and start>end:
        raise DataError("future_data", "Financial duration/disclosure falls outside the requested cutoff")
    rows=[row for row in data.get("facts",{}).get(concept,[]) if row.get("unit")==unit and row.get("start")==start and
          row.get("end")==end and row.get("filed")==filing and row.get("accn")==accession]
    if not rows:
        raise DataError("insufficient_coverage", "Exact duration/unit/accession not found; no quarterly or latest-filing substitution")
    values={canonical(numeric(row["value"])) for row in rows}
    if len(values)!=1:
        raise DataError("source_disagreement", "The selected accession contains conflicting values")
    return _selection("sec",{"cik":cik,"concept":concept,"unit":unit,"period_start":start,"period_end":end,
        "filed":filing,"accession":accession,"form":rows[0].get("form"),"value":json.loads(next(iter(values))),
        "available_at":None,"availability_precision":"filed_date_only"},[result])


def _selection(provider,observation,results):
    provenance=[json.loads(canonical(result["provenance"])) for result in results]
    selection={"schema_version":VERSION,"provider":provider,"observation":observation,"provenance":provenance,
               "input_hashes":[digest(result) for result in results],"official_output_allowed":False,
               "core_admission_complete":False}
    return {**selection,"selection_hash":digest(selection)}
