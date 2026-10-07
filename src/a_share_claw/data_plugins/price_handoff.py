"""Trusted index-to-core handoff. Freeze identity/calendar before acquisition."""
from __future__ import annotations

import json
from zoneinfo import ZoneInfo

from ..harness.contracts import canonical,digest
from ..harness.quant import checked_quant_spec,timestamp
from .core import DataError,Requirement
from .normalization import checked_result
from .tdx import INDEXES,checked_params


def freeze_price_bindings(plan,spec,bindings):
    spec=checked_quant_spec(spec)
    requirements={r.requirement_id:r for r in (Requirement.parse(obj) for obj in plan["requirements"])}
    expected={a["symbol"]:a for a in spec["assets"]};seen=set()
    if not isinstance(bindings,list) or len(bindings)!=len(expected):
        raise DataError("insufficient_coverage","Bind every frozen core index to a planned native source")
    for b in bindings:
        if (not isinstance(b,dict) or set(b)!={"symbol","requirement_id"} or
                not isinstance(b["symbol"],str) or b["symbol"] not in expected or b["symbol"] in seen or
                not isinstance(b["requirement_id"],str) or b["requirement_id"] not in requirements):
            raise DataError("invalid_request","Each frozen index binds exactly one explicit requirement")
        seen.add(b["symbol"]);req=requirements[b["requirement_id"]];asset=expected[b["symbol"]]
        if spec['schema_version']=='quant-spec-v2':
            from .swresearch import checked_params as checked_sw_params
            if req.provider!='swresearch' or req.capability!='market.sw_index_daily_snapshot':
                raise DataError('source_denied','SW31 rotation uses only explicitly enabled Shenwan daily snapshots')
            checked_sw_params(req)
            if (req.params['symbol']!=asset['symbol'] or req.params['start_date']!=asset['anchor']['trade_date'] or
                    req.params['end_date']!=asset['sessions'][-1]['trade_date']):
                raise DataError('insufficient_coverage','Freeze the exact Shenwan anchor and final declared session')
            continue
        if req.provider!="easytdx" or req.capability!="market.index_daily_snapshot":
            raise DataError("source_denied","Primary index handoff requires authorized easy-tdx; TickFlow is auxiliary")
        checked_params(req);native=INDEXES.get(b["symbol"])
        if native is None or any(asset[k]!=native[k] for k in ("name","unit","currency","market_timezone")) or asset["adjustment"]!="none":
            raise DataError("market_contract_mismatch","Core index identity/unit/currency/timezone/adjustment must match the fixed source mapping")
        if (req.params["symbol"]!=asset["symbol"] or req.params["start_date"]!=asset["anchor"]["trade_date"] or
                req.params["end_date"]!=spec["window_end"] or req.params["count"]<len(asset["sessions"])+1):
            raise DataError("insufficient_coverage","Plan the exact anchor/window with enough bounded rows for every declared session")
    return json.loads(canonical(bindings))


def freeze_quant_contract(run,plan,spec,bindings):
    if run.core_scope_key is None:raise DataError("scope_mismatch","Core price handoff requires trusted four-field Scope")
    if not isinstance(plan,dict) or set(plan)!={"framework","requirements"}:
        raise DataError("invalid_request","Use an explicit source plan before acquisition")
    spec=checked_quant_spec(spec);bindings=freeze_price_bindings(plan,spec,bindings)
    dates={Requirement.parse(obj).as_of_date for obj in plan["requirements"]}
    if len(dates)!=1:raise DataError("source_mismatch","A price run uses one explicit current core date")
    return {"schema_version":"source-core-quant-v1","source_run_id":run.run_id,"scope_key":run.core_scope_key,
        "as_of_date":next(iter(dates)),"specification":{"quant_spec":spec},"price_bindings":bindings}


def price_evidence(run):
    if run._core_contract_document is None:raise DataError("plan_required","Freeze price bindings before acquisition")
    contract=json.loads(run._core_contract_document)
    if json.loads((run.directory/"core-contract.json").read_text())!=contract:
        raise DataError("hash_mismatch","Frozen source/core price contract changed")
    if contract["source_run_id"]!=run.run_id or contract["scope_key"]!=run.core_scope_key:
        raise DataError("scope_mismatch","Price contract belongs to another run or scope")
    if "price_bindings" not in contract:raise DataError("plan_required","No price bindings were frozen for this source run")
    if contract['specification']['quant_spec']['schema_version']=='quant-spec-v2':
        from .sw_handoff import price_evidence as sw_price_evidence
        return sw_price_evidence(run,contract)
    spec=contract["specification"]["quant_spec"];assets={a["symbol"]:a for a in spec["assets"]};series=[];sources=[]
    for b in contract["price_bindings"]:
        result=run._archived_result(b["requirement_id"]);data=checked_result(result,"easytdx","market.index_daily_snapshot")
        source=result["provenance"];capture=timestamp(source["retrieved_at"]);asset=assets[b["symbol"]]
        if (data["symbol"]!=b["symbol"] or data.get("snapshot_as_of_date")!=contract["as_of_date"] or
                capture.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()!=contract["as_of_date"] or
                data.get("availability_basis")!="observed_current_snapshot" or data.get("historical_vintage_certified") is not False or
                capture>timestamp(spec["cutoff_timestamp"])):
            raise DataError("future_data","Current index capture must fit the fixed core date/cutoff without historical promotion")
        rows={row["trade_date"]:row for row in data["bars"]};sessions=[asset["anchor"],*asset["sessions"]]
        if len(rows)!=len(data["bars"]) or set(rows)!={s["trade_date"] for s in sessions}:
            raise DataError("insufficient_coverage","Exact frozen sessions and anchor are required; no missing-day carry forward")
        if any(timestamp(s["close_at"])>capture for s in sessions):
            raise DataError("price_before_close","Snapshot was captured before a required market close")
        current={"basis":"observed_current_snapshot","snapshot_as_of_date":contract["as_of_date"],
            "historical_vintage_certified":False,"source_run_id":run.run_id,"raw_sha256":source["sha256"],
            "sdk_version":data["sdk_version"],"native_identity":data["native_identity"],"raw_format":"decoded_sdk_response"}
        series.append({"symbol":b["symbol"],**{key:asset[key] for key in ("name","unit","currency","adjustment","market_timezone")},
            "frequency":"daily","source":"easy-tdx current captured native index","source_file":str(run.directory/source["artifact"]),
            "source_timestamp":capture.isoformat(),"publication_date":capture.astimezone(ZoneInfo(asset["market_timezone"])).date().isoformat(),
            "current_snapshot":current,"rows":[{"trade_date":s["trade_date"],"close":rows[s["trade_date"]]["close"],"available_at":capture.isoformat()} for s in sessions]})
        sources.append({"symbol":b["symbol"],"requirement_id":b["requirement_id"],"input_hash":digest(result),**source})
    data={"schema_version":"price-series-v2","series":series};path=run.directory/"core-price-evidence.json"
    provenance={"source":"planned primary easy-tdx index snapshots","source_file":str(path),"sha256":digest(data),
        "source_timestamp":max((s["source_timestamp"] for s in series),key=timestamp),"publication_date":contract["as_of_date"],
        "publication_date_basis":"aggregate_snapshot_capture_date_not_original_release","observation_date":max(a["sessions"][-1]["trade_date"] for a in assets.values()),
        "data_period":spec["window_start"]+"/"+spec["window_end"],"source_run_id":run.run_id,
        "source_contract_hash":digest(contract),"quant_spec_hash":digest(spec),"source_selections":sources}
    envelope={"capability":"price_history","scope_key":run.core_scope_key,"data":data,"provenance":provenance,"fallback_status":"none"}
    if path.exists():
        if json.loads(path.read_text())!=envelope:raise DataError("hash_mismatch","Pinned core price evidence cannot change")
    else:run._save(path.name,envelope)
    return envelope
