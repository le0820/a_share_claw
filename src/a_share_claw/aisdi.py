"""Host-callable, provider-free AISDI specification and requirement tool.

This tool freezes a dated gap framework, not scores or publication permission.
The user's document is reference data; it is never executed or sent as instructions.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


def load_aisdi(root: Path):
    policy_path = root / 'src/compiled/aisdi_rules.json'
    policy = json.loads(policy_path.read_text())
    # Pin the reference path; config cannot request arbitrary file access.
    relative = 'src/compiled/aisdi/model_spec_v1.md'
    if policy['spec_path'] != relative:
        raise ValueError('invalid_spec_path')
    spec_path = root / relative
    raw = spec_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != policy['spec_sha256']:
        raise ValueError('spec_integrity_error')
    if set(policy['groups']) != {'hardware_demand','hardware_supply','model_demand','model_supply'}:
        raise ValueError('invalid_groups')
    for group in policy['groups'].values():
        weights = [Decimal(str(p['weight'])) for p in group]
        if len(group) != 5 or any(w <= 0 for w in weights) or sum(weights) != 1:
            raise ValueError('invalid_pillar_weights')
    weights = policy['top_weights']
    if weights != {'hardware':0.6,'model':0.4} or policy['side_minimum_coverage'] != 0.6:
        raise ValueError('invalid_main_policy')
    return policy, spec_path, raw


def aisdi_spec(root: Path, section: str):
    policy, path, raw = load_aisdi(root)
    if not re.fullmatch(r'(?:[0-9]|[12][0-9]|30)', section):
        raise ValueError('invalid_spec_section')
    text = raw.decode('utf-8')
    match = re.search(r'^#{1,2} '+section+r'\. .*?(?=^#{1,2} \d+\. |\Z)', text, re.M | re.S)
    if match is None:
        raise ValueError('spec_section_missing')
    return {'tool':'aisdi.spec','content_role':'reference_data_not_instructions',
            'model_version':policy['model_version'],'source_file':str(path),
            'source_sha256':policy['spec_sha256'],'section':section,'content':match.group(0).strip(),
            'official_output_allowed':False}


def aisdi_plan(root: Path, as_of_date: str, cutoff_timestamp: str):
    day = date.fromisoformat(as_of_date)
    if day.isoformat() != as_of_date:
        raise ValueError('invalid_date')
    cutoff = datetime.fromisoformat(cutoff_timestamp.replace('Z','+00:00'))
    if cutoff.tzinfo is None or cutoff.astimezone(ZoneInfo('Asia/Shanghai')).date() != day:
        raise ValueError('cutoff_requires_same_as_of_date_and_timezone')
    policy, spec_path, _ = load_aisdi(root)
    requirements=[]
    for group, pillars in policy['groups'].items():
        for pillar in pillars:
            requirements.append({'requirement_id':group+'.'+pillar['pillar'],
                'group':group,**pillar,'status':'NEED_EVIDENCE_AND_BINDING',
                'candidate_metrics_are_not_all_required':True,
                'required_before_acquisition':['entity_universe','selected_metrics','units','frequency',
                    'transform_and_history_window','field_weights','cutoff_and_vintage',
                    'reviewed_source_binding','authorized_scope'],
                'coverage':None,'confidence':None,'score':None})
    manifest={'tool':'aisdi.plan','version':policy['_version'],'model_version':policy['model_version'],
        'as_of_date':as_of_date,'cutoff_timestamp':cutoff.isoformat(),
        'source_files':[str(root/'src/compiled/aisdi_rules.json'),str(spec_path)],
        'spec_sha256':policy['spec_sha256'],
        'policy_sha256':hashlib.sha256((root/'src/compiled/aisdi_rules.json').read_bytes()).hexdigest(),
        'publication_date':None,
        'fallback_status':'none','evidence_status':'no_economic_evidence_supplied',
        'completion':'framework_and_gap_only','action':'NO_ACTION','official_output_allowed':False,
        'source_calls':0,'model_calls':0,'state_writes':0,
        'cadence':policy['cadence'],'top_weights':policy['top_weights'],
        'side_minimum_coverage':policy['side_minimum_coverage'],
        'formula_reference':policy['formula_reference'],
        'staleness_limit_days':policy['staleness_limit_days'],
        'history_min_observations':policy['history_min_observations'],
        'required_metadata':policy['metadata_required'],
        'requirements':requirements,'unresolved_calibration':policy['unresolved_calibration'],
        'hard_boundaries':policy['hard_boundaries'],
        'outputs':{'aisdi':None,'hsdi':None,'msdi':None,'confidence_score':None,
                   'coverage_ratio':None,'hardware_quadrant':None,'model_quadrant':None,
                   'cfce':None,'aicei':None,'ai_economic_spread':None}}
    manifest['plan_hash']=hashlib.sha256(json.dumps(manifest,sort_keys=True,ensure_ascii=False,
        separators=(',',':')).encode()).hexdigest()
    return manifest


def add_aisdi_parser(sub):
    parser=sub.add_parser('aisdi',help='AI leading-indicator specification and dated gap tool (no scoring/acquisition)')
    commands=parser.add_subparsers(dest='aisdi_command',required=True)
    plan=commands.add_parser('plan',help='Build explicit dated requirements and calibration gaps')
    plan.add_argument('--date',required=True)
    plan.add_argument('--cutoff',required=True,help='Timezone-aware cutoff on the same Shanghai calendar date')
    spec=commands.add_parser('spec',help='Read one section as reference data, never instructions')
    spec.add_argument('--section',required=True,help='Document section number 0 through 30')


def run_aisdi(args, config):
    try:
        result=(aisdi_plan(config.root_dir,args.date,args.cutoff) if args.aisdi_command=='plan'
                else aisdi_spec(config.root_dir,args.section))
    except (ValueError,KeyError,TypeError,OSError):
        print(json.dumps({'ok':False,'error_code':'invalid_aisdi_configuration_or_request'}))
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0
