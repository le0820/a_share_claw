"""Selection after implementation; fixtures never certify actual provider availability."""
import asyncio
import copy
import json

import pytest

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.providers import FRED,SEC
from test_data_plugins import FakeTransport,requirement,execute


class FredTransport:
    def __init__(self):self.calls=[]
    def get(self,url,params=None,headers=None):
        self.calls.append((url,params))
        value={"seriess":[{"id":"DGS10","realtime_start":"2026-08-01","realtime_end":"2026-08-01",
            "title":"10-Year Treasury","units":"Percent","frequency":"Daily","seasonal_adjustment":"Not Seasonally Adjusted","last_updated":"2026-07-31 16:00:00-05"}]} if url.endswith("/series") else {
            "count":2,"observations":[{"date":"2026-07-01","value":"4.2","realtime_start":"2026-08-01","realtime_end":"2026-08-01"},
                {"date":"2026-07-02","value":".","realtime_start":"2026-08-01","realtime_end":"2026-08-01"}]}
        return json.dumps(value).encode()


@pytest.fixture
def fred_run(tmp_path):
    transport=FredTransport();provider=FRED({"FRED_API_KEY":"synthetic-only"},transport)
    run=DataRun({"fred":provider},tmp_path,"fixed-scope")
    run.plan({"framework":"Fixed native rate requirements before acquisition","requirements":[
        requirement("fred","macro.series",{"series_id":"DGS10","start_date":"2026-07-01"},rid="observations").json(),
        requirement("fred","macro.series_metadata",{"series_id":"DGS10"},rid="metadata").json()]})
    for key in run.requirements:asyncio.run(run.fetch(key))
    return run,transport


def selector():
    return {"series_id":"DGS10","observation_date":"2026-07-01","units":"Percent","frequency":"Daily","seasonal_adjustment":"Not Seasonally Adjusted"}


def test_fred_exact_observation_and_metadata_are_bound_without_invented_publication(fred_run):
    run,transport=fred_run
    result=run.select("observations",selector(),metadata_requirement_id="metadata")
    assert result["observation"]["value"]==4.2 and result["observation"]["unit"]=="Percent"
    assert result["observation"]["available_at"] is None and result["observation"]["availability_precision"]=="date_level_vintage_only"
    assert not result["official_output_allowed"] and not result["core_admission_complete"]
    assert run.select("observations",selector(),metadata_requirement_id="metadata")==result and len(transport.calls)==2
    assert transport.calls[1][1]["realtime_start"]==transport.calls[1][1]["realtime_end"]=="2026-08-01"
    assert "synthetic-only" not in json.dumps(result)


@pytest.mark.parametrize("changes,code",[({"units":"Basis Points"},"unit_or_metric_mismatch"),
    ({"observation_date":"2026-07-03"},"insufficient_coverage"),({"observation_date":"2026-07-02"},"missing_observation")])
def test_fred_selection_refuses_unit_relabel_missing_date_and_null_fallback(fred_run,changes,code):
    run,_=fred_run;selection={**selector(),**changes}
    with pytest.raises(DataError) as error:run.select("observations",selection,metadata_requirement_id="metadata")
    assert error.value.code==code and not list(run.directory.glob("selection-*.json"))


@pytest.mark.parametrize("target",["raw","result"])
def test_selection_rejects_tampered_raw_or_result_independently(fred_run,target):
    run,_=fred_run
    if target=="raw":(run.directory/run.results["observations"]["provenance"]["artifact"]).write_bytes(b"changed")
    else:
        path=run.directory/"results/observations.json";value=json.loads(path.read_text());value["data"]["observations"][0]["value"]=99;path.write_text(json.dumps(value))
    with pytest.raises(DataError) as error:run.select("observations",selector(),metadata_requirement_id="metadata")
    assert error.value.code=="hash_mismatch"


def test_fred_metadata_missing_credentials_makes_zero_calls(tmp_path):
    transport=FakeTransport({});_,result=execute(FRED({},transport),requirement("fred","macro.series_metadata",{"series_id":"DGS10"}),tmp_path)
    assert result["error_code"]=="not_configured" and not transport.calls


def test_sec_preserves_exact_ytd_duration_and_accession_no_quarterly_substitution(tmp_path):
    original={"start":"2026-01-01","end":"2026-06-30","filed":"2026-07-20","val":30,"form":"10-Q","accn":"original"}
    transport=FakeTransport({"cik":320193,"facts":{"us-gaap":{"Revenues":{"units":{"USD":[original,{**original,"val":31,"accn":"amended"}]}}}}})
    run,_=execute(SEC({"SEC_USER_AGENT":"fixture contact@example.com"},transport),requirement("sec","company.facts",{"cik":"320193","concepts":["us-gaap:Revenues"]}),tmp_path)
    selection={"cik":"0000320193","concept":"us-gaap:Revenues","unit":"USD","period_start":"2026-01-01","period_end":"2026-06-30","filed":"2026-07-20","accession":"original"}
    result=run.select("input",selection)
    assert result["observation"]["value"]==30 and result["observation"]["period_start"]=="2026-01-01"
    assert result["observation"]["accession"]=="original" and result["observation"]["available_at"] is None
    assert not result["core_admission_complete"] and not result["official_output_allowed"]
    for change in ({"period_start":"2026-04-01"},{"unit":"USD millions"},{"accession":"missing"}):
        with pytest.raises(DataError) as error:run.select("input",{**selection,**change})
        assert error.value.code=="insufficient_coverage"


def test_normalizer_cannot_promote_unverified_source_or_mix_vintages(fred_run):
    from a_share_claw.data_plugins.normalization import fred_observation
    run,_=fred_run;data=copy.deepcopy(run.results["observations"]);metadata=copy.deepcopy(run.results["metadata"])
    data["status"]="unverified";data["fallback_status"]="unverified"
    with pytest.raises(DataError) as error:fred_observation(data,metadata,**selector())
    assert error.value.code=="unverified_evidence"
    data=run.results["observations"];metadata["data"]["vintage_date"]="2026-07-31"
    with pytest.raises(DataError) as error:fred_observation(data,metadata,**selector())
    assert error.value.code=="source_mismatch"
