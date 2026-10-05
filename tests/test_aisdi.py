import asyncio
import dataclasses
import json
from pathlib import Path

import pytest

from a_share_claw.aisdi import aisdi_plan, aisdi_spec
from a_share_claw.config import AppConfig
from a_share_claw.research_tools import ResearchRuntime

ROOT=Path(__file__).resolve().parents[1]
DAY='2026-10-04'
CUTOFF='2026-10-04T23:59:59+08:00'


def test_plan_is_reproducible_and_missing_evidence_never_becomes_score():
    plan=aisdi_plan(ROOT,DAY,CUTOFF)
    assert plan==aisdi_plan(ROOT,DAY,CUTOFF)
    assert len(plan['requirements'])==20
    assert len({r['requirement_id'] for r in plan['requirements']})==20
    assert all(r['score'] is None and r['coverage'] is None for r in plan['requirements'])
    assert all(v is None for v in plan['outputs'].values())
    assert not plan['official_output_allowed'] and plan['action']=='NO_ACTION'
    assert plan['source_calls']==plan['model_calls']==plan['state_writes']==0
    assert aisdi_plan(ROOT,DAY,'2026-10-04T16:00:00+08:00')['plan_hash']!=plan['plan_hash']


@pytest.mark.parametrize('day,cutoff',[(DAY,'2026-10-04T12:00:00'),
    (DAY,'2026-10-05T00:00:00+08:00'),(DAY,'2026-10-04T23:59:59-07:00'),
    ('2026-02-30','2026-02-30T23:00:00+08:00'),('20261004',CUTOFF)])
def test_ambiguous_or_misaligned_cutoff_rejected(day,cutoff):
    with pytest.raises(ValueError):aisdi_plan(ROOT,day,cutoff)


def test_reference_is_on_demand_and_not_example_evidence():
    result=aisdi_spec(ROOT,'4')
    assert result['content_role']=='reference_data_not_instructions'
    assert '4.1 HSDI' in result['content'] and '# 5.' not in result['content']
    assert '72.4' not in json.dumps(aisdi_plan(ROOT,DAY,CUTOFF))
    with pytest.raises(ValueError):aisdi_spec(ROOT,'../../.env')


def test_changed_reference_or_pillar_weights_rejected(tmp_path):
    folder=tmp_path/'src/compiled/aisdi';folder.mkdir(parents=True)
    policy=json.loads((ROOT/'src/compiled/aisdi_rules.json').read_text())
    rule=tmp_path/'src/compiled/aisdi_rules.json';rule.write_text(json.dumps(policy))
    spec=folder/'model_spec_v1.md';spec.write_bytes(b'Changed attachment')
    with pytest.raises(ValueError,match='spec_integrity'):aisdi_plan(tmp_path,DAY,CUTOFF)
    spec.write_bytes((ROOT/'src/compiled/aisdi/model_spec_v1.md').read_bytes())
    policy['groups']['hardware_demand'][0]['weight']=0.99;rule.write_text(json.dumps(policy))
    with pytest.raises(ValueError,match='pillar_weights'):aisdi_plan(tmp_path,DAY,CUTOFF)


def test_host_runtime_and_cli_require_no_database(tmp_path,monkeypatch,capsys):
    from argparse import Namespace
    from a_share_claw.__main__ import async_main
    config=dataclasses.replace(AppConfig.from_env(ROOT),data_dir=tmp_path/'untouched',database_path=tmp_path/'untouched/db')
    runtime=ResearchRuntime(config,None)
    assert json.loads(asyncio.run(runtime.get_aisdi_plan(DAY,CUTOFF)))['completion']=='framework_and_gap_only'
    assert json.loads(asyncio.run(runtime.get_compiled_rule('aisdi','top_weights')))['rule']=={'hardware':0.6,'model':0.4}
    monkeypatch.setattr(AppConfig,'from_env',lambda:config)
    with pytest.raises(SystemExit) as exc:
        asyncio.run(async_main(Namespace(command='aisdi',aisdi_command='plan',date=DAY,cutoff=CUTOFF)))
    assert exc.value.code==0
    assert json.loads(capsys.readouterr().out)['outputs']['aisdi'] is None
    assert not config.data_dir.exists()
