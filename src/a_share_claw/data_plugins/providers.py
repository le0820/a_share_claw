"""Five explicitly selected sources; no substitute providers or web-search fallback."""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, time, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from .core import DataError, Manifest, Payload, Provider, Requirement, iso_date


def parameters(r: Requirement, allowed: set[str], required: set[str] = frozenset()) -> dict:
    p = dict(r.params)
    if set(p) - allowed or required - set(p):
        raise DataError("invalid_request", "Unsupported or missing capability parameters")
    return p


def identifier(value: str, pattern: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise DataError("invalid_request", "Invalid source identifier")
    return value


def number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not an observation")
    n = float(value)
    if not math.isfinite(n):
        raise ValueError("Non-finite observation")
    return n


def document_url(url: str, hosts: tuple[str, ...]) -> str:
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname not in hosts or p.username or p.password
            or p.port not in (None, 443) or p.query or p.fragment or not p.path.endswith((".html", ".htm"))):
        raise DataError("source_denied", "Use an HTML publication URL on this provider's official host")
    return url


class _Publication(HTMLParser):
    """Retain published prose/table cells without interpreting their economic meaning."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.text = []
        self.links = []
        self._href = None
        self._label = []
        self._ignored = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "style"}:
            self._ignored += 1
        if tag == "meta":
            self.meta[(attrs.get("name") or attrs.get("property") or "").lower()] = attrs.get("content", "")
        if tag == "a":
            self._href, self._label = attrs.get("href"), []
        if tag in {"p", "div", "tr", "br", "table"}:
            self.text.append("\n")
        if tag in {"td", "th"}:
            self.text.append("\t")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self._ignored = max(0, self._ignored - 1)
        if tag == "a" and self._href:
            self.links.append((self._href, "".join(self._label).strip()))
            self._href = None

    def handle_data(self, data):
        if not self._ignored:
            self.text.append(data)
            if self._href:
                self._label.append(data)

    def publication_date(self):
        for key in ("pubdate", "publishdate", "publishtime", "date", "dc.date", "article:published_time"):
            value = self.meta.get(key, "")
            m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", value)
            if m:
                return iso_date(f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}")
        # Only labelled release timestamps, never an observation period or URL date.
        m = re.search(r"(?:发布时间|发布日期|文章来源)[：:\s]*[^\n]{0,100}?(\d{4}-\d{2}-\d{2})", "".join(self.text))
        return iso_date(m[1]) if m else None


class OfficialPublication(Provider):
    index_url: str

    def fetch(self, r: Requirement) -> Payload:
        p = parameters(r, {"url"})
        index = r.capability == "macro.release_index"
        url = document_url(p.get("url", self.index_url) if index else p["url"], self.manifest.hosts)
        raw = self.transport.get(url)
        # Sites sometimes serve GB18030 despite modern UTF-8 pages.
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030")
        if "<html" not in text.lower():
            raise DataError("invalid_schema", "Expected an official HTML publication")
        parser = _Publication()
        parser.feed(text)
        if index:
            links = []
            seen = set()
            for href, label in parser.links:
                try:
                    target = document_url(urljoin(url, href), self.manifest.hosts)
                except (DataError, ValueError):
                    continue
                if label and target != url and target not in seen:
                    links.append({"url": target, "title": label})
                    seen.add(target)
            if not links:
                raise DataError("no_results", "No publication links found")
            return Payload({"links": links, "purpose": "discovery_only"}, raw, url, "unverified",
                           ["Index is current discovery, not historical macro evidence; plan a dated release request next"])
        published = parser.publication_date()
        if published and published > r.as_of_date:
            raise DataError("future_data", "Publication is later than the requested availability cutoff")
        content = re.sub(r"\n[ \t\n]+", "\n", "".join(parser.text)).strip()
        if not content:
            raise DataError("no_results", "Publication contains no readable text")
        # A live page can be revised without changing its initial publication date.
        # Preserve prose/units and require archived vintage validation for historical scoring.
        return Payload({"publication_date": published, "text": content,
                        "format": "official_publication_text", "numeric_series": False}, raw, url, "unverified",
                       ["Publication text is evidence, not a normalized macro time series",
                        "Live page revision history is unavailable; no historical-vintage certification"])


class NBS(OfficialPublication):
    manifest = Manifest("nbs", ("macro.release", "macro.release_index"), ("www.stats.gov.cn",))
    index_url = "https://www.stats.gov.cn/sj/zxfb/index.html"


class PBC(OfficialPublication):
    manifest = Manifest("pbc", ("macro.release", "macro.release_index"), ("www.pbc.gov.cn",))
    index_url = "https://www.pbc.gov.cn/diaochatongjisi/116219/index.html"


class FRED(Provider):
    manifest = Manifest("fred", ("macro.series", "macro.series_metadata"), ("api.stlouisfed.org",), "FRED_API_KEY", version="1.1.0")

    def fetch(self, r: Requirement) -> Payload:
        if r.capability == "macro.series_metadata":
            return self._metadata(r)
        p = parameters(r, {"series_id", "start_date", "end_date", "limit"}, {"series_id", "start_date"})
        series = identifier(p["series_id"], r"[A-Za-z0-9_]{1,80}")
        start, end = iso_date(p["start_date"]), iso_date(p.get("end_date", r.as_of_date))
        limit = int(p.get("limit", 10000))
        if not start <= end <= r.as_of_date or not 1 <= limit <= 100000:
            raise DataError("invalid_request", "Invalid date window or observation limit")
        url = "https://api.stlouisfed.org/fred/series/observations"
        raw = self.transport.get(url, {"api_key": self.credential(), "file_type": "json", "series_id": series,
                                      "observation_start": start, "observation_end": end, "limit": limit,
                                      "realtime_start": r.as_of_date, "realtime_end": r.as_of_date})
        obj = json.loads(raw)
        if int(obj["count"]) > limit:
            raise DataError("insufficient_coverage", "Response is paginated; narrow the requested window")
        rows = []
        for row in obj["observations"]:
            day = iso_date(row["date"])
            vintage_start, vintage_end = iso_date(row["realtime_start"]), iso_date(row["realtime_end"])
            if not start <= day <= end or not vintage_start <= r.as_of_date <= vintage_end:
                raise DataError("future_data", "FRED observation/vintage does not match the requested cutoff")
            rows.append({"date": day, "value": None if row["value"] == "." else number(row["value"]),
                         "realtime_start": vintage_start, "realtime_end": vintage_end})
        if not rows or not any(row["value"] is not None for row in rows):
            raise DataError("no_results", "No observations in the requested vintage/window")
        return Payload({"series_id": series, "vintage_date": r.as_of_date, "observations": rows,
                        "units": "source_native", "transformation": "none"}, raw, url, "verified",
                       ["Cutoff is date-level/end-of-day, not intraday publication timing"])


    def _metadata(self, r: Requirement) -> Payload:
        p = parameters(r, {"series_id"}, {"series_id"})
        series = identifier(p["series_id"], r"[A-Za-z0-9_]{1,80}")
        url = "https://api.stlouisfed.org/fred/series"
        raw = self.transport.get(url, {"api_key": self.credential(), "file_type": "json", "series_id": series,
                                      "realtime_start": r.as_of_date, "realtime_end": r.as_of_date})
        rows = json.loads(raw)["seriess"]
        if not isinstance(rows,list) or len(rows)!=1 or rows[0].get("id")!=series:
            raise DataError("invalid_schema", "Expected exactly the requested FRED series metadata")
        row=rows[0]
        if not iso_date(row["realtime_start"])<=r.as_of_date<=iso_date(row["realtime_end"]):
            raise DataError("future_data", "Metadata vintage does not match the requested cutoff")
        fields=("title","units","frequency","seasonal_adjustment","last_updated")
        if any(not isinstance(row.get(key),str) or not row[key].strip() for key in fields):
            raise DataError("invalid_schema", "Missing FRED series identity or units metadata")
        return Payload({"series_id":series,"vintage_date":r.as_of_date,**{key:row[key] for key in fields}},raw,url,"verified",
                       ["Series last_updated is not a per-observation original release timestamp",
                        "Metadata preserves native units/frequency/seasonal adjustment; no rescaling or economic-series substitution"])


class SEC(Provider):
    manifest = Manifest("sec", ("company.facts",), ("data.sec.gov",), "SEC_USER_AGENT")

    def fetch(self, r: Requirement) -> Payload:
        p = parameters(r, {"cik", "concepts", "start_date"}, {"cik", "concepts"})
        cik = identifier(p["cik"], r"\d{1,10}").zfill(10)
        concepts = p["concepts"]
        if not isinstance(concepts, list) or not 1 <= len(concepts) <= 40:
            raise DataError("invalid_request", "Request 1..40 explicit taxonomy:concept identifiers")
        for concept in concepts:
            identifier(concept, r"(?:us-gaap|ifrs-full|dei):[A-Za-z0-9_]+")
        start = iso_date(p.get("start_date", "1900-01-01"))
        contact = self.credential()
        if "@" not in contact or "\n" in contact or "\r" in contact:
            raise DataError("not_configured", "SEC_USER_AGENT must identify the application and contact email")
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
        raw = self.transport.get(url, headers={"User-Agent": contact, "Accept": "application/json"})
        obj = json.loads(raw)
        if str(obj["cik"]).zfill(10) != cik:
            raise DataError("invalid_schema", "SEC response CIK does not match the request")
        facts, missing = {}, []
        for concept in concepts:
            taxonomy, tag = concept.split(":")
            units = obj["facts"].get(taxonomy, {}).get(tag, {}).get("units", {})
            rows = []
            for unit, items in units.items():
                for item in items:
                    filed, end = iso_date(item["filed"]), iso_date(item["end"])
                    if filed > r.as_of_date or not start <= end <= r.as_of_date:
                        continue
                    if item.get("form") not in {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}:
                        continue
                    if item.get("start") and iso_date(item["start"]) > end:
                        raise ValueError("Invalid financial duration")
                    rows.append({**{key: item[key] for key in ("start", "end", "filed", "accn", "form", "fy", "fp", "frame") if key in item},
                                 "value": number(item["val"]), "unit": unit})
            if rows:
                facts[concept] = sorted(rows, key=lambda row: (row["end"], row["filed"], row.get("accn", "")))
            else:
                missing.append(concept)
        if not facts:
            raise DataError("no_results", "No requested facts were filed by the availability cutoff")
        return Payload({"cik": cik, "facts": facts, "missing_concepts": missing}, raw, url,
                       "unverified" if missing else "verified",
                       ["Facts preserve duration, units and all eligible filings; do not sum duplicate vintages",
                        "Standard entity-wide XBRL facts only; custom tags/segments and complete statement layouts are not reconstructed",
                        "SEC filed date is a date-level cutoff, not intraday acceptance timing"])


class TickFlow(Provider):
    manifest = Manifest("tickflow", ("market.quote", "market.daily_bars", "financial.income",
                                     "financial.balance_sheet", "financial.cash_flow"),
                        ("api.tickflow.org",), "TICKFLOW_API_KEY")

    def fetch(self, r: Requirement) -> Payload:
        if r.capability.startswith("financial."):
            return self._financial(r)
        p = parameters(r, {"symbol", "start_date", "end_date", "adjust", "count"}, {"symbol"})
        symbol = identifier(p["symbol"], r"[A-Za-z0-9._-]{1,32}")
        headers = {"x-api-key": self.credential()}
        if r.capability == "market.quote":
            if set(p) != {"symbol"}:
                raise DataError("invalid_request", "Quote accepts only symbol")
            if r.as_of_date != datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat():
                raise DataError("historical_unavailable", "Quote endpoint is a current snapshot, not historical quotes")
            url = "https://api.tickflow.org/v1/quotes"
            raw = self.transport.get(url, {"symbols": symbol}, headers)
            obj = json.loads(raw)
            if not obj.get("data"):
                raise DataError("no_results", "No quote returned")
            return Payload(obj["data"], raw, url, "unverified",
                           ["Native quote snapshot; trade timestamp/currency require schema-specific validation"])
        start = iso_date(p["start_date"])
        end = iso_date(p.get("end_date", r.as_of_date))
        adjust = p.get("adjust")
        count = int(p.get("count", 10000))
        if not start <= end <= r.as_of_date or adjust not in {"none", "forward", "backward", "forward_additive", "backward_additive"} or not 1 <= count <= 10000:
            raise DataError("invalid_request", "Daily bars require a valid window, explicit adjustment and count 1..10000")
        if symbol.endswith((".SH", ".SZ", ".BJ", ".HK")):
            zone = ZoneInfo("Asia/Shanghai")
        elif symbol.endswith((".US", ".NASDAQ", ".NYSE")):
            zone = ZoneInfo("America/New_York")
        else:
            raise DataError("invalid_request", "Use an exchange-qualified symbol with a known trading timezone")
        stamp = lambda d, t: int(datetime.combine(datetime.fromisoformat(d).date(), t, zone).timestamp() * 1000)
        url = "https://api.tickflow.org/v1/klines"
        raw = self.transport.get(url, {"symbol": symbol, "period": "1d", "adjust": adjust,
                                      "start_time": stamp(start, time.min), "end_time": stamp(end, time.max), "count": count}, headers)
        data = json.loads(raw)["data"]
        columns = ("timestamp", "open", "high", "low", "close", "volume", "amount")
        size = len(data["timestamp"])
        if any(len(data[key]) != size for key in columns):
            raise DataError("invalid_schema", "TickFlow columns have unequal lengths")
        if not size:
            raise DataError("no_results", "No daily bars returned")
        if size >= count:
            raise DataError("insufficient_coverage", "Count limit reached; split the date window to prove coverage")
        rows = []
        for i in range(size):
            observed = datetime.fromtimestamp(number(data["timestamp"][i]) / 1000, zone).date().isoformat()
            if not start <= observed <= end:
                raise DataError("future_data", "Bar is outside the requested observation window")
            rows.append({"date": observed, **{key: number(data[key][i]) for key in columns}})
        if len({row["date"] for row in rows}) != size:
            raise DataError("invalid_schema", "Duplicate daily bars")
        return Payload({"symbol": symbol, "adjust": adjust, "timezone": str(zone),
                        "bars": sorted(rows, key=lambda row: row["date"])}, raw, url, "unverified",
                       ["Current-vintage market history; adjusted prices may incorporate later corporate actions",
                        "Trading-calendar completeness, unit mapping and official scoring are not certified"])

    def _financial(self, r: Requirement) -> Payload:
        p = parameters(r, {"symbols", "start_date", "end_date"}, {"symbols", "start_date"})
        symbols = p["symbols"]
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 20:
            raise DataError("invalid_request", "symbols must contain 1..20 instruments")
        for symbol in symbols:
            identifier(symbol, r"[A-Za-z0-9._-]{1,32}")
        start, end = iso_date(p["start_date"]), iso_date(p.get("end_date", r.as_of_date))
        if not start <= end <= r.as_of_date:
            raise DataError("invalid_request", "Invalid financial reporting window")
        credential = self.credential()
        if r.as_of_date != datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat():
            raise DataError("historical_unavailable", "Financial period filters do not establish disclosure vintage; historical requests are blocked")
        path = {"financial.income": "income", "financial.balance_sheet": "balance-sheet",
                "financial.cash_flow": "cash-flow"}[r.capability]
        url = f"https://api.tickflow.org/v1/financials/{path}"
        raw = self.transport.get(url, {"symbols": ",".join(symbols), "start_date": start,
                                      "end_date": end, "latest": "false"}, {"x-api-key": credential})
        obj = json.loads(raw)
        if not isinstance(obj.get("data"), (dict, list)):
            raise DataError("invalid_schema", "Expected native TickFlow financial data")
        if not obj["data"]:
            raise DataError("no_results", "No financial statements returned")
        # Public docs currently specify data as an opaque object. Never invent field
        # names or equate period_end to the announcement date.
        return Payload({"statement": path, "symbols": symbols, "native_data": obj["data"],
                        "period_filter": {"start": start, "end": end}}, raw, url, "unverified",
                       ["Native schema preserved; disclosure dates, revision vintage and accounting units need account-sample validation",
                        "period_end filtering is not point-in-time filtering; not eligible for historical conclusions"])
