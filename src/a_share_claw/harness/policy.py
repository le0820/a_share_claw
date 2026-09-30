"""Pinned local policies. Providers and models cannot supply weights or executable code."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

from .contracts import canonical, digest


def policy_only(obj):
    if isinstance(obj, dict):
        return {key: policy_only(value) for key, value in obj.items()
                if not key.startswith(("_current", "_example", "_scoring_verdict", "_key_insight"))}
    if isinstance(obj, list):
        return [policy_only(item) for item in obj]
    return obj


class PolicyBundle:
    def __init__(self, root: Path):
        self.root = root
        paths = [*sorted((root / "src" / "compiled").glob("*.json")),
                 *sorted((root / "src/a_share_claw/harness").glob("*.py")),
                 root / "DATA_CONTRACT.md", root / "IDENTITY.md",
                 root / "src/pipeline/rules_L1.py", root / "src/pipeline/ai_strategy.py",
                 root / "src/pipeline/p1_upgrade.py", root / "src/pipeline/run_scoring.py",
                 root / "src/pipeline/pipeline_universe.json"]
        self.hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.version = digest(self.hashes)
        self.weights = json.loads((root / "src/compiled/weight_matrix.json").read_text())
        self.ai_rules = json.loads((root / "src/compiled/ai_strategy_rules.json").read_text())
        self.universe = json.loads((root / "src/pipeline/pipeline_universe.json").read_text())["scoring_assets"]
        # These are trusted existing pure policy implementations; no fetch script is loaded.
        self.l1 = self.module("rules_L1.py")

    def unchanged(self):
        return all((self.root / name).is_file() and hashlib.sha256((self.root / name).read_bytes()).hexdigest() == value
                   for name, value in self.hashes.items())

    def module(self, name):
        if name not in {"rules_L1.py", "ai_strategy.py"}:
            raise ValueError("Unregistered policy implementation")
        spec = importlib.util.spec_from_file_location("_harness_" + name[:-3], self.root / "src/pipeline" / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def plan(self, workflow):
        required = {"macro": ["cn_macro", "us_macro", "market_history"],
                    "ai": ["ai_growth", "ai_market", "ai_macro", "ai_cn"],
                    "company": ["primary_documents"], "industry": ["primary_documents"],
                    "mixed": ["cn_macro", "us_macro", "market_history", "primary_documents"],
                    "quant": ["market_history"], "general": []}
        if workflow not in required:
            raise ValueError("Unsupported workflow")
        return {"workflow": workflow, "policy_version": self.version, "required_capabilities": required[workflow],
                "optional_capabilities": ["tech_capex"] if workflow in {"macro", "mixed"} else [],
                "disabled_layers": ["L2"], "output_template": {
                    "as_of_date": None, "data_audit": None, "scores": None,
                    "confirmed_facts": [], "inference": [], "unknowns": [],
                    "recommendation_or_escalation": "NO_ACTION"}}

    def score_macro(self, facts):
        import numpy as np
        import pandas as pd
        us, cn, histories = facts["us_macro"], facts["cn_macro"], facts["market_history"]
        for group in (us, cn, facts.get("tech_capex", {})):
            for value in group.values():
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    raise ValueError("invalid_numeric_field")
        for field in ("vix", "ust_10y", "ust_30y", "brent", "cpi_us_yoy", "core_cpi_yoy", "ppi_us_yoy",
                      "fed_rate", "nfp_actual", "unemployment", "credit_spread", "jp_jgb_10y", "usdjpy"):
            if us.get(field) is None:
                raise ValueError("missing_required_field:us_macro." + field)
        for field in ("pmi_mfg", "pmi_non_mfg", "retail_sales_yoy", "m2_yoy", "m1_yoy"):
            if cn.get(field) is None:
                raise ValueError("missing_required_field:cn_macro." + field)
        r = self.l1
        us_scores = {"employment": r.score_us_employment(us["nfp_actual"], us["unemployment"]),
                     "inflation": r.score_us_inflation(us["core_cpi_yoy"], us["cpi_us_yoy"], us["ppi_us_yoy"], us["brent"]),
                     "monetary": r.score_us_monetary(us["fed_rate"]),
                     "financial": r.score_us_financial(us["vix"], us["ust_30y"]),
                     "tech_capex": r.score_us_tech_capex(facts.get("tech_capex", {}).get("capex_pct_ocf"))}
        cn_scores = {"manufacturing": r.score_cn_manufacturing(cn["pmi_mfg"], cn["pmi_non_mfg"]),
                     "consumption": r.score_cn_consumption(cn["retail_sales_yoy"], cn["pmi_non_mfg"]),
                     "credit": r.score_cn_credit(cn["m2_yoy"], cn["m1_yoy"], cn.get("tsf_monthly")),
                     "real_estate": None, "policy": None}
        l1 = r.l1_composite(r.us_composite(us_scores), r.cn_composite(cn_scores))
        vix = us["vix"]
        vix_score = 5 if vix < 12 else 4 if vix < 17 else 3 if vix < 22 else 2 if vix < 28 else 1
        pc, credit = us.get("put_call_ratio"), us.get("credit_spread_change_20d")
        sentiment = {"vix": vix_score, "put_call": None if pc is None else 1 if pc > 1 else 2 if pc >= .5 else 3,
                     "dxy": None, "credit_spread": None if credit is None else 4 if credit <= -.1 else 2 if credit >= .1 else 3}
        weights = {k: v for k, v in self.weights["L3_weights"]["sentiment_factors"].items() if not k.startswith("_")}
        coverage = r.factor_coverage(sentiment, weights)
        if coverage["coverage_ratio"] < .5:
            raise ValueError("insufficient_coverage:L3.sentiment")
        per_asset, series = {}, {}
        risk_score = lambda x, thresholds: float(5 - sum(x > threshold for threshold in thresholds))
        for symbol in self.universe:
            rows = histories.get(symbol, [])
            if len(rows) < 61:
                raise ValueError("insufficient_coverage:market_history." + symbol)
            frame = pd.DataFrame(rows).sort_values("trade_date")
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) for value in frame["close"]):
                raise ValueError("invalid_market_history")
            prices = pd.Series(frame["close"].astype(float).to_numpy(), index=pd.to_datetime(frame["trade_date"]))
            if not prices.index.is_unique or any(prices <= 0):
                raise ValueError("invalid_market_history")
            series[symbol] = prices
            returns = prices.pct_change().dropna()
            recent = returns.iloc[-60:]
            quantile = float(recent.quantile(.05))
            var = abs(quantile)
            cvar = abs(float(recent[recent <= quantile].mean()))
            wealth = prices / prices.iloc[0]
            dd = abs(float((wealth / wealth.cummax() - 1).min()))
            rolling = returns.rolling(60).quantile(.05).abs().dropna()
            prior = float(rolling.iloc[-40:-20].mean()) if len(rolling) >= 40 else 0
            trend = float(rolling.iloc[-20:].mean()) / prior - 1 if prior else 0
            parts = [risk_score(var, (.015, .0225, .03, .04)), risk_score(cvar, (.02, .03, .04, .055)),
                     risk_score(dd, (.05, .10, .15, .25)), risk_score(max(trend, -1), (-.15, 0, .15, .35))]
            per_asset[symbol] = {"avg_risk_score": round(float(np.mean(parts)), 2), "var": var, "cvar": cvar, "drawdown": dd}
        aligned = pd.concat({s: p.pct_change() for s, p in series.items()}, axis=1, join="inner").dropna()
        if len(aligned) < 60:
            raise ValueError("insufficient_coverage:aligned_market_history")
        risk = round(float(np.mean([row["avg_risk_score"] for row in per_asset.values()])), 2)
        l3 = round(r.weighted_composite(sentiment, weights) * self.weights["L3_weights"]["sentiment"] + risk * self.weights["L3_weights"]["risk"], 2)
        composite = round(l1 * self.weights["composite_weights"]["L1"] + l3 * self.weights["composite_weights"]["L3"], 2)
        bands = [(1.5, "极端防御", 10, [0, 10]), (2, "防御", 20, [10, 20]), (2.5, "偏防御", 35, [20, 35]),
                 (3, "中性偏保守", 45, [35, 50]), (3.5, "中性偏进攻", 55, [50, 60]), (4, "进攻", 70, [60, 70]), (6, "积极进攻", 85, [70, 85])]
        _, label, center, interval = next(b for b in bands if composite < b[0])
        return {"L1": l1, "L2": None, "L2_status": "disabled", "L3": l3, "composite": composite,
                "us_factor_scores": us_scores, "cn_factor_scores": cn_scores, "sentiment": sentiment,
                "coverage": {"us": r.factor_coverage(us_scores, r.US_WEIGHTS), "cn": r.factor_coverage(cn_scores, r.CN_WEIGHTS), "sentiment": coverage},
                "risk": per_asset, "position_band": {"label": label, "central_pct": center, "range_pct": interval},
                "alerts": ["ust_30y_above_5pct"] if us["ust_30y"] >= 5 else []}

    def score_ai(self, facts, current_ai_pct):
        import math
        import pandas as pd
        policy = self.module("ai_strategy.py")
        def numeric_values(value):
            if isinstance(value, dict):
                for item in value.values():
                    numeric_values(item)
            elif isinstance(value, list):
                for item in value:
                    numeric_values(item)
            elif isinstance(value, bool) or isinstance(value, float) and not math.isfinite(value):
                raise ValueError("invalid_numeric_field")
        numeric_values(facts)
        def prices(symbol):
            rows = facts["ai_market"][symbol]
            if any(not isinstance(row["close"], (int, float)) or row["close"] <= 0 for row in rows):
                raise ValueError("invalid_market_history")
            series = pd.Series([r["close"] for r in rows], index=pd.to_datetime([r["trade_date"] for r in rows])).sort_index()
            if not series.index.is_unique:
                raise ValueError("invalid_market_history")
            return series
        factors = {"growth": policy.growth_state(facts["ai_growth"], self.ai_rules),
                   "momentum": policy.momentum_state(prices("159819.SZ"), prices("510300.SH"), self.ai_rules),
                   "sentiment": policy.sentiment_state(facts["ai_macro"], facts["ai_cn"], self.ai_rules),
                   "liquidity": policy.liquidity_state(facts["ai_macro"], self.ai_rules)}
        return {"factors": factors, "decision": policy.position_decision(factors, self.ai_rules, current_ai_pct)}
