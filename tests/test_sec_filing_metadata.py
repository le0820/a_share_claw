"""Implementation-following SEC identity checks; no real source or model calls."""
import asyncio
import copy
import json

import pytest

from a_share_claw.data_plugins import DataRun
from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.providers import SEC
from test_data_plugins import FakeTransport, requirement, execute

ACCESSION='0000950170-26-000123'  # Filing-agent prefix need not equal the company CIK.


def submissions():
    return {'cik':'320193','filings':{'recent':{
        'accessionNumber':[ACCESSION,'0000950170-26-000124'],
        'filingDate':['2026-07-20','2026-08-05'], 'reportDate':['2026-06-30','2026-06-30'],
        'acceptanceDateTime':['2026-07-20T20:00:00.000Z','2026-08-05T20:00:00.000Z'],
        'form':['10-Q','10-Q/A'], 'primaryDocument':['example-20260630.htm','amended-20260630.htm']},
        'files':[{'name':'CIK0000320193-submissions-001.json','filingFrom':'2000-01-01','filingTo':'2020-01-01'}]}}


def company_facts():
    return {'cik':320193,'facts':{'us-gaap':{'Revenues':{'units':{'USD':[
        {'start':'2026-01-01','end':'2026-06-30','filed':'2026-07-20','val':30,'form':'10-Q','accn':ACCESSION},
        {'start':'2026-01-01','end':'2026-03-31','filed':'2026-07-20','val':12,'form':'10-Q','accn':ACCESSION}
    ]}}}}}


class Transport:
    def __init__(self,metadata=None):self.calls=[];self.metadata=metadata or submissions()
    def get(self,url,params=None,headers=None):
        self.calls.append((url,headers))
        return json.dumps(self.metadata if '/submissions/' in url else company_facts()).encode()


def planned_run(tmp_path,metadata=None):
    transport=Transport(metadata);provider=SEC({'SEC_USER_AGENT':'offline fixture contact@example.com'},transport)
    run=DataRun({'sec':provider},tmp_path,'fixed-company-scope')
    run.plan({'framework':'Freeze exact company, duration and accession before acquiring facts','requirements':[
        requirement('sec','company.facts',{'cik':'320193','concepts':['us-gaap:Revenues']},rid='facts').json(),
        requirement('sec','company.filing_metadata',{'cik':'320193','accession':ACCESSION},rid='filing').json()]})
    for key in run.requirements:asyncio.run(run.fetch(key))
    return run,transport


def selector():
    return {'cik':'0000320193','concept':'us-gaap:Revenues','unit':'USD','period_start':'2026-01-01',
        'period_end':'2026-06-30','filed':'2026-07-20','accession':ACCESSION}


def test_exact_filing_metadata_binds_same_run_values_without_claiming_public_availability(tmp_path):
    run,transport=planned_run(tmp_path)
    result=run.select('facts',selector(),metadata_requirement_id='filing')
    obs=result['observation']
    assert obs['value']==30 and obs['unit']=='USD' and obs['period_start']=='2026-01-01'
    assert obs['acceptance_timestamp_raw']=='2026-07-20T20:00:00.000Z'
    assert obs['acceptance_timestamp_declared']=='2026-07-20T20:00:00+00:00'
    assert obs['available_at'] is None and obs['public_dissemination_certified'] is False
    assert not result['core_admission_complete'] and not result['official_output_allowed']
    assert len(result['provenance'])==len(result['input_hashes'])==2 and len(transport.calls)==2
    assert transport.calls[1][0]=='https://data.sec.gov/submissions/CIK0000320193.json'
    assert 'contact@example.com' not in json.dumps(result)
    assert run.select('facts',selector(),metadata_requirement_id='filing')==result and len(transport.calls)==2
    # Comparative facts from that exact filing stay valid; the report's period is not their period.
    old=run.select('facts',{**selector(),'period_end':'2026-03-31'},metadata_requirement_id='filing')
    assert old['observation']['value']==12 and old['observation']['report_date']=='2026-06-30'


def test_filing_metadata_without_contact_performs_no_network(tmp_path):
    transport=FakeTransport({})
    _,result=execute(SEC({},transport),requirement('sec','company.filing_metadata',{'cik':'320193','accession':ACCESSION}),tmp_path)
    assert result['error_code']=='not_configured' and not transport.calls


@pytest.mark.parametrize('kind,code',[
    ('company','source_mismatch'), ('missing','insufficient_coverage'), ('duplicate','insufficient_coverage'),
    ('columns','invalid_schema'), ('naive_time','invalid_schema'), ('empty_time','invalid_schema'),
    ('future_time','future_data'), ('future_filing','future_data'), ('report_after_filing','future_data'),
    ('unsupported_form','unsupported_filing_form'), ('document_path','invalid_request')])
def test_metadata_shape_identity_and_date_gaps_do_not_fetch_another_file(tmp_path,kind,code):
    obj=submissions();recent=obj['filings']['recent']
    if kind=='company':obj['cik']='123'
    elif kind=='missing':recent['accessionNumber'][0]='0000950170-26-999999'
    elif kind=='duplicate':recent['accessionNumber'][1]=ACCESSION
    elif kind=='columns':recent['form'].pop()
    elif kind=='naive_time':recent['acceptanceDateTime'][0]='2026-07-20T20:00:00'
    elif kind=='empty_time':recent['acceptanceDateTime'][0]=''
    elif kind=='future_time':recent['acceptanceDateTime'][0]='2026-08-02T20:00:00Z'
    elif kind=='future_filing':recent['filingDate'][0]='2026-08-02'
    elif kind=='report_after_filing':recent['reportDate'][0]='2026-07-21'
    elif kind=='unsupported_form':recent['form'][0]='8-K'
    elif kind=='document_path':recent['primaryDocument'][0]='../secret.htm'
    transport=FakeTransport(obj)
    run,result=execute(SEC({'SEC_USER_AGENT':'fixture contact@example.com'},transport),
        requirement('sec','company.filing_metadata',{'cik':'320193','accession':ACCESSION}),tmp_path)
    assert result['error_code']==code and len(transport.calls)==1 and not run.summary()['required_data_complete']


@pytest.mark.parametrize('change',[{'form':'10-K'},{'filingDate':'2026-07-21'},{'reportDate':'2026-03-31'}])
def test_fact_join_rejects_mismatched_filing_form_date_and_report_period(tmp_path,change):
    metadata=submissions()
    for key,value in change.items():metadata['filings']['recent'][key][0]=value
    run,_=planned_run(tmp_path,metadata)
    with pytest.raises(DataError) as error:run.select('facts',selector(),metadata_requirement_id='filing')
    assert error.value.code=='source_mismatch' and not list(run.directory.glob('selection-*.json'))


@pytest.mark.parametrize('target',['raw','result'])
def test_metadata_archives_cannot_be_changed_after_planned_acquisition(tmp_path,target):
    run,_=planned_run(tmp_path)
    if target=='raw':(run.directory/run.results['filing']['provenance']['artifact']).write_bytes(b'changed')
    else:(run.directory/'results/filing.json').write_text('{}')
    with pytest.raises(DataError) as error:run.select('facts',selector(),metadata_requirement_id='filing')
    assert error.value.code=='hash_mismatch'


def test_pure_join_cannot_turn_acceptance_metadata_into_public_availability(tmp_path):
    from a_share_claw.data_plugins.normalization import sec_fact
    run,_=planned_run(tmp_path);metadata=copy.deepcopy(run.results['filing'])
    metadata['data']['available_at']='2026-07-20T20:00:00Z';metadata['data']['public_dissemination_certified']=True
    with pytest.raises(DataError) as error:sec_fact(run.results['facts'],metadata=metadata,**selector())
    assert error.value.code=='unverified_evidence'
