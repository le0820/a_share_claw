"""Research outlook from admitted releases and core-derived price statistics."""
from __future__ import annotations

import json

from .contracts import canonical, validate_date
from .quant import METRIC_UNITS, checked_quant_spec, timestamp
from .research import catalog, checked_spec, checked_snapshot_availability
from .macro_derivation import contracts as macro_contracts, calculate as derive_macro


def checked_outlook_spec(spec):
    if not isinstance(spec, dict) or set(spec) != {"quant_spec", "research_spec", "forecast_start", "forecast_end"}:
        raise ValueError("invalid_outlook_spec")
    quant = checked_quant_spec(spec["quant_spec"])
    research = checked_spec(spec["research_spec"], workflow="outlook")
    if not quant["window_end"] < validate_date(spec["forecast_start"]) <= validate_date(spec["forecast_end"]):
        raise ValueError("invalid_outlook_spec")
    macro_contracts(research)
    questions = {q["question_id"]: q for q in research["questions"]}
    if any(questions.get(key, {}).get("role") != role for key, role in (("base_scenario", "hong_guan"), ("market_comparison", "hong_guan"), ("risk_monitoring", "ping_heng"))):
        raise ValueError("invalid_outlook_spec")
    # All computed metric identities are known before data acquisition.
    computed = {f"price.{a['symbol']}.{m}" for a in quant["assets"] for m in quant["metrics"]}
    if not computed <= {f["fact_id"] for f in research["required_facts"]}:
        raise ValueError("invalid_outlook_spec")
    if not computed <= set(questions["market_comparison"]["required_fact_ids"]):
        raise ValueError("invalid_outlook_spec")
    return {"quant_spec": quant, "research_spec": research,
            "forecast_horizon": {"start": spec["forecast_start"], "end": spec["forecast_end"]}}


def derived_requirements(spec):
    return [{"fact_id": f"price.{a['symbol']}.{metric}", "entity": a["symbol"], "metric": metric,
             "unit": METRIC_UNITS[metric], "value_type": "number", "data_period": spec["window_start"] + "/" + spec["window_end"],
             "observation_start": a["sessions"][-1]["trade_date"], "observation_end": a["sessions"][-1]["trade_date"]}
            for a in spec["assets"] for metric in spec["metrics"]]


def outlook_facts(releases, quant, descriptor, cutoff_date, research_spec=None, macro_archive=None):
    if not isinstance(releases, dict) or set(releases) != {"schema_version", "facts"} or releases["schema_version"] not in {"macro-release-facts-v1","macro-release-facts-v2"} or not isinstance(releases["facts"], list):
        raise ValueError("invalid_outlook_facts")
    cutoff = timestamp(quant["specification"]["cutoff_timestamp"])
    snapshots=releases["schema_version"]=="macro-release-facts-v2"
    version="research-facts-v2" if snapshots else "research-facts-v1"
    facts = []
    for raw in releases["facts"]:
        if not isinstance(raw, dict) or "available_at" not in raw or not isinstance(raw.get("fact_id"), str) or raw["fact_id"].startswith("price.") or raw.get("source") in {"core_macro_v1","core_quant_v1"}:
            raise ValueError("invalid_outlook_facts")
        available = timestamp(raw["available_at"])
        if available > cutoff:
            raise ValueError("future_data")
        if snapshots:
            checked_snapshot_availability(raw,cutoff_date)
            facts.append(json.loads(canonical(raw)))
        else:
            if available.date().isoformat() != raw["publication_date"]:
                raise ValueError("invalid_outlook_facts")
            facts.append({key: value for key, value in raw.items() if key != "available_at"})
    admitted=catalog({"schema_version": version, "facts": facts}, cutoff_date)
    if research_spec is not None:
        computed=derive_macro(admitted,research_spec)
        if computed:
            if macro_archive is None:
                raise ValueError("missing_provenance")
            archive=macro_archive({"schema_version":"core-macro-v1","contracts":macro_contracts(research_spec),
                "input_facts":[admitted[key] for key in sorted({key for f in computed for key in f["derivation"]["input_fact_ids"]})],
                "calculations":computed})
            facts.extend({**fact,"source_file":archive["path"]} for fact in computed)
    for contract in derived_requirements(quant["specification"]):
        symbol, metric = contract["entity"], contract["metric"]
        value = quant["metrics"][symbol][metric]
        if value is None:
            raise ValueError("insufficient_coverage:outlook_metric")
        facts.append({"fact_id": contract["fact_id"], "entity": symbol, "metric": metric, "value": value,
                      "unit": contract["unit"], "data_period": contract["data_period"],
                      "source": "core_quant_v1", "source_file": descriptor["path"],
                      "publication_date": quant["series_audit"][symbol]["publication_date"],
                      "observation_date": contract["observation_end"], "fallback_status": "none"})
    return {"primary_documents": {"schema_version": version, "facts": facts}}
