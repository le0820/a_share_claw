"""Native discovery cannot substitute missing classification, date or universe evidence."""
import json,copy
import pytest
from a_share_claw.data_plugins.tdx import EasyTDX,checked_params
from a_share_claw.data_plugins.core import Requirement,DataError
from test_source_core_handoff import clock,Clock,DAY


@pytest.fixture(autouse=True)
def current_clock(clock,monkeypatch):monkeypatch.setattr('a_share_claw.data_plugins.tdx.datetime',Clock)


def req(cap,params):return Requirement.parse({'requirement_id':'discovery','provider':'easytdx','capability':cap,'as_of_date':DAY,'params':params,'required':True})


class Transport:
    def __init__(self):
        self.obj={'sdk_version':'1.20.4','endpoint':'tcp://121.36.248.138:7709','market_totals':{'SH':1,'SZ':1,'BJ':1},'page_counts':{'SH':1,'SZ':1,'BJ':1},
            'quotes':[{'requested_market':label,'market':market,'code':code,'name':'Synthetic','fields':{'amount':100,'main_net_amount':-20,'server_update_date':20260713,'server_update_time':150000}} for label,market,code in [('SH',1,'600000'),('SZ',0,'000001'),('BJ',2,'920001')]]}
    def request(self,p):return json.dumps(self.obj).encode()


def test_complete_three_exchange_discovery_retains_negative_native_amount_but_not_core_admission():
    t=Transport();out=EasyTDX({},t).fetch(req('market.a_share_quote_snapshot',{'markets':['SH','SZ','BJ'],'max_rows':10000}))
    assert out.availability=='unverified' and out.data['coverage_status']=='complete_against_native_totals'
    assert out.data['quotes'][0]['fields']['main_net_amount']==-20
    assert not out.data['historical_vintage_certified'] and 'not total investor cash' in out.warnings[1]
    del t.obj['quotes'][0]['fields']['main_net_amount']
    out=EasyTDX({},t).fetch(req('market.a_share_quote_snapshot',{'markets':['SH','SZ','BJ'],'max_rows':10000}))
    assert out.data['missing_fields']==[{'market':'SH','code':'600000','field':'main_net_amount'}]
    assert 'main_net_amount' not in out.data['quotes'][0]['fields']


@pytest.mark.parametrize('change',['missing_bj','duplicate','wrong_market','infinite'])
def test_native_discovery_rejects_false_whole_market_coverage(change):
    t=Transport()
    if change=='missing_bj':t.obj['quotes'].pop()
    elif change=='duplicate':t.obj['quotes'].append(copy.deepcopy(t.obj['quotes'][0]))
    elif change=='wrong_market':t.obj['quotes'][-1]['market']=0
    else:t.obj['quotes'][0]['fields']['main_net_amount']=float('inf')
    with pytest.raises(DataError):EasyTDX({},t).fetch(req('market.a_share_quote_snapshot',{'markets':['SH','SZ','BJ'],'max_rows':10000}))


def test_discovery_never_labels_generic_tdx_industry_as_sw2021():
    with pytest.raises(DataError):checked_params(req('market.board_catalog',{'classification':'sw2021_level1'}))
    with pytest.raises(DataError):checked_params(req('market.a_share_quote_snapshot',{'markets':['SH','SZ'],'max_rows':80}))
    t=Transport();t.obj={'sdk_version':'1.20.4','endpoint':'tcp://121.36.248.138:7709','identity':[{'market':1,'code':'881001','name':'TDX generic sector'}],'bars':[]}
    out=EasyTDX({},t).fetch(req('market.board_catalog',{'classification':'native_industry_level1'}))
    assert out.availability=='unverified' and not out.data['historical_vintage_certified']
