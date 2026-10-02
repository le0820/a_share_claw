"""Explicit, immutable, four-field scoped host binding for ordinary chat."""
from __future__ import annotations

import json
from dataclasses import dataclass

from .harness.contracts import Scope, canonical, validate_date
from .harness.mixed import workflow_parameters


@dataclass(frozen=True)
class TrustedChatProfile:
    document: str

    @classmethod
    def parse(cls,configuration):
        keys={'schema_version','scope','as_of_date','workflow','parameters','source_contract'}
        if (not isinstance(configuration,dict) or set(configuration)!=keys or
                configuration['schema_version']!='trusted-chat-host-v1' or
                configuration['workflow'] not in {'company','industry','quant','outlook'} or
                not isinstance(configuration['scope'],dict) or set(configuration['scope'])!={'workspace','principal','session','agent_key'} or
                not isinstance(configuration['parameters'],dict)):
            raise ValueError('invalid_chat_host_profile')
        Scope(**configuration['scope']);validate_date(configuration['as_of_date'])
        workflow=configuration['workflow'];parameters=configuration['parameters']
        expected={'company':{'research_spec'},'industry':{'research_spec'},'quant':{'quant_spec'},'outlook':{'outlook_spec'}}[workflow]
        if set(parameters)!=expected or any(v is None for v in parameters.values()):
            raise ValueError('invalid_chat_host_profile')
        workflow_parameters(workflow,**parameters)
        from .data_plugins.adapter import PluginEvidenceAdapter
        PluginEvidenceAdapter('.',configuration['source_contract'],settings={})  # shape only, no I/O/provider instantiation
        return cls(canonical(configuration))

    def json(self):
        return json.loads(self.document)

    def authorize(self,request,planning_constraints):
        profile=self.json()
        if Scope(**profile['scope']).key!=request.scope.key:
            raise ValueError('chat_host_scope_mismatch')
        if profile['as_of_date']!=request.as_of_date:
            raise ValueError('chat_host_date_mismatch')
        if profile['workflow']!=request.workflow:
            raise ValueError('chat_host_workflow_mismatch')
        if planning_constraints is not None and canonical(planning_constraints)!=canonical(profile['parameters']):
            raise ValueError('chat_host_constraints_mismatch')
        return profile['parameters']

    def evidence_adapter(self,artifact_root):
        from .data_plugins.adapter import PluginEvidenceAdapter
        return PluginEvidenceAdapter(artifact_root,self.json()['source_contract'])
