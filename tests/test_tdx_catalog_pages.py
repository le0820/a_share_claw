"""Native global pagination must prove completeness before catalog discovery."""
import copy,json
import pytest
from a_share_claw.data_plugins._tdx_worker import scan_extended_catalog
from a_share_claw.data_plugins.tdx import EasyTDX
from a_share_claw.data_plugins.core import DataError
from test_tdx_market_discovery import req,Transport,current_clock
from test_source_core_handoff import clock


def rows(n):return [{'category':0,'market':62,'code':str(i),'name':'Synthetic','desc':'','raw_hex':'00'} for i in range(n)]


def test_full_catalog_pages_use_native_count_and_keep_last_short_page():
    items=rows(1501);calls=[]
    def page(start,count):calls.append((start,count));return items[start:start+count]
    data=scan_extended_catalog(lambda:1501,page)
    assert [{k:v for k,v in r.items() if k!='native_offset'} for r in data['rows']]==items and data['page_count']==2 and [(s,c) for s,c in calls if c>1]==[(0,1000),(1000,501)]


@pytest.mark.parametrize('failure',['short_page','wrong_market','changed_total','over_bound'])
def test_native_global_catalog_does_not_turn_partial_pages_into_complete_coverage(failure):
    items=rows(1001);totals=iter([1001,1002] if failure=='changed_total' else [1001,1001])
    def page(start,count):
        batch=copy.deepcopy(items[start:start+count])
        if failure=='short_page' and count>1:batch.pop()
        if failure=='wrong_market' and count>1:batch[0]['market']=63
        return batch
    with pytest.raises(ValueError):scan_extended_catalog(lambda:250001 if failure=='over_bound' else next(totals),page)


def test_more_than_600_discovered_indices_require_retained_complete_directory(current_clock):
    t=Transport();catalog={'rows':[{**r,'native_offset':i} for i,r in enumerate(rows(601))],'native_total':601,'market_start':0,'market_end':601,'market_total':601,'page_count':1,'boundary_probes':[{'offset':0,'row':rows(601)[0]},{'offset':600,'row':rows(601)[-1]}],'coverage_status':'complete_against_native_market_boundaries'}
    t.obj={'sdk_version':'1.20.4','endpoint':'tcp://116.205.135.205:7727','identity':[{k:v for k,v in r.items() if k!='raw_hex'} for r in catalog['rows']],'bars':[],'native_catalog':catalog}
    out=EasyTDX({},t).fetch(req('market.extended_index_catalog',{'market':62}))
    assert len(out.data['instruments'])==601 and out.availability=='unverified'
    t.obj['native_catalog']['rows'].pop()
    with pytest.raises(DataError,match='Extended catalog'):EasyTDX({},t).fetch(req('market.extended_index_catalog',{'market':62}))


def test_native_market_interval_retains_duplicate_identities_for_discovery_without_deduplicating():
    before=[{**r,'market':0} for r in rows(20)];selected=rows(1001);selected[-1]=copy.deepcopy(selected[0]);after=[{**r,'market':70} for r in rows(7)]
    items=before+selected+after
    data=scan_extended_catalog(lambda:len(items),lambda start,count:copy.deepcopy(items[start:start+count]),62)
    assert data['market_start']==20 and data['market_end']==1021 and len(data['rows'])==1001
    assert data['ambiguous_identities']==[{'market':62,'code':'0','records':2}]
    assert [r['native_offset'] for r in data['rows']]==list(range(20,1021))
