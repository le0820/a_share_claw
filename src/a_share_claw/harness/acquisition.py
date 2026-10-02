"""Provider-independent trusted host acquisition after frozen planning."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .contracts import canonical,digest
from .research import bounded_call,catalog,match_facts
from .quant import compute_quant,timestamp
from .macro_derivation import contracts as macro_contracts


@dataclass(frozen=True)
class EvidenceRequest:
    document: str
    remaining_seconds: float

    def json(self):return json.loads(self.document)


@dataclass(frozen=True)
class EvidenceBatch:
    document: str
    fetch: object
    finish: object

    def json(self):return json.loads(self.document)


def checked_existing(plan,facts,reference,market_timezone):
    p=plan["parameters"];workflow=plan["workflow"]
    if "price_history" in facts:
        compute_quant(p["quant_spec"],facts["price_history"],reference,plan["as_of_date"],market_timezone)
    if "primary_documents" in facts:
        match_facts(p["research_spec"],catalog(facts["primary_documents"],plan["as_of_date"]))
    if "macro_release_facts" in facts:
        body=facts["macro_release_facts"];snap=body.get("schema_version")=="macro-release-facts-v2"
        if body.get("schema_version") not in {"macro-release-facts-v1","macro-release-facts-v2"} or not isinstance(body.get("facts"),list):
            raise ValueError("invalid_outlook_facts")
        raw=[]
        for f in body["facts"]:
            if (not isinstance(f,dict) or f.get("source") in {"core_macro_v1","core_quant_v1"} or f.get("fact_id","").startswith("price.") or
                    timestamp(f["available_at"])>timestamp(p["quant_spec"]["cutoff_timestamp"])):raise ValueError("invalid_outlook_facts")
            if not snap and timestamp(f["available_at"]).date().isoformat()!=f["publication_date"]:raise ValueError("invalid_outlook_facts")
            raw.append(f if snap else {k:v for k,v in f.items() if k!="available_at"})
        computed={c["target"]["fact_id"] for c in macro_contracts(p["research_spec"])}
        spec={**p["research_spec"],"required_facts":[f for f in p["research_spec"]["required_facts"] if not f["fact_id"].startswith("price.") and f["fact_id"] not in computed]}
        match_facts(spec,catalog({"schema_version":"research-facts-v2" if snap else "research-facts-v1","facts":raw},plan["as_of_date"]),questions=False)


def acquire(engine,session,policy,plan,packet,adapter,output):
    if session.request.mode!="research" or plan["workflow"] not in {"company","industry","quant","outlook"}:
        raise ValueError("source_acquisition_not_authorized")
    if plan["unresolved_constraints"]:raise ValueError("planning_constraints_required")
    admitted,_=engine._evidence(session,policy,plan,packet,plan["as_of_date"],record=False)
    checked_existing(plan,admitted,session.evaluation_clock,engine.market_timezone)
    needed=[c for c in plan["required_capabilities"] if c not in admitted]
    scope=session.request.scope
    payload={"core_run_id":session.run_id,"scope":scope.__dict__,"plan":plan,"needed_capabilities":needed,
        "existing_packet":packet or {"as_of_date":plan["as_of_date"],"facts":[]},"evaluation_timestamp":session.evaluation_clock.isoformat()}
    session.step("source_prepare",{"plan_id":plan["plan_id"],"needed_capabilities":needed})
    request=EvidenceRequest(canonical(payload),session.remaining)
    with session.action("source", "prepare_batch", "bind_frozen_gaps_before_fetch", {"plan_id":plan["plan_id"],"needed_capabilities":needed}) as span:
        batch=bounded_call(adapter.prepare,request,session.remaining,session.control)
        span.observe(batch_hash=digest(batch.json()) if isinstance(batch,EvidenceBatch) else None)
    if not isinstance(batch,EvidenceBatch):raise ValueError("invalid_source_batch")
    ticket=batch.json();keys={"schema_version","core_run_id","plan_id","scope_key","as_of_date","source_run_id","requirements"}
    if (set(ticket)!=keys or ticket["schema_version"]!="evidence-batch-v1" or ticket["core_run_id"]!=session.run_id or
            ticket["plan_id"]!=plan["plan_id"] or ticket["scope_key"]!=scope.key or ticket["as_of_date"]!=plan["as_of_date"] or
            not isinstance(ticket["requirements"],list) or len(ticket["requirements"])>100):raise ValueError("invalid_source_batch")
    if (ticket["requirements"] and (not isinstance(ticket["source_run_id"],str) or not re.fullmatch(r"[a-f0-9]{32}",ticket["source_run_id"])) or
            not ticket["requirements"] and ticket["source_run_id"] is not None):raise ValueError("invalid_source_batch")
    ids=set()
    for r in ticket["requirements"]:
        if (not isinstance(r,dict) or set(r)!={"requirement_id","provider","capability","as_of_date","params","required"} or
                not isinstance(r["requirement_id"],str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",r["requirement_id"]) or
                r["requirement_id"] in ids or r["as_of_date"]!=plan["as_of_date"] or not isinstance(r["params"],dict) or r["required"] is not True):
            raise ValueError("invalid_source_batch")
        ids.add(r["requirement_id"])
    if len(ids)>session.request.max_tool_calls-session.tool_count:raise TimeoutError("budget_exceeded")
    engine._archive(session,"source_batch",ticket)
    summary={"source_run_id":ticket["source_run_id"],"planned_calls":len(ids),"completed_calls":0,"gaps":[]}
    output["source_acquisition"]=summary
    for r in ticket["requirements"]:
        session.checkpoint()
        call=EvidenceRequest(canonical({"core_run_id":session.run_id,"source_run_id":ticket["source_run_id"],"requirement_id":r["requirement_id"]}),session.remaining)
        envelope=session.source_tool("source."+r["provider"]+"."+r["capability"],r,batch.fetch,call)
        summary["completed_calls"]+=1
        if not envelope["ok"] or envelope["status"]!="ok" or envelope["fallback_status"]!="none" or envelope["truncated"]:
            summary["gaps"].append({"requirement_id":r["requirement_id"],"status":envelope["status"],"error_code":envelope["error_code"],"retryable":envelope["retryable"]})
    session.step("source_acquisition",summary)
    if summary["gaps"]:
        output["gaps"]=summary["gaps"]
        raise ValueError("missing_required_data")
    session.checkpoint()
    with session.action("source", "finish_batch", "verify_source_archives_and_construct_fact_packet", {"source_run_id":ticket["source_run_id"]}) as span:
        result=bounded_call(batch.finish,EvidenceRequest(canonical(payload),session.remaining),session.remaining,session.control)
        span.observe(packet_hash=digest(result))
    if not isinstance(result,dict) or set(result)!={"as_of_date","facts"}:raise ValueError("invalid_evidence")
    session.checkpoint()
    if plan!=payload["plan"] or not policy.unchanged():raise ValueError("plan_changed")
    session.step("source_handoff",{"source_run_id":ticket["source_run_id"],"packet_hash":digest(result),"plan_id":plan["plan_id"]})
    return result
