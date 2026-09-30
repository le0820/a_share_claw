"""Prose mapping checks after implementation; synthetic releases are not live acceptance."""
import copy

import pytest

from a_share_claw.data_plugins.core import DataError
from a_share_claw.data_plugins.macro_mapping import publication_observation
from a_share_claw.data_plugins.providers import NBS, PBC
from test_data_plugins import FakeTransport, execute, requirement

NBS_TEXT = """<h1>8月份国民经济运行情况</h1>
<p>8月份，全国规模以上工业增加值同比增长5.1%。全国服务业生产指数同比增长4.2%。</p>
<p>1—8月份，全国规模以上工业增加值同比增长5.6%。全国服务业生产指数同比增长5.0%。</p>
<p>1—8月份，社会消费品零售总额300000亿元，同比增长1.2%。8月份，社会消费品零售总额39000亿元，同比增长0.5%。</p>
<p>1—8月份，全国固定资产投资（不含农户）290000亿元，同比下降7.3%。</p>
<p>8月份，全国城镇调查失业率为5.4%。1—8月份，全国城镇调查失业率平均值为5.2%。</p>
<p>8月份，全国居民消费价格（CPI）同比上涨0.9%。扣除食品和能源价格后的核心CPI同比上涨1.1%。全国工业生产者出厂价格同比下跌3.9%。</p>
<p>2026年1—8月份主要指标数据</p>"""
PBC_TEXT = """<h1>2026年8月金融统计数据报告</h1>
<p>初步统计，2026年8月末社会融资规模存量为460万亿元，同比增长7.1%。</p>
<p>初步统计，2026年前八个月社会融资规模增量累计为23万亿元。</p>
<p>8月末，广义货币（M2）余额350万亿元，同比增长7.6%。狭义货币（M1）余额110万亿元，同比增长4.2%。</p>
<p>注：M1统计口径于2025年修订。上述数据为初步数据。</p>
<p>2024年8月末，狭义货币（M1）余额90万亿元，同比下降3.1%。</p>"""


def run_release(tmp_path, text=NBS_TEXT, provider=NBS, meta='2026/09/15 10:00'):
    clock = f'<meta name="PubDate" content="{meta}">' if meta is not None else ''
    raw = ('<html>' + clock + text + '</html>').encode()
    url = f'https://{provider.manifest.hosts[0]}/release.html'
    return execute(provider({}, FakeTransport(raw)), requirement(provider.manifest.plugin_id, 'macro.release', {'url':url}, day='2026-09-30'), tmp_path)


def choose(metric, kind='month', **changes):
    return dict(metric=metric, year=2026, month=8, period_kind=kind, **changes)


def test_nbs_exact_month_ytd_signed_native_percent_and_immutable_archive(tmp_path):
    run, result = run_release(tmp_path)
    cases = [('industrial_value_added_yoy','month',5.1), ('industrial_value_added_yoy','year_to_date',5.6),
        ('services_production_yoy','month',4.2), ('retail_sales_yoy','month',0.5),
        ('retail_sales_yoy','year_to_date',1.2), ('fixed_asset_investment_yoy','year_to_date',-7.3),
        ('urban_surveyed_unemployment','month',5.4), ('cpi_yoy','month',0.9),
        ('core_cpi_yoy','month',1.1), ('ppi_yoy','month',-3.9)]
    for metric,kind,value in cases:
        selection = run.select('input',choose(metric,kind))
        obs = selection['observation']
        assert obs['value']==value and obs['unit']=='percent' and obs['period_end']=='2026-08-31'
        assert obs['period_start']==('2026-01-01' if kind=='year_to_date' else '2026-08-01')
        assert obs['available_at']=='2026-09-15T10:00:00+08:00' and obs['publication_time_precision']=='minute'
        assert obs['eligibility']=='unverified' and not selection['core_admission_complete'] and not selection['official_output_allowed']
        assert run.select('input',choose(metric,kind))==selection
    assert result['status']=='unverified' and not run.summary()['required_data_complete']
    assert len(list(run.directory.glob('selection-*.json')))==len(cases)


def test_pbc_stock_and_m1_definition_remain_bound_without_old_year_or_monthly_inference(tmp_path):
    run,_ = run_release(tmp_path,PBC_TEXT,PBC,meta=None)
    # Visible labelled timestamp, rather than URL dates or a fabricated midnight.
    # No date means no selection; a date-only native meta remains acceptable as unverified evidence.
    with pytest.raises(DataError) as error:run.select('input',choose('m2_yoy'))
    assert error.value.code=='missing_publication_date'
    run,_ = run_release(tmp_path/'dated',PBC_TEXT,PBC,meta='2026-09-14')
    for metric,value in [('m2_yoy',7.6),('m1_yoy',4.2),('tsf_stock_yoy',7.1)]:
        obs=run.select('input',choose(metric))['observation']
        assert obs['value']==value and obs['available_at'] is None and obs['publication_time_precision']=='day'
        assert any('口径' in note for note in obs['source_notes'])
    for metric,kind,code in [('tsf_increment','month','mapping_unavailable'),('m2_yoy','year_to_date','period_mismatch')]:
        with pytest.raises(DataError) as error:run.select('input',choose(metric,kind))
        assert error.value.code==code


@pytest.mark.parametrize('text,code',[
    (NBS_TEXT.replace('2026年1—8月份主要指标数据','2025年1—8月份主要指标数据'),'period_mismatch'),
    (NBS_TEXT.replace('2026年1—8月份主要指标数据','2026年9月份主要指标数据'),'period_mismatch'),
    (NBS_TEXT+'<p>8月份，全国规模以上工业增加值同比增长8.1%。</p>','source_disagreement'),
    (NBS_TEXT.replace('全国规模以上工业增加值同比增长5.1%','某地区规模以上工业增加值同比增长5.1%'),'insufficient_coverage'),
    (NBS_TEXT.replace('同比增长5.1%','同比增长5.1亿元'),'insufficient_coverage'),
])
def test_nbs_rejects_wrong_report_period_ambiguous_values_and_other_regions_or_units(tmp_path,text,code):
    run,_=run_release(tmp_path,text)
    with pytest.raises(DataError) as error:run.select('input',choose('industrial_value_added_yoy'))
    assert error.value.code==code and not list(run.directory.glob('selection-*.json'))


@pytest.mark.parametrize('changes,code',[
    ({'year':0},'invalid_request'), ({'month':True},'invalid_request'), ({'metric':[]},'invalid_request'),
    ({'period_kind':[]},'invalid_request'), ({'period_kind':'year_to_date'},'period_mismatch'),
])
def test_prose_typed_selector_and_stock_period_never_coerce(tmp_path,changes,code):
    run,_=run_release(tmp_path)
    with pytest.raises(DataError) as error:run.select('input',{**choose('urban_surveyed_unemployment'),**changes})
    assert error.value.code==code


@pytest.mark.parametrize('meta,expected,precision',[
    ('2026/09/15 10:00','2026-09-15T10:00:00+08:00','minute'),
    ('2026-09-15T02:00:12Z','2026-09-15T10:00:12+08:00','second'),
    ('2026-09-15',None,'day'),
])
def test_publisher_clock_keeps_native_precision_and_offset(tmp_path,meta,expected,precision):
    _,result=run_release(tmp_path,meta=meta)
    assert result['data']['available_at']==expected and result['data']['publication_time_precision']==precision


def test_visible_pbc_clock_parses_split_spans_but_not_url_or_observation_date(tmp_path):
    text='<p>文章来源：<span>2026-09-14</span> <span>17:00:00</span></p>'+PBC_TEXT
    run,result=run_release(tmp_path,text,PBC,meta=None)
    assert result['data']['available_at']=='2026-09-14T17:00:00+08:00'
    assert run.select('input',choose('m1_yoy'))['observation']['value']==4.2


@pytest.mark.parametrize('target',['result','raw'])
def test_unverified_numeric_selection_rejects_archived_tamper(tmp_path,target):
    run,result=run_release(tmp_path)
    if target=='raw':(run.directory/result['provenance']['artifact']).write_bytes(b'modified')
    else:(run.directory/'results/input.json').write_text('{}')
    with pytest.raises(DataError) as error:run.select('input',choose('cpi_yoy'))
    assert error.value.code=='hash_mismatch'


def test_prose_mapping_cannot_promote_eligibility_or_use_future_timestamp(tmp_path):
    _,result=run_release(tmp_path)
    changed=copy.deepcopy(result);changed['status']='ok';changed['fallback_status']='none';changed['provenance']['availability']='verified'
    with pytest.raises(DataError) as error:publication_observation(changed,**choose('cpi_yoy'))
    assert error.value.code=='unverified_evidence'
    changed=copy.deepcopy(result);changed['data']['available_at']='2026-09-15T10:00:00+08:00';changed['provenance']['retrieved_at']='2026-09-15T09:59:00+08:00'
    with pytest.raises(DataError) as error:publication_observation(changed,**choose('cpi_yoy'))
    assert error.value.code=='future_data'


@pytest.mark.parametrize('metric,national,regional',[
    ('retail_sales_yoy','社会消费品零售总额39000亿元','某省社会消费品零售总额39000亿元'),
    ('ppi_yoy','全国工业生产者出厂价格','某省工业生产者出厂价格'),
])
def test_unqualified_metric_patterns_do_not_extract_nested_regional_subjects(tmp_path,metric,national,regional):
    run,_=run_release(tmp_path,NBS_TEXT.replace(national,regional))
    with pytest.raises(DataError) as error:run.select('input',choose(metric))
    assert error.value.code=='insufficient_coverage'


def test_standalone_old_year_header_does_not_inherit_current_report_year(tmp_path):
    text=PBC_TEXT.replace('狭义货币（M1）余额110万亿元，同比增长4.2%。','')
    text=text.replace('2024年8月末，','2024年历史对照</p><p>8月末，')
    run,_=run_release(tmp_path,text,PBC,meta='2026-09-14')
    with pytest.raises(DataError) as error:run.select('input',choose('m1_yoy'))
    assert error.value.code=='insufficient_coverage'


def test_identical_duplicate_source_expression_keeps_all_evidence_without_conflict(tmp_path):
    run,_=run_release(tmp_path,NBS_TEXT+'<p>8月份，全国规模以上工业增加值同比增长5.1%。</p>')
    assert run.select('input',choose('industrial_value_added_yoy'))['observation']['value']==5.1


def test_conflicting_native_timezone_date_is_not_silently_reinterpreted(tmp_path):
    _,result=run_release(tmp_path,meta='2026-09-15T23:00:00Z')
    assert not result['ok'] and result['error_code']=='invalid_schema'
