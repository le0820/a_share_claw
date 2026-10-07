"""Explicit trusted-host plugin adapter; models never choose or execute sources."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from ..harness.acquisition import EvidenceBatch
from ..harness.contracts import Scope,canonical
from .core import DataError,DataRun,Requirement


class PluginEvidenceAdapter:
    def __init__(self,artifact_root,configuration,*,registry=None,settings=None,providers=None):
        keys={"source_plan","bindings","price_bindings","cutoff_timestamp"}
        if not isinstance(configuration,dict) or set(configuration)!=keys:
            raise ValueError("source_contract_mismatch")
        self._configuration=canonical(configuration)
        self.artifact_root=Path(artifact_root)
        self.settings=dict(os.environ if settings is None else settings)
        self.registry=registry
        self.providers=None if providers is None else dict(providers)

    def prepare(self,request):
        try:return self._prepare(request)
        except ValueError as exc:
            if str(exc)=='rotation_source_binding_missing':raise
            raise ValueError('source_contract_mismatch') from None
        except (DataError,KeyError,TypeError):raise ValueError("source_contract_mismatch") from None

    def _prepare(self,request):
        e=request.json();plan=e["plan"];scope=Scope(**e["scope"]);needed=set(e["needed_capabilities"]);existing=e["existing_packet"]
        if (plan["mode"]!="research" or plan["workflow"] not in {"quant","outlook","company","industry"} or scope.key!=plan["scope_key"] or
                not needed<=set(plan["required_capabilities"])):raise ValueError("source_contract_mismatch")
        ticket={"schema_version":"evidence-batch-v1","core_run_id":e["core_run_id"],"plan_id":plan["plan_id"],"scope_key":scope.key,
            "as_of_date":plan["as_of_date"],"source_run_id":None,"requirements":[]}
        if needed=={"fund_flow_snapshot"}:
            from .flow_handoff import prepare_flow
            return prepare_flow(self,request)
        if not needed:
            return EvidenceBatch(canonical(ticket),lambda _:None,lambda _:json.loads(canonical(existing)))
        if 'industry_classification' in needed:raise ValueError('rotation_source_binding_missing')
        c=json.loads(self._configuration);source_plan=c["source_plan"]
        if not isinstance(source_plan,dict) or set(source_plan)!={"framework","requirements"}:raise ValueError("source_contract_mismatch")
        parsed=[Requirement.parse(r) for r in source_plan["requirements"]]
        if len({r.requirement_id for r in parsed})!=len(parsed):raise ValueError("source_contract_mismatch")
        if not isinstance(c["bindings"],list) or not isinstance(c["price_bindings"],list):raise ValueError("source_contract_mismatch")
        fact_bindings=c["bindings"] if needed & {"macro_release_facts","primary_documents"} else []
        price_bindings=c["price_bindings"] if "price_history" in needed else []
        used={b["requirement_id"] for b in fact_bindings+price_bindings}
        used|={b["metadata_requirement_id"] for b in fact_bindings if "metadata_requirement_id" in b}
        declarations={r.requirement_id for r in parsed}
        all_used={b["requirement_id"] for b in c["bindings"]+c["price_bindings"]}|{b["metadata_requirement_id"] for b in c["bindings"] if "metadata_requirement_id" in b}
        if declarations!=all_used or not used<=declarations:raise ValueError("source_contract_mismatch")
        selected=[r for r in parsed if r.requirement_id in used]
        if any(r.as_of_date!=plan["as_of_date"] or not r.required for r in selected):raise ValueError("source_contract_mismatch")
        wanted={r.provider for r in selected}
        if self.providers is not None:providers={k:v for k,v in self.providers.items() if k in wanted}
        else:
            from . import default_registry
            settings=dict(self.settings)
            enabled={k.strip() for k in settings.get("ASCLAW_DATA_PROVIDERS","nbs,pbc,easytdx,bea,sec").split(",") if k.strip()}
            settings["ASCLAW_DATA_PROVIDERS"]=",".join(sorted(wanted & enabled))
            providers=(self.registry or default_registry()).snapshot(settings)
        run=DataRun(providers,self.artifact_root,scope)
        selected_plan={"framework":source_plan["framework"],"requirements":[r.json() for r in selected]};p=plan["parameters"]
        if "macro_release_facts" in needed:
            spec={"quant_spec":p["quant_spec"],"research_spec":p["research_spec"],"forecast_start":p["forecast_horizon"]["start"],"forecast_end":p["forecast_horizon"]["end"]}
            run.plan_core_outlook(selected_plan,spec,fact_bindings,price_bindings=price_bindings if "price_history" in needed else None)
        elif "primary_documents" in needed:
            run.plan_core_research(selected_plan,p["research_spec"],fact_bindings,cutoff_timestamp=c["cutoff_timestamp"],workflow=plan["workflow"])
        elif needed=={"price_history"}:
            run.plan_core_quant(selected_plan,p["quant_spec"],price_bindings)
        else:raise ValueError("source_contract_mismatch")
        run._save("host-core-link.json",{"core_run_id":e["core_run_id"],"plan_id":plan["plan_id"],"scope_key":scope.key,
            "as_of_date":plan["as_of_date"],"source_run_id":run.run_id,"needed_capabilities":sorted(needed)})
        ticket.update(source_run_id=run.run_id,requirements=[r.json() for r in selected])
        frozen_ticket=canonical(ticket)
        def fetch(call):
            v=call.json()
            if v!={"core_run_id":e["core_run_id"],"source_run_id":run.run_id,"requirement_id":v.get("requirement_id")} or v["requirement_id"] not in run.requirements:
                raise ValueError("source_contract_mismatch")
            return asyncio.run(run.fetch(v["requirement_id"]))
        def finish(call):
            if call.document!=request.document or canonical(ticket)!=frozen_ticket:raise ValueError("source_contract_mismatch")
            try:
                new=[]
                if "macro_release_facts" in needed:new.append(run.core_macro_evidence())
                if "primary_documents" in needed:new.append(run.core_research_evidence())
                if "price_history" in needed:new.append(run.core_price_evidence())
            except DataError as exc:
                code=exc.code if exc.code in {"future_data","hash_mismatch","scope_mismatch","unverified_evidence"} else "insufficient_coverage:source_handoff."+exc.code
                raise ValueError(code) from None
            return {"as_of_date":plan["as_of_date"],"facts":json.loads(canonical(existing["facts"]))+new}
        return EvidenceBatch(frozen_ticket,fetch,finish)
