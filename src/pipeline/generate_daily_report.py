#!/usr/bin/env python3
"""generate_daily_report.py — 评分 JSON → 结构化 Markdown 报告 (前端可解析)

读取 run_scoring.py 产出的 4 个 JSON:
  scores_L1_*.json / scores_L2_*.json / scores_L3_*.json / scores_composite_*.json

产出:
  data/reports/daily_report_${date}.md
    - YAML frontmatter (composite/L1/L2/L3/position/audit 等, 前端直接读元数据)
    - 数据派生表格 (L1 US/CN 因子, L2 禁用状态, L3 情绪/风险)
    - 数据审计块 (fallback 状态, 来源文件)
    - 五角色定性研判: 用各 JSON 的 assessment 串成"驱动摘要" + 显式标记位供 LLM 填充

设计原则:
  - 机器可读 (frontmatter + 表格) 与 人工研判 (<!-- TEAM_ANALYSIS -->) 诚实分离
  - 缺失文件不静默跳过, 在报告中标注 MISSING
  - 不伪造任何数据; 所有值来自 JSON

用法:
  python generate_daily_report.py --date 20260708
  python generate_daily_report.py            # 默认今天 YYYYMMDD
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pipeline_paths import (
    REPORTS_DIR as PIPELINE_REPORTS_DIR,
    SCORES_DIR as PIPELINE_SCORES_DIR,
    current_date,
    validate_as_of_date,
)

SCORES_DIR = str(PIPELINE_SCORES_DIR)
REPORTS_DIR = str(PIPELINE_REPORTS_DIR)


def _load(date, kind):
    """kind: L1 / L2 / L3 / composite → 返回 (dict_or_None, path)"""
    path = os.path.join(SCORES_DIR, f"scores_{kind}_{date}.json")
    if not os.path.exists(path):
        return None, path
    try:
        with open(path) as f:
            return json.load(f), path
    except Exception as e:
        return {"_parse_error": str(e)}, path


def _fmt(v):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def _factor_row(name, block):
    """block: {score, value, rationale} → markdown table row"""
    score = block.get("score", "—") if isinstance(block, dict) else "—"
    val = block.get("value", "") if isinstance(block, dict) else ""
    rat = block.get("rationale", "") if isinstance(block, dict) else ""
    if isinstance(val, dict):
        val = ", ".join(f"{k}={v}" for k, v in val.items())
    return f"| {name} | {_fmt(score)} | {val} | {rat} |"


def build_md(date, L1, L2, L3, C):
    lines = []
    # ── frontmatter ──
    comp = C.get("composite") if C else None
    lines.append("---")
    lines.append('title: "每日宏观评分报告"')
    lines.append(f'as_of_date: "{date}"')
    lines.append(f'generated_at: "{datetime.now(timezone.utc).isoformat()}"')
    if C:
        lines.append(f'composite: {comp}')
        lines.append(f'l1: {C.get("L1")}')
        lines.append(f'l2: {"null" if C.get("L2") is None else C.get("L2")}')
        lines.append(f'l3: {C.get("L3")}')
    if L1:
        lines.append(f'l1_us: {L1.get("L1_US_composite")}')
        lines.append(f'l1_cn: {L1.get("L1_CN_composite")}')
    if C:
        lines.append(f'position_band: "{C.get("position_band", "")}"')
        lines.append(f'position_range: "{C.get("position_range", "")}"')
        lines.append(f'position_central: {C.get("position_central", "")}')
    # audit summary
    fb = (C or L1 or L2 or L3 or {}).get("data_audit", {})
    fallbacks = fb.get("fallbacks", []) if isinstance(fb, dict) else []
    static = fb.get("static_fallback_fields", []) if isinstance(fb, dict) else []
    omitted = fb.get("omitted_factors", []) if isinstance(fb, dict) else []
    lines.append(f'fallback_used: {bool(fallbacks or static)}')
    lines.append(f'fallback_count: {len(fallbacks) + len(static)}')
    lines.append(f'omitted_factor_count: {len(omitted)}')
    lines.append("---")
    lines.append("")

    # ── 标题 ──
    date_h = f"{date[:4]}-{date[4:6]}-{date[6:]}"
    lines.append(f"# 📊 每日宏观评分报告 — {date_h}")
    lines.append("")

    # ── 数据口径 ──
    lines.append("## 一、数据口径")
    lines.append("")
    lines.append(f"- **as_of_date**: `{date}`")
    lines.append(f"- **generated_at**: `{datetime.now(timezone.utc).isoformat()}`")
    if C and C.get("formula"):
        lines.append(f"- **Composite 公式**: `{C['formula']}`")
    lines.append(f"- **fallback 状态**: {'⚠️ 使用回退' if (fallbacks or static) else '✅ 未注入 fallback'}")
    lines.append(f"- **未评分因子**: `{len(omitted)}`（缺源因子不填中性值，按已观测权重重归一）")
    if isinstance(fb, dict):
        srcs = fb.get("source_files", [])
        if srcs:
            lines.append("- **来源文件**:")
            for s in srcs:
                lines.append(f"  - {s.get('label')}: `{os.path.basename(s.get('path',''))}` ({s.get('role')})")
    lines.append("")

    # ── 评分总览 ──
    lines.append("## 二、评分总览")
    lines.append("")
    lines.append("| 层级 | 分数 | 说明 |")
    lines.append("|:---|:---:|:---|")
    if L1:
        lines.append(f"| L1 宏观 | **{L1.get('L1_composite')}** | US {L1.get('L1_US_composite')} / CN {L1.get('L1_CN_composite')} |")
    if L2:
        l2_value = "—" if L2.get("L2_composite") is None else L2.get("L2_composite")
        lines.append(f"| L2 因子 | **{l2_value}** | {L2.get('status', 'unknown')} |")
    if L3:
        lines.append(f"| L3 资金/技术 | **{L3.get('L3_composite')}** | — |")
    if C:
        lines.append(f"| **Composite** | **{C.get('composite')}** | {C.get('position_band')} |")
        lines.append("")
        lines.append(f"**仓位带**: {C.get('position_band')} ｜ **区间**: `{C.get('position_range')}` ｜ **中枢**: `{C.get('position_central')}%`")
        if C.get("previous_composite") is not None:
            lines.append(f"**较前值**: {C.get('change')} (prev {C.get('previous_composite')})")
    lines.append("")

    # ── L1 因子 ──
    if L1:
        lines.append("## 三、L1 宏观因子明细")
        lines.append("")
        lines.append("### US（权重各 0.20）")
        lines.append("")
        lines.append("| 因子 | 分 | 真实值 | 依据 |")
        lines.append("|:---|:---:|:---|:---|")
        for k, label in [("employment","employment"),("inflation","inflation"),("monetary","monetary"),("financial","financial"),("tech_capex","tech_capex")]:
            if k in L1.get("us", {}):
                lines.append(_factor_row(label, L1["us"][k]))
        lines.append("")
        lines.append("### CN（权重来自 compiled/weight_matrix.json；缺失因子按已观测权重重归一）")
        lines.append("")
        lines.append("| 因子 | 分 | 真实值 | 依据 |")
        lines.append("|:---|:---:|:---|:---|")
        for k in ["manufacturing","consumption","credit","real_estate","policy"]:
            if k in L1.get("cn", {}):
                lines.append(_factor_row(k, L1["cn"][k]))
        if L1.get("assessment"):
            lines.append("")
            lines.append(f"> **L1 驱动摘要**: {L1['assessment']}")
        lines.append("")

    # ── L2 状态 ──
    if L2:
        lines.append("## 四、L2 因子层")
        lines.append("")
        lines.append(f"- **状态**: `{L2.get('status', 'unknown')}`")
        lines.append(f"- **Composite 权重**: `{L2.get('composite_weight', 0)}`")
        lines.append(f"- **原因**: {L2.get('reason', '—')}")
        lines.append("- **替代策略**: 不使用动量、估值快照或非官方代理静默替代 FF5。")
        if L2.get("assessment"):
            lines.append("")
            lines.append(f"> **L2 驱动摘要**: {L2['assessment']}")
        lines.append("")

    # ── L3 情绪/风险 ──
    if L3:
        lines.append("## 五、L3 情绪/风险")
        lines.append("")
        sent = L3.get("sentiment", {})
        lines.append(f"- **Sentiment 综合**: `{sent.get('composite')}`")
        if isinstance(sent, dict):
            for k in ["vix","put_call","aaii","dxy","credit_spread"]:
                b = sent.get(k)
                if isinstance(b, dict):
                    lines.append(f"  - {k}: score `{b.get('score')}` value `{b.get('value')}` — {b.get('note', b.get('interpretation',''))}")
        rsi = L3.get("rsi_14", {})
        if rsi:
            lines.append(f"- **RSI(14)**: " + ", ".join(f"{k}={v}" for k, v in rsi.items()))
        risk = L3.get("risk", {})
        if isinstance(risk, dict):
            lines.append(f"- **Risk 综合**: `{risk.get('composite')}`")
            lines.append("")
            lines.append("| ETF | VaR | CVaR | DD | VaR趋势 | 均分 |")
            lines.append("|:---|:---:|:---:|:---:|:---:|:---:|")
            for etf, m in risk.get("per_etf", {}).items():
                if isinstance(m, dict):
                    lines.append(f"| {etf} ({m.get('name','')}) | {m.get('var_score')} | {m.get('cvar_score')} | {m.get('dd_score')} | {m.get('var_trend_score')} | {m.get('avg_risk_score')} |")
        if L3.get("assessment"):
            lines.append("")
            lines.append(f"> **L3 驱动摘要**: {L3['assessment']}")
        lines.append("")

    # ── 数据审计 ──
    lines.append("## 六、数据审计")
    lines.append("")
    if isinstance(fb, dict):
        lines.append(f"- allow_stale_fallback: `{fb.get('allow_stale_fallback')}`")
        lines.append(f"- allow_static_fallback: `{fb.get('allow_static_fallback')}`")
        lines.append(f"- fallbacks: `{len(fallbacks)}` ｜ static_fallback_fields: `{len(static)}`")
        lines.append(f"- omitted_factors: `{len(omitted)}`")
        if fallbacks or static:
            lines.append("")
            lines.append("⚠️ **使用了回退数据，报告非完全精确**:")
            for f in (fallbacks + static):
                lines.append(f"  - {f}")
        if omitted:
            lines.append("")
            lines.append("未评分且未注入中性值的因子:")
            for factor in omitted:
                lines.append(f"  - {factor}")
    else:
        lines.append("- (无 data_audit 块)")
    lines.append("")

    # ── 五角色团队研判 (LLM 填充位) ──
    lines.append("## 七、五角色团队研判")
    lines.append("")
    lines.append("> 以下为数据驱动的**驱动摘要**（自动生成，来自各层 assessment）。")
    lines.append("> 定性研判 / 多空辩论 / 最终风控动作由 Agent 交付层按需填充。")
    lines.append("")
    lines.append("<!-- TEAM_ANALYSIS_START -->")
    lines.append("<!-- TEAM_ANALYSIS: 由 LLM 填充: Ping Heng 路由/风控, Hong Guan 宏观, Jia Zhi 价值, Ge Yan 技术, Qian Zhan/Shen Du 多空 -->")
    lines.append("_（此处由 LLM 生成五角色研判文本）_")
    lines.append("<!-- TEAM_ANALYSIS_END -->")
    lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=current_date().strftime("%Y%m%d"),
                    help="评分日期 YYYYMMDD (默认今天)")
    args = ap.parse_args()
    try:
        date = validate_as_of_date(args.date)
    except ValueError as exc:
        ap.error(str(exc))

    os.makedirs(REPORTS_DIR, exist_ok=True)
    L1, p1 = _load(date, "L1")
    L2, p2 = _load(date, "L2")
    L3, p3 = _load(date, "L3")
    C,  p4 = _load(date, "composite")

    missing = [p for p, d in [(p1,L1),(p2,L2),(p3,L3),(p4,C)] if d is None]
    if missing:
        print(f"❌ MISSING score files for {date}:", file=sys.stderr)
        for m in missing:
            print(f"   {m}", file=sys.stderr)
        return 2

    for label, data in (("L1", L1), ("L2", L2), ("L3", L3), ("composite", C)):
        if data.get("as_of_date") != date:
            print(f"❌ {label} embedded as_of_date does not match {date}", file=sys.stderr)
            return 2
        audit = data.get("data_audit")
        if not isinstance(audit, dict):
            print(f"❌ {label} is missing data_audit", file=sys.stderr)
            return 2
        if audit.get("allow_static_fallback") or audit.get("static_fallback_fields"):
            print(f"❌ {label} contains static fallback and cannot produce an official report", file=sys.stderr)
            return 2

    md = build_md(date, L1, L2, L3, C)
    out = os.path.join(REPORTS_DIR, f"daily_report_{date}.md")
    with open(out, "w") as f:
        f.write(md)
    print(f"✅ report → {out} ({len(md)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
