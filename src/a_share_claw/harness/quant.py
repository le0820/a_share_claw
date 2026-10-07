"""Frozen price-window statistics; no implicit universe, calendar or data fetch."""
from __future__ import annotations

import json
import math
import statistics
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from .contracts import canonical, digest, validate_date

METRIC_UNITS = {"period_return": "ratio", "max_drawdown": "ratio", "annualized_volatility": "ratio",
                "excess_return": "percentage_points", "correlation": "ratio"}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("invalid_quant_spec")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("invalid_quant_spec")
    return result


def checked_quant_spec(spec):
    if isinstance(spec,dict) and spec.get('schema_version')=='quant-spec-v2':
        base={k:v for k,v in spec.items() if k!='rotation'};base['schema_version']='quant-spec-v1'
        checked=checked_quant_spec(base)
        from .rotation import checked_rotation
        rotation=checked_rotation(spec.get('rotation'),checked)
        checked.update(schema_version='quant-spec-v2',rotation=rotation)
        return checked
    if isinstance(spec,dict) and spec.get("operation")=="fund_flow_snapshot":
        from .flows import checked_flow_spec
        return checked_flow_spec(spec)
    keys = {"schema_version", "operation", "frequency", "window_start", "window_end", "cutoff_timestamp", "assets", "benchmark", "metrics", "annualization_factor"}
    if not isinstance(spec, dict) or set(spec) != keys or spec["schema_version"] != "quant-spec-v1":
        raise ValueError("invalid_quant_spec")
    if spec["operation"] != "price_statistics" or spec["frequency"] != "daily":
        raise ValueError("quant_operation_not_implemented")
    start, end = validate_date(spec["window_start"]), validate_date(spec["window_end"])
    cutoff = timestamp(spec["cutoff_timestamp"])
    if start > end or type(spec["annualization_factor"]) is not int or not 1 <= spec["annualization_factor"] <= 366:
        raise ValueError("invalid_quant_spec")
    if (not isinstance(spec["assets"], list) or not 1 <= len(spec["assets"]) <= 31 or
            not isinstance(spec["metrics"], list) or not spec["metrics"] or
            any(not isinstance(v, str) for v in spec["metrics"])):
        raise ValueError("invalid_quant_spec")
    if set(spec["metrics"]) - METRIC_UNITS.keys():
        raise ValueError("quant_operation_not_implemented")
    if len(set(spec["metrics"])) != len(spec["metrics"]):
        raise ValueError("invalid_quant_spec")
    symbols = set()
    for asset in spec["assets"]:
        fields = {"symbol", "name", "unit", "currency", "adjustment", "market_timezone", "calendar_source", "anchor", "sessions"}
        if not isinstance(asset, dict) or set(asset) != fields or any(not isinstance(asset[k], str) or not asset[k].strip() for k in fields - {"anchor", "sessions"}):
            raise ValueError("invalid_quant_spec")
        if asset["symbol"] in symbols or not isinstance(asset["sessions"], list) or not 1 <= len(asset["sessions"]) <= 2000:
            raise ValueError("invalid_quant_spec")
        symbols.add(asset["symbol"])
        zone = ZoneInfo(asset["market_timezone"])
        prior = None
        for index, session in enumerate([asset["anchor"], *asset["sessions"]]):
            if not isinstance(session, dict) or set(session) != {"trade_date", "close_at"}:
                raise ValueError("invalid_quant_spec")
            day = validate_date(session["trade_date"])
            close = timestamp(session["close_at"])
            if (close.astimezone(zone).date().isoformat() != day or
                    prior is not None and day <= prior or
                    index == 0 and day >= start or index > 0 and not start <= day <= end):
                raise ValueError("invalid_quant_spec")
            prior = day
            # A planned future cutoff is allowed; execution checks the host clock.
            if close > cutoff:
                raise ValueError("window_not_closed")
    benchmark = spec["benchmark"]
    if benchmark is not None and (not isinstance(benchmark, str) or benchmark not in symbols):
        raise ValueError("invalid_quant_spec")
    if set(spec["metrics"]) & {"excess_return", "correlation"} and benchmark is None:
        raise ValueError("invalid_quant_spec")
    return json.loads(canonical(spec))


def compute_quant(spec, data, reference, as_of_date, market_timezone):
    if spec is None:
        raise ValueError("quant_spec_required")
    cutoff = timestamp(spec["cutoff_timestamp"])
    if cutoff > reference or cutoff.astimezone(ZoneInfo(market_timezone)).date().isoformat() > as_of_date:
        raise ValueError("future_data")
    if (not isinstance(data, dict) or set(data) != {"schema_version", "series"} or data["schema_version"] not in {"price-series-v1","price-series-v2","price-series-v3"} or
            not isinstance(data["series"], list)):
        raise ValueError("price_contract_mismatch")
    current_snapshot=data["schema_version"] in {"price-series-v2","price-series-v3"}
    expected = {a["symbol"]: a for a in spec["assets"]}
    received, prices, metadata = {}, {}, {}
    for series in data["series"]:
        keys = {"symbol", "name", "unit", "currency", "adjustment", "market_timezone", "frequency", "source", "source_file", "source_timestamp", "publication_date", "rows"}
        if current_snapshot:keys=keys|{"current_snapshot"}
        if not isinstance(series, dict) or set(series) != keys or series["symbol"] not in expected or series["symbol"] in received:
            raise ValueError("price_contract_mismatch")
        symbol = series["symbol"]
        asset = expected[symbol]
        if (any(series[k] != asset[k] for k in ("name", "unit", "currency", "adjustment", "market_timezone")) or
                series["frequency"] != "daily" or any(not isinstance(series[k], str) or not series[k].strip() for k in ("source", "source_file")) or
                validate_date(series["publication_date"]) > as_of_date or
                series["publication_date"] > timestamp(series["source_timestamp"]).astimezone(ZoneInfo(series["market_timezone"])).date().isoformat() or
                timestamp(series["source_timestamp"]) > cutoff):
            raise ValueError("price_contract_mismatch")
        if current_snapshot and data["schema_version"]=="price-series-v2":
            meta=series["current_snapshot"];captured=timestamp(series["source_timestamp"])
            if (not isinstance(meta,dict) or set(meta)!={"basis","snapshot_as_of_date","historical_vintage_certified","source_run_id","raw_sha256","sdk_version","native_identity","raw_format"} or
                    meta["basis"]!="observed_current_snapshot" or meta["historical_vintage_certified"] is not False or
                    meta["snapshot_as_of_date"]!=as_of_date or captured.astimezone(ZoneInfo(market_timezone)).date().isoformat()!=as_of_date or
                    meta["sdk_version"]!="1.20.4" or meta["raw_format"]!="decoded_sdk_response" or
                    not isinstance(meta["source_run_id"],str) or not re.fullmatch(r"[a-f0-9]{32}",meta["source_run_id"]) or
                    not isinstance(meta["raw_sha256"],str) or not re.fullmatch(r"[a-f0-9]{64}",meta["raw_sha256"]) or
                    not isinstance(meta["native_identity"],dict) or
                    type(meta["native_identity"].get("market")) is not int or
                    any(not isinstance(meta["native_identity"].get(k),str) or not meta["native_identity"][k].strip() for k in ("code","name")) or
                    series["publication_date"]!=captured.astimezone(ZoneInfo(series["market_timezone"])).date().isoformat()):
                raise ValueError("price_contract_mismatch")
        if data["schema_version"]=="price-series-v3":
            meta=series["current_snapshot"];captured=timestamp(series["source_timestamp"])
            required={"basis","snapshot_as_of_date","historical_vintage_certified","source_run_id","raw_sha256","provider","adapter_version","native_identity","raw_format"}
            if (spec['schema_version']!='quant-spec-v2' or not isinstance(meta,dict) or set(meta)!=required or
                    meta['basis']!='observed_current_snapshot' or meta['snapshot_as_of_date']!=as_of_date or
                    meta['historical_vintage_certified'] is not False or meta['provider']!='swresearch' or
                    meta['adapter_version'] not in {'0.1.0', '0.2.0'} or meta['raw_format']!='native_http_json' or
                    meta['native_identity']!={'code':symbol[:-3],'name':asset['name'],'publisher':'申万宏源研究'} or
                    captured.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()!=as_of_date or
                    not isinstance(meta['source_run_id'],str) or not re.fullmatch(r'[a-f0-9]{32}',meta['source_run_id']) or
                    not isinstance(meta['raw_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',meta['raw_sha256']) or
                    series['publication_date']!=as_of_date):
                raise ValueError('price_contract_mismatch')
        sessions = [asset["anchor"], *asset["sessions"]]
        if not isinstance(series["rows"], list) or len(series["rows"]) != len(sessions):
            raise ValueError("insufficient_coverage:price_sessions")
        closes = {}
        for row, session in zip(series["rows"], sessions):
            if not isinstance(row, dict) or set(row) != {"trade_date", "close", "available_at"} or row["trade_date"] != session["trade_date"]:
                raise ValueError("price_contract_mismatch")
            if type(row["close"]) not in {int, float} or not math.isfinite(row["close"]) or row["close"] <= 0:
                raise ValueError("invalid_market_history")
            available = timestamp(row["available_at"])
            if current_snapshot and available!=timestamp(series["source_timestamp"]):
                raise ValueError("price_contract_mismatch")
            if available > cutoff or available > timestamp(series["source_timestamp"]):
                raise ValueError("future_data")
            if available < timestamp(session["close_at"]):
                raise ValueError("price_before_close")
            closes[row["trade_date"]] = row["close"]
        prices[symbol], received[symbol] = closes, series
        metadata[symbol] = {"name": asset["name"], "unit": asset["unit"], "currency": asset["currency"], "adjustment": asset["adjustment"],
                            "anchor_date": asset["anchor"]["trade_date"], "last_trade_date": asset["sessions"][-1]["trade_date"],
                            "session_count": len(asset["sessions"]), "calendar_source": asset["calendar_source"],
                            "coverage_status": "complete_against_declared_calendar", "input_hash": digest(series),
                            "source": series["source"], "source_file": series["source_file"], "publication_date": series["publication_date"]}
        if current_snapshot:
            metadata[symbol]["current_snapshot"]=json.loads(canonical(series["current_snapshot"]))
    if received.keys() != expected.keys():
        raise ValueError("insufficient_coverage:price_assets")
    benchmark = spec["benchmark"]
    period_returns = {symbol: list(p.values())[-1] / list(p.values())[0] - 1 for symbol, p in prices.items()}
    metrics, unknowns = {}, []
    for symbol, p in prices.items():
        values = list(p.values())
        returns = [right / left - 1 for left, right in zip(values, values[1:])]
        peak, drawdown = values[0], 0
        for value in values:
            peak = max(peak, value)
            drawdown = max(drawdown, 1 - value / peak)
        selected = {}
        for metric in spec["metrics"]:
            if metric == "period_return":
                result = period_returns[symbol]
            elif metric == "max_drawdown":
                result = drawdown
            elif metric == "annualized_volatility":
                if len(returns) < 2:
                    raise ValueError("insufficient_coverage:volatility")
                result = statistics.stdev(returns) * math.sqrt(spec["annualization_factor"])
            elif metric == "excess_return":
                if metadata[symbol]["anchor_date"] != metadata[benchmark]["anchor_date"]:
                    raise ValueError("non_comparable_anchor")
                result = 100 * (period_returns[symbol] - period_returns[benchmark])
            else:
                common = sorted(p.keys() & prices[benchmark].keys())
                if len(common) < 3:
                    raise ValueError("insufficient_coverage:correlation")
                left = [p[b] / p[a] - 1 for a, b in zip(common, common[1:])]
                right = [prices[benchmark][b] / prices[benchmark][a] - 1 for a, b in zip(common, common[1:])]
                try:
                    result = statistics.correlation(left, right)
                except statistics.StatisticsError:
                    result = None
                    unknowns.append({"symbol": symbol, "metric": metric, "reason": "zero_variance"})
            selected[metric] = result
        metrics[symbol] = selected
    return {"schema_version": "quant-output-v1", "specification": spec, "metrics": metrics, "metric_units": METRIC_UNITS,
            "series_audit": metadata, "chart_series": {symbol: {"name": metadata[symbol]["name"],
                "unit": metadata[symbol]["unit"], "source_file": metadata[symbol]["source_file"],
                "input_hash": metadata[symbol]["input_hash"], "rows": received[symbol]["rows"]} for symbol in prices}, "unknowns": unknowns, "risk_decision": "NO_ACTION",
            "limitations": ["Calendar and source identity require trusted host review; labels and hashes alone do not prove authenticity.",
                            "Price changes exclude dividends, fees and FX conversion; these are statistics, not a strategy backtest.",
                            "Volatility uses sample standard deviation of simple local-session returns and the declared annualization factor.",
                            "Correlation uses changes between common calendar-date closes; cross-market closes are not synchronous."]}
