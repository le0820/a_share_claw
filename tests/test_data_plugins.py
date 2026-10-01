"""Offline provider contract fixtures; no credentials or live service required."""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from unittest.mock import patch

import pytest

from a_share_claw.data_plugins import DataRun, default_registry
from a_share_claw.data_plugins.core import DataError, Manifest, Payload, Provider, Registry, Requirement, Transport, preview
from a_share_claw.data_plugins.providers import FRED, NBS, PBC, SEC, TickFlow


class FakeTransport:
    def __init__(self, response):
        self.raw = response if isinstance(response, bytes) else json.dumps(response).encode()
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params or {}, headers or {}))
        return self.raw


def requirement(provider, capability, params, day="2026-08-01", rid="input"):
    return Requirement.parse(dict(requirement_id=rid, provider=provider, capability=capability,
                                  as_of_date=day, params=params))


def execute(provider, req, tmp_path):
    run = DataRun({provider.manifest.plugin_id: provider}, tmp_path, "test-user/session")
    run.plan({"framework": "A dated research question and explicit evidence gap", "requirements": [req.json()]})
    return run, asyncio.run(run.fetch(req.requirement_id))


def test_plan_required_and_zero_plugin_plan(tmp_path):
    run = DataRun({}, tmp_path, "u")
    assert asyncio.run(run.fetch("missing"))["error_code"] == "plan_required"
    req = requirement("fred", "macro.series", {"series_id": "DGS10", "start_date": "2026-07-01"})
    run.plan({"framework": "Rates", "requirements": [req.json()]})
    assert asyncio.run(run.fetch("input"))["error_code"] == "provider_unavailable"
    assert not run.summary()["required_data_complete"]
    assert not run.summary()["official_output_allowed"]


@pytest.mark.parametrize("provider", [FRED, SEC, TickFlow])
def test_missing_credentials_are_explicit_and_do_not_fetch(provider, tmp_path):
    params = {FRED: {"series_id": "DGS10", "start_date": "2026-07-01"},
              SEC: {"cik": "320193", "concepts": ["us-gaap:Assets"]},
              TickFlow: {"symbols": ["600519.SH"], "start_date": "2026-01-01"}}[provider]
    capability = {FRED: "macro.series", SEC: "company.facts", TickFlow: "financial.income"}[provider]
    transport = FakeTransport({})
    _, result = execute(provider({}, transport), requirement(provider.manifest.plugin_id, capability, params), tmp_path)
    assert result["error_code"] == "not_configured"
    assert transport.calls == []


def test_fred_vintage_missing_value_and_archive(tmp_path):
    transport = FakeTransport({"count": 2, "observations": [
        {"date": "2026-07-01", "value": "4.2", "realtime_start": "2026-08-01", "realtime_end": "2026-08-01"},
        {"date": "2026-07-02", "value": ".", "realtime_start": "2026-08-01", "realtime_end": "2026-08-01"}]})
    run, result = execute(FRED({"FRED_API_KEY": "secret-test"}, transport),
                          requirement("fred", "macro.series", {"series_id": "DGS10", "start_date": "2026-07-01"}), tmp_path)
    assert result["status"] == "ok"
    assert result["data"]["observations"][1]["value"] is None
    assert transport.calls[0][1]["realtime_end"] == "2026-08-01"
    assert transport.calls[0][1]["realtime_start"] == "2026-08-01"
    assert "secret-test" not in "".join(p.read_text() for p in run.directory.glob("*.json"))
    raw = (run.directory / result["provenance"]["artifact"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == result["provenance"]["sha256"]
    asyncio.run(run.fetch("input"))
    assert len(transport.calls) == 1  # same-run evidence replay, no refetch
    assert run.summary()["required_data_complete"]
    assert not run.summary()["official_output_allowed"]


@pytest.mark.parametrize("count,day,vintage,code", [
    (100001, "2026-07-01", "2026-08-01", "insufficient_coverage"),
    (1, "2026-08-02", "2026-08-01", "future_data"),
    (1, "2026-07-01", "2026-08-02", "future_data"),
])
def test_fred_rejects_pagination_future_and_wrong_vintage(tmp_path, count, day, vintage, code):
    transport = FakeTransport({"count": count, "observations": [
        {"date": day, "value": "1", "realtime_start": vintage, "realtime_end": vintage}]})
    _, result = execute(FRED({"FRED_API_KEY": "test"}, transport),
                        requirement("fred", "macro.series", {"series_id": "DGS10", "start_date": "2026-07-01"}), tmp_path)
    assert result["error_code"] == code


def test_sec_filters_future_filings_and_preserves_units_and_durations(tmp_path):
    fact = {"start": "2026-01-01", "end": "2026-03-31", "filed": "2026-05-01", "val": 10,
            "form": "10-Q", "accn": "original"}
    transport = FakeTransport({"cik": 320193, "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        fact, {**fact, "filed": "2026-09-01", "val": 99, "accn": "restated"}]}}}}})
    _, result = execute(SEC({"SEC_USER_AGENT": "test contact@example.com"}, transport),
                        requirement("sec", "company.facts", {"cik": "320193", "concepts": ["us-gaap:Revenues"]}), tmp_path)
    rows = result["data"]["facts"]["us-gaap:Revenues"]
    assert len(rows) == 1 and rows[0]["value"] == 10 and rows[0]["unit"] == "USD"
    assert rows[0]["start"] == fact["start"] and rows[0]["accn"] == "original"
    assert transport.calls[0][0].endswith("CIK0000320193.json")
    assert "contact@example.com" not in json.dumps(result)


@pytest.mark.parametrize("provider,host", [(NBS, "www.stats.gov.cn"), (PBC, "www.pbc.gov.cn")])
def test_official_publication_text_dates_and_no_fabricated_series(provider, host, tmp_path):
    raw = '<html><meta name="PubDate" content="2026/07/15 09:30"><script>evil()</script><p>单位：亿元</p><table><tr><td>M2</td><td>300</td></tr></table></html>'.encode()
    transport = FakeTransport(raw)
    _, result = execute(provider({}, transport), requirement(provider.manifest.plugin_id, "macro.release", {"url": f"https://{host}/release.html"}), tmp_path)
    assert result["status"] == "unverified"
    assert result["data"]["publication_date"] == "2026-07-15"
    assert "300" in result["data"]["text"] and "evil" not in result["data"]["text"]
    assert result["data"]["numeric_series"] is False


def test_publication_future_date_and_external_links_are_blocked(tmp_path):
    transport = FakeTransport(b'<html><meta name="PubDate" content="2026-09-01"><p>1</p></html>')
    _, result = execute(NBS({}, transport), requirement("nbs", "macro.release", {"url": "https://www.stats.gov.cn/r.html"}), tmp_path)
    assert result["error_code"] == "future_data"
    transport = FakeTransport(b'<html><a href="/r.html">release</a><a href="https://evil.example/r.html">bad</a></html>')
    _, result = execute(NBS({}, transport), requirement("nbs", "macro.release_index", {}), tmp_path)
    assert result["data"]["links"] == [{"url": "https://www.stats.gov.cn/r.html", "title": "release"}]


@pytest.mark.parametrize("url", ["http://www.stats.gov.cn/r.html", "https://www.stats.gov.cn.evil.example/r.html",
                                 "https://127.0.0.1/r.html", "https://www.stats.gov.cn:444/r.html",
                                 "https://user:pass@www.stats.gov.cn/r.html", "https://www.stats.gov.cn/r.html?key=x"])
def test_source_allowlist_denies_before_network(url):
    with patch("urllib.request.build_opener", side_effect=AssertionError("network must not open")):
        with pytest.raises(DataError, match="declared HTTPS"):
            Transport(NBS.manifest).get(url)


@pytest.mark.parametrize("capability,path", [("financial.income", "income"),
                                              ("financial.balance_sheet", "balance-sheet"),
                                              ("financial.cash_flow", "cash-flow")])
def test_tickflow_three_statements_use_documented_endpoints(capability, path, tmp_path):
    transport = FakeTransport({"data": {"600519.SH": [{"period_end": "2026-03-31", "revenue": 10}]}})
    _, result = execute(TickFlow({"TICKFLOW_API_KEY": "test-key"}, transport),
                        requirement("tickflow", capability, {"symbols": ["600519.SH"], "start_date": "2026-01-01"},
                                    day=datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()), tmp_path)
    assert transport.calls[0][0].endswith("financials/" + path)
    assert transport.calls[0][2] == {"x-api-key": "test-key"}
    assert result["status"] == "unverified" and "test-key" not in json.dumps(result)
    assert result["data"]["native_data"]["600519.SH"][0]["revenue"] == 10


def test_tickflow_columnar_bars_and_explicit_adjustment(tmp_path):
    ts = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    data = {"timestamp": [ts], **{k: [10] for k in ("open", "high", "low", "close", "volume", "amount")}}
    transport = FakeTransport({"data": data})
    _, result = execute(TickFlow({"TICKFLOW_API_KEY": "test"}, transport), requirement("tickflow", "market.daily_bars",
                        {"symbol": "600519.SH", "start_date": "2026-07-01", "adjust": "none"}), tmp_path)
    assert result["data"]["bars"][0]["date"] == "2026-07-01"
    assert transport.calls[0][1]["adjust"] == "none"
    assert result["status"] == "unverified"
    _, result = execute(TickFlow({"TICKFLOW_API_KEY": "test"}, transport), requirement("tickflow", "market.daily_bars",
                        {"symbol": "600519.SH", "start_date": "2026-07-01"}), tmp_path)
    assert result["error_code"] == "invalid_request"


def test_snapshot_hot_removal_and_credential_changes_do_not_mutate_active_run():
    registry = default_registry()
    env = {"FRED_API_KEY": "old", "ASCLAW_DATA_PROVIDERS": "fred"}
    before = registry.snapshot(env)
    registry.unregister("fred")
    env["FRED_API_KEY"] = "new"
    assert before["fred"].credential() == "old" and "fred" not in registry.snapshot(env)
    registry.register(FRED)
    assert registry.snapshot(env)["fred"].credential() == "new"
    assert registry.snapshot({"ASCLAW_DATA_PROVIDERS": ""}) == {}


def test_plan_extension_cannot_mutate_old_requirements_and_is_scope_isolated(tmp_path):
    first = DataRun({}, tmp_path, "user-a/session")
    second = DataRun({}, tmp_path, "user-b/session")
    req = requirement("nbs", "macro.release_index", {})
    plan = {"framework": "Discover official releases", "requirements": [req.json()]}
    first.plan(plan)
    second.plan(plan)
    assert first.directory.parent != second.directory.parent
    plan["requirements"][0]["params"]["url"] = "https://evil.example/r.html"
    assert "url" not in first.requirements["input"].params
    with pytest.raises(ValueError, match="cannot be removed or changed"):
        first.plan(plan)
    extension = requirement("pbc", "macro.release_index", {}, rid="second")
    first.plan({"framework": "Also discover monetary releases", "requirements": [req.json(), extension.json()]})
    assert len(list(first.directory.glob("plan-*.json"))) == 2


def test_preview_keeps_valid_envelope_and_does_not_truncate_archive():
    result = {"ok": True, "data": "x" * 20000, "provenance": {"sha256": "a"}, "truncated": False}
    rendered = json.loads(preview(result, 5000))
    assert rendered["truncated"] and rendered["provenance"] == result["provenance"]
    assert len(result["data"]) == 20000


def test_tickflow_historical_financials_block_before_network(tmp_path):
    transport = FakeTransport({"data": {"future_disclosure": 99}})
    _, result = execute(TickFlow({"TICKFLOW_API_KEY": "test"}, transport),
                        requirement("tickflow", "financial.income", {"symbols": ["600519.SH"], "start_date": "2025-01-01"}), tmp_path)
    assert result["error_code"] == "historical_unavailable"
    assert transport.calls == [] and result["data"] is None


def test_redirects_never_forward_credentials():
    import urllib.error
    from a_share_claw.data_plugins.core import _NoRedirect
    handler = _NoRedirect()
    assert handler.redirect_request(None, None, 302, "", {}, "https://evil.example") is None
    with patch("urllib.request.build_opener") as opener:
        opener.return_value.open.side_effect = urllib.error.HTTPError("https://api.stlouisfed.org?api_key=secret", 302, "", {}, None)
        with pytest.raises(DataError) as exc:
            Transport(FRED.manifest).get("https://api.stlouisfed.org/fred/series/observations", {"api_key": "secret"})
        assert "secret" not in str(exc.value)
        assert opener.return_value.open.call_count == 1


def test_transient_failure_retries_are_bounded_and_redacted():
    import urllib.error
    with patch("urllib.request.build_opener") as opener, patch("time.sleep"):
        opener.return_value.open.side_effect = urllib.error.HTTPError("https://api.stlouisfed.org?api_key=secret", 429, "", {}, None)
        with pytest.raises(DataError) as exc:
            Transport(FRED.manifest).get("https://api.stlouisfed.org/fred/series/observations", {"api_key": "secret"})
        assert exc.value.retryable and "secret" not in str(exc.value)
        assert opener.return_value.open.call_count == 3
