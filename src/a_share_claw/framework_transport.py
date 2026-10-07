"""Bounded model-visible metadata for complete code-owned host parameters.

Only framework stages use this view. Admitted research facts and role packets
remain complete. The core retains and validates the original calendar lists.
"""
from .harness.contracts import digest


def metadata_view(value):
    if isinstance(value,dict):
        return {key:({'count':len(item),'first':item[0] if item else None,'last':item[-1] if item else None,
                      'sha256':digest(item),'representation':'calendar_summary_not_executable_sessions'}
                     if key=='sessions' and isinstance(item,list) else metadata_view(item))
                for key,item in value.items()}
    if isinstance(value,list):return [metadata_view(item) for item in value]
    return value


def reference_schema(reference):
    def obj(fields):
        return {'type':'object','properties':fields,'required':list(fields),'additionalProperties':False}
    string={'type':'string'}
    return obj({'framework':string,'parameters_ref':obj({key:{'type':'string','enum':[value]} for key,value in reference.items()}),
                'unresolved_constraints':{'type':'array','maxItems':30,'items':obj({'constraint':string,'reason':string})}})
