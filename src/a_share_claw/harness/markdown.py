"""Deterministic readable report; model/source text cannot introduce markup."""
from __future__ import annotations

import html
import json
import re

from .contracts import canonical, digest

VERSION = "markdown-v1"
TITLES = {"macro": "宏观评分", "ai": "AI 仓位叠加层", "company": "公司研究", "industry": "行业链研究",
          "quant": "价格统计", "outlook": "市场展望", "mixed": "混合请求"}
ROLES = {"hong_guan": "Hong Guan / 宏观", "jia_zhi": "Jia Zhi / 价值链", "ge_yan": "Ge Yan / 技术",
         "qian_zhan": "Qian Zhan / 多方", "shen_du": "Shen Du / 空方", "ping_heng": "Ping Heng / 风险"}


def plain(value):
    if value is None:
        return "未定义 / null"
    text = canonical(value) if isinstance(value, (dict, list, bool)) else str(value)
    text = html.escape(text, quote=False).replace("\r", " ").replace("\n", " / ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~])", r"\\\1", text)


def table(headers, rows):
    result = ["| " + " | ".join(map(plain, headers)) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    result += ["| " + " | ".join(map(plain, row)) + " |" for row in rows]
    return result + [""]


def bullets(values):
    return ["- " + plain(v) for v in values] + [""] if values else ["无已记录项。", ""]


def statistics(data, level):
    heading = "#" * level
    spec = data["specification"]
    result = [heading + " 冻结窗口与价格口径", ""]
    result += bullets(["窗口：" + spec["window_start"] + " 至 " + spec["window_end"],
                       "截止：" + spec["cutoff_timestamp"], "基准：" + str(spec["benchmark"]),
                       "年化因子：" + str(spec["annualization_factor"])])
    assets = spec["assets"]
    result += table(["标的", "名称", "币种/单位", "复权", "市场时区", "日历来源", "锚点", "声明会话数"],
                    [[v["symbol"], v["name"], v["currency"] + "/" + v["unit"], v["adjustment"], v["market_timezone"],
                      v["calendar_source"], v["anchor"]["trade_date"], len(v["sessions"])] for v in assets])
    result += [heading + " 核心计算统计", ""]
    result += table(["标的", "指标", "原始数值", "单位"],
                    [[symbol, metric, value, data["metric_units"][metric]] for symbol, values in data["metrics"].items() for metric, value in values.items()])
    result += [heading + " 序列覆盖审计", ""]
    result += table(["标的", "序列审计"], [[symbol, value] for symbol, value in data["series_audit"].items()])
    result += [heading + " 限制与未定义项", ""] + bullets(data["limitations"] + data["unknowns"])
    return result


def body(workflow, data, level=2):
    heading = "#" * level
    if workflow == "mixed":
        result = [heading + " 切片状态", ""]
        result += table(["切片", "工作流", "run_id", "状态", "行动"],
                        [[v["slice_id"], v["workflow"], v["run_id"], v["disposition"], "NO_ACTION"] for v in data["slice_status"]])
        for child in data["slices"]:
            result += [heading + " 切片：" + plain(child["slice_id"]), "子运行仅为研究产物；没有独立正式发布。", ""]
            result += body(child["workflow"], child["data"], level+1)
        return result + [heading + " 综合缺口", ""] + bullets(data["combined_gaps"])
    if workflow == "macro":
        result = [heading + " 评分与规则候选仓位", ""]
        result += table(["项", "数值"], [[k, data[k]] for k in ("L1", "L2", "L2_status", "L3", "composite", "position_band")])
        result += ["Composite 保持 L1 × 4/7 + L3 × 3/7；L2 disabled/null。", "", heading + " 驱动、覆盖与风险", ""]
        result += table(["组", "内容"], [[k, data[k]] for k in ("us_factor_scores", "cn_factor_scores", "sentiment", "risk", "alerts")])
        omitted = [group + "." + k for group in ("us_factor_scores", "cn_factor_scores", "sentiment") for k,v in data[group].items() if v is None]
        return result + [heading + " 缺失因子", ""] + bullets(omitted)
    if workflow == "ai":
        return ([heading + " 原始因子计算", ""] + table(["因子", "结果"], [[k,v] for k,v in data["factors"].items()]) +
                [heading + " 规则候选决策（发布由终态决定）", ""] + table(["字段", "值"], [[k,v] for k,v in data["decision"].items()]))
    if workflow == "quant":
        return statistics(data, level)
    result = []
    if workflow == "outlook":
        result += [heading + " 预测窗口与条件基准情景", ""]
        result += bullets([data["forecast_horizon"], data["base_scenario"], "情景是条件推断，不是新观测、交易动作或已验证概率。"])
        result += statistics(data["market_statistics"], level)
    result += [heading + " 准入事实与核心派生值", ""]
    result += table(["fact_id", "类型", "对象/指标", "数值", "单位", "统计期", "发布/观测日", "来源文件"],
                    [[f["fact_id"], "核心派生" if f["source"] in {"core_quant_v1","core_macro_v1"} else "来源事实",
                      f["entity"] + "/" + f["metric"], f["value"], f["unit"], f["data_period"],
                      (f["publication_date"] or "原始发布日期未知") + "/" + f["observation_date"], f["source_file"]] for f in data["confirmed_facts"]])
    snapshots=[f for f in data["confirmed_facts"] if "availability" in f]
    if snapshots:
        result += ["", "当前快照口径：以下数值是本次抓取看到的版本；原始发布日期与快照可得时间分别保留，不证明抓取前的历史页面版本。", ""]
        result += table(["事实", "当前快照可得时间", "原文发布时钟", "发布精度", "来源口径备注", "选择哈希"],
            [[f["fact_id"],f["available_at"],f["availability"]["publisher_available_at"],
              f["availability"]["publication_time_precision"],f["availability"]["source_notes"],f["availability"]["selection_hash"]] for f in snapshots])
    derived_macro=[f for f in data["confirmed_facts"] if f["source"]=="core_macro_v1"]
    if derived_macro:
        result += [heading + " 核心月度指数变化计算", "", "原生同 vintage 指数计算，不是发布机构另行确认的同比/环比数值。原始观测发布日期未知；未采用 series last_updated 代替。", ""]
        result += table(["事实","公式","原生输入事实","输入哈希"],
            [[f["fact_id"],f["derivation"]["formula"],f["derivation"]["input_fact_ids"],f["derivation"]["input_hashes"]] for f in derived_macro])
    result += [heading + " 角色推断与事实引用", "", "以下文案已通过候选语义评估；引用存在本身不证明推断正确。", ""]
    unknowns = []
    phases = ["hong_guan:initial", "ge_yan:initial", "jia_zhi:initial", "qian_zhan:initial", "shen_du:initial", "qian_zhan:rebuttal", "shen_du:rebuttal", "ping_heng:final"]
    for key in phases:
        if key not in data["role_outputs"]:
            continue
        output = data["role_outputs"][key]
        result += ["#"*(level+1) + " " + plain(ROLES[output["role"]] + " / " + output["phase"]), ""]
        result += table(["问题", "引用 fact_id", "推断"], [[a["question_id"], a["fact_ids"], a["inference"]] for a in output["answers"]])
        if output["responds_to"]:
            result += bullets(["回应：" + ", ".join(output["responds_to"])])
        unknowns += [{"role":key, **u} for u in output["unknowns"]]
    result += [heading + " 未知项", ""] + bullets(unknowns)
    result += [heading + " 风险与监控触发条件", "", "风险决策：NO_ACTION。", ""]
    result += table(["条件", "引用 fact_id"], [[v["condition"],v["fact_ids"]] for v in data["monitoring_triggers"]])
    result += [heading + " 语义评估", ""] + bullets([data["semantic_review"]])
    return result


def _render(report):
    report = json.loads(canonical(report))  # stable across archive load and dictionary insertion order
    workflow = report["workflow"]
    if report.get("markdown_version") != VERSION or workflow not in TITLES:
        raise ValueError("report_contract_failure")
    result = ["# " + TITLES[workflow] + "报告", "", "归档状态：staged。该文件本身不证明正式发布；以授权读取的运行终态和状态事务为准。", "",
              "## 运行与数据口径", ""]
    result += table(["字段", "值"], [[k,report[k]] for k in ("run_id", "plan_id", "scope_key", "workflow", "as_of_date", "generated_at", "mode", "fallback_status", "policy_version")])
    result += ["JSON 报告哈希：" + digest(report), "", "## 来源表", ""]
    result += table(["能力/切片", "来源", "文件", "日期", "日期口径", "观测日", "统计期", "捕获时间", "事实哈希"],
        [[p["capability"] + ("/" + p["slice_id"] if "slice_id" in p else ""),p["source"],p["source_file"],p["publication_date"],p.get("publication_date_basis","source_declared_date"),
          p["observation_date"],p["data_period"],p["source_timestamp"],p["sha256"]] for p in report["source_table"]])
    result += body(workflow, report["data"])
    result += ["## 授权历史比较", ""] + bullets([report["prior_comparison"]])
    result += ["## 交付边界", "", "归档文本不执行交易。research/replay 和研究子切片保持 NO_ACTION。来源真实性、vintage 和日历仍需相应验收；没有来源的字段不补造。", ""]
    return "\n".join(result)


def render_markdown(report):
    return _render(report)


def validate_markdown(rendered, report):
    if not isinstance(rendered, str) or rendered != _render(report):
        raise ValueError("report_contract_failure")
