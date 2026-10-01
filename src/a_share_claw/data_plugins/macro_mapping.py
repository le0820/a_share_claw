"""Versioned official prose mappings; extraction never certifies page revision vintage."""
from __future__ import annotations

import calendar
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..harness.contracts import digest
from .core import DataError,iso_date
from .normalization import _selection,numeric

MAPPING_VERSION="official-macro-prose-v1.1"
NUMBER=r"(?P<value>-?\d+(?:\.\d+)?)"
CHANGE=r"(?P<direction>增长|下降|上涨|下跌)"+NUMBER+r"%"
MONEY=r"\d+(?:\.\d+)?(?:万亿元|亿元)"
NBS_PATTERNS={
    "industrial_value_added_yoy":r"全国规模以上工业增加值同比"+CHANGE,
    "services_production_yoy":r"全国服务业生产指数同比"+CHANGE,
    "retail_sales_yoy":r"社会消费品零售总额"+MONEY+r",同比"+CHANGE,
    "fixed_asset_investment_yoy":r"全国固定资产投资\(不含农户\)"+MONEY+r",同比"+CHANGE,
    "urban_surveyed_unemployment":r"全国城镇调查失业率为"+NUMBER+r"%",
    "cpi_yoy":r"全国居民消费价格(?:\(CPI\))?同比"+CHANGE,
    "core_cpi_yoy":r"(?:扣除食品和能源价格后的)?核心CPI同比"+CHANGE,
    "ppi_yoy":r"(?:全国)?工业生产者出厂价格同比"+CHANGE,
}
PBC_PATTERNS={
    "m2_yoy":r"广义货币\(M2\)余额"+MONEY+r",同比"+CHANGE,
    "m1_yoy":r"狭义货币\(M1\)余额"+MONEY+r",同比"+CHANGE,
    "tsf_stock_yoy":r"社会融资规模存量为"+MONEY+r",同比"+CHANGE,
}
MONTH_CN={1:"一",2:"二",3:"三",4:"四",5:"五",6:"六",7:"七",8:"八",9:"九",10:"十",11:"十一",12:"十二"}


def clean(text):
    return re.sub(r"\s+","",text).translate(str.maketrans({"（":"(","）":")","，":",","：":":","％":"%","－":"-","–":"—"}))


def period_segments(text, year, month, kind, provider):
    # Bind to a printed report title/table heading, not any old-year number in a footnote.
    heading = (r"(?P<year>\d{4})年(?:1[—-])?(?P<month>1[0-2]|[1-9])月份(?:主要指标数据|国民经济运行情况)"
               if provider == "nbs" else
               r"(?P<year>\d{4})年(?P<month>1[0-2]|[1-9])月金融统计数据报告")
    periods = {(int(m["year"]), int(m["month"])) for m in re.finditer(heading, text)}
    if periods != {(year, month)}:
        raise DataError("period_mismatch", "An unambiguous native report title/table heading must identify the requested year/month")
    markers = list(re.finditer(r"(?:^|[。\n])(?:其中,)?(?:初步统计,)?(?:(?P<year>\d{4})年)?(?:(?P<cumulative>1[—-])?(?P<month>1[0-2]|[1-9])月(?:份|末)?|前(?P<cn>十一|十二|十|[一二三四五六七八九])个月)", text))
    # A standalone prior-year header also changes scope; do not inherit the report year into it.
    year_headers = list(re.finditer(r"(?:^|[。\n])(?P<year>\d{4})年", text))
    chosen = []; current_year = year
    for i, marker in enumerate(markers):
        preceding = [header for header in year_headers if header.start() <= marker.start()]
        if preceding:
            current_year = int(preceding[-1]["year"])
        if marker["year"]:
            current_year = int(marker["year"])
        observed = int(marker["month"]) if marker["month"] else next(k for k, v in MONTH_CN.items() if v == marker["cn"])
        period_kind = "year_to_date" if marker["cumulative"] or marker["cn"] else "month"
        if current_year == year and observed == month and period_kind == kind:
            end = markers[i+1].start() if i+1 < len(markers) else len(text)
            following = [header.start() for header in year_headers if marker.start() < header.start() < end]
            if following:
                end = following[0]
            chosen.append(text[marker.start():end])
    if not chosen:
        raise DataError("period_mismatch", "Requested monthly/cumulative paragraph is absent")
    return chosen


def publication_observation(result,*,metric,year,month,period_kind):
    if (type(year) is not int or not 1<=year<=9999 or type(month) is not int or not 1<=month<=12
            or not isinstance(metric,str) or not isinstance(period_kind,str) or period_kind not in {"month","year_to_date"}):
        raise DataError("invalid_request","Use an explicit year, month and month/year_to_date period")
    provider=result.get("provenance",{}).get("provider")
    snapshot=result.get("capability")=="macro.release_snapshot"
    if (provider not in {"nbs","pbc"} or result.get("capability") not in {"macro.release","macro.release_snapshot"} or not result.get("ok") or
            result.get("truncated") or result.get("status") not in {"ok","unverified"} or not isinstance(result.get("data"),dict)):
        raise DataError("source_mismatch","Use an intact planned NBS/PBC publication, never a discovery index")
    expected="verified" if snapshot else "unverified"
    if (result.get("status")!=("ok" if snapshot else "unverified") or
            result.get("fallback_status")!=("none" if snapshot else "unverified") or
            result.get("provenance",{}).get("availability")!=expected):
        raise DataError("unverified_evidence","Select only the eligibility of this explicitly planned capability; no promotion of historical pages")
    data=result["data"]
    if not isinstance(data.get("text"),str) or not data["text"].strip():
        raise DataError("invalid_schema","Publication text is absent")
    try:
        published=iso_date(data.get("publication_date"))
        cutoff=iso_date(result["provenance"]["as_of_date"])
    except (ValueError,KeyError):
        raise DataError("missing_publication_date","Source needs an explicit publication date") from None
    end=f"{year:04d}-{month:02d}-{calendar.monthrange(year,month)[1]:02d}"
    start=f"{year:04d}-{'01' if period_kind=='year_to_date' else f'{month:02d}'}-01"
    if published>cutoff or end>published or end>cutoff:
        raise DataError("future_data","Observation period/publication lies after the allowed date")
    available=data.get("publisher_available_at") if snapshot else data.get("available_at")
    if available is not None:
        try:
            stamp=datetime.fromisoformat(available);capture=datetime.fromisoformat(result["provenance"]["retrieved_at"])
        except (TypeError,ValueError,KeyError):
            raise DataError("invalid_schema","Publisher/capture timestamp is malformed") from None
        if stamp.tzinfo is None or capture.tzinfo is None or stamp.date().isoformat()!=published or stamp>capture:
            raise DataError("future_data","Publisher timestamp is inconsistent or later than capture")
    capture=None
    if snapshot:
        try:
            capture=datetime.fromisoformat(result["provenance"]["retrieved_at"])
        except (ValueError,KeyError,TypeError):
            raise DataError("invalid_schema","Snapshot needs the archived capture clock") from None
        if (capture.tzinfo is None or capture.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()!=cutoff or
                data.get("snapshot_as_of_date")!=cutoff or data.get("availability_basis")!="observed_current_snapshot" or
                data.get("historical_vintage_certified") is not False):
            raise DataError("historical_unavailable","Current snapshot cannot certify an earlier page version or silently cross dates")
    patterns=NBS_PATTERNS if provider=="nbs" else PBC_PATTERNS
    if metric not in patterns:
        raise DataError("mapping_unavailable","Metric is not in the versioned native prose mapping")
    if (metric=="fixed_asset_investment_yoy" and period_kind!="year_to_date" or
            metric in {"urban_surveyed_unemployment","cpi_yoy","core_cpi_yoy","ppi_yoy","m2_yoy","m1_yoy","tsf_stock_yoy"} and period_kind!="month"):
        raise DataError("period_mismatch","This mapping does not substitute cumulative values for monthly or stock observations")
    # Keep newlines as scope markers while removing visual span whitespace.
    text="\n".join(clean(line) for line in data["text"].splitlines())
    # PBC's native stock sentence joins 月末 directly to 社会融资; other subjects need a boundary.
    boundary = r"(?:^|[。,\n]|(?<=月末))" if metric=="tsf_stock_yoy" else r"(?:^|[。,\n]|(?<=月份))" if metric=="core_cpi_yoy" else r"(?:^|[。,\n])"
    matches=[m for block in period_segments(text,year,month,period_kind,provider)
             for m in re.finditer(boundary+patterns[metric],block)]
    if metric=="ppi_yoy" and period_kind=="month":
        # Exact national cumulative sentence identifies the omitted subject of its immediately following monthly sentence.
        contextual=rf"(?:^|[。\n])(?:{year}年)?1[—-]{month}月份,全国工业生产者出厂价格同比(?:增长|下降|上涨|下跌)-?\d+(?:\.\d+)?%。其中,{month}月份同比"+CHANGE
        headers=list(re.finditer(r"(?:^|[。\n])(?P<year>\d{4})年",text))
        for m in re.finditer(contextual,text):
            preceding=[h for h in headers if h.start()<=m.start()]
            if not preceding or int(preceding[-1]["year"])==year:matches.append(m)
    if not matches:
        raise DataError("insufficient_coverage","No exact metric/unit expression in the requested source period")
    values=[]
    for match in matches:
        value=numeric(float(match["value"]))
        if match.groupdict().get("direction") in {"下降","下跌"}:
            if value<0:raise DataError("invalid_schema","Conflicting negative sign and decline wording")
            value=-value
        values.append(value)
    if len(set(values))!=1:
        raise DataError("source_disagreement","Repeated source expressions disagree; do not choose one silently")
    observation={"metric":metric,"value":values[0],"unit":"percent","period_start":start,"period_end":end,
        "period_kind":period_kind,"publication_date":published,"available_at":available,
        "publication_time_precision":data.get("publication_time_precision","unknown"),
        "eligibility":"unverified","mapping_version":MAPPING_VERSION,"source_excerpts":list(dict.fromkeys(m.group() for m in matches)),
        "source_notes":[line.strip() for line in data["text"].splitlines() if re.search(r"口径|修订|初步数据|初步统计",line)],
        "source_text_hash":digest(data["text"]),"interpretation":"Native printed value only; no score, weight, policy threshold or action"}
    if snapshot:
        observation.update(eligibility="verified_current_snapshot",available_at=capture.isoformat(),
            publisher_available_at=available,snapshot_as_of_date=cutoff,availability_basis="observed_current_snapshot",
            historical_vintage_certified=False)
    return _selection(provider,observation,[result])
