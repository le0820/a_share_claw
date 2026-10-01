from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .config import AppConfig
from .context import ConversationContext


class ResearchWorkflow(str, Enum):
    GENERAL = "general"
    MIXED = "mixed"
    MACRO = "macro"
    QUANT = "quant"
    OUTLOOK = "outlook"
    COMPANY = "company"
    INDUSTRY = "industry"


@dataclass(frozen=True)
class ResearchRouteDecision:
    workflow: ResearchWorkflow
    reason: str


@dataclass(frozen=True)
class ResearchContextBundle:
    workflow: ResearchWorkflow
    routing_reason: str
    instructions: str
    loaded_files: tuple[str, ...]
    missing_files: tuple[str, ...]
    state_scope: str


BASE_CONTEXT_FILES = ("AGENTS.md", "README.md", "IDENTITY.md", "DATA_CONTRACT.md")

# Fail-closed Agent allowlist. Legacy web/MCP/bash/pipeline/file tools are not exposed.
COMMON_TOOL_NAMES = frozenset({"list_data_plugins", "plan_data", "fetch_data", "data_gap_report"})
WORKFLOW_TOOL_NAMES = {
    workflow: COMMON_TOOL_NAMES | {"get_compiled_rule", "get_market_session_status"}
    for workflow in ResearchWorkflow
}

_MACRO_SCORING_TERMS = (
    "每日评分",
    "综合评分",
    "宏观评分",
    "仓位带",
    "数据审计",
    "l1/l2/l3",
    "l1",
    "l2",
    "l3",
    "composite",
)
_QUANT_TERMS = (
    "量化",
    "回测",
    "因子分析",
    "因子排名",
    "最大回撤",
    "夏普",
    "胜率",
    "波动率",
    "相关性",
    "技术指标",
    "技术面",
    "历史收益",
    "均线策略",
    "动量策略",
    "rsi",
    "macd",
    "boll",
    "beta",
    "alpha",
    "var",
    "backtest",
)
_INDUSTRY_STRONG_TERMS = (
    "产业链",
    "行业链",
    "价值链",
    "供应链",
    "行业研究",
    "行业调研",
    "行业分析",
    "行业深度",
    "行业景气",
    "上下游",
    "竞争格局",
    "产能格局",
    "利润池",
    "技术基线",
    "技术路线",
    "value chain",
    "supply chain",
    "industry chain",
)
_COMPANY_STRONG_TERMS = (
    "个股",
    "公司研究",
    "公司调研",
    "公司财报",
    "财报",
    "年报",
    "季报",
    "一季报",
    "中报",
    "业绩预告",
    "业绩快报",
    "最新公告",
    "基本面",
    "护城河",
    "商业模式",
    "现金流",
    "盈利能力",
    "毛利率",
    "roic",
    "roe",
    "ocf",
    "earnings",
)
_COMPANY_OBJECT_TERMS = (
    "个股",
    "公司研究",
    "公司调研",
    "公司财报",
)
_INDUSTRY_TOPICS = (
    "芯片",
    "半导体",
    "光模块",
    "hbm",
    "ai基础设施",
    "数据中心",
    "机器人",
    "新能源",
    "消费电子",
)
_RESEARCH_VERBS = (
    "分析",
    "研究",
    "调研",
    "梳理",
    "拆解",
    "前景",
    "格局",
    "怎么看",
    "怎么样",
)
_POSITION_ACTION_TERMS = (
    "加仓",
    "减仓",
    "补仓",
    "建仓",
    "清仓",
    "持仓",
    "仓位",
    "调仓",
    "换仓",
    "再平衡",
    "买入",
    "卖出",
    "止损",
    "止盈",
    "抄底",
    "能买吗",
    "值得买",
    "配置比例",
)
_MARKET_TERMS = (
    "a股",
    "大盘",
    "早盘",
    "午盘",
    "收盘",
    "行情",
    "盘面",
    "指数",
    "沪深300",
    "上证",
    "深证",
    "创业板",
    "etf",
    "市场总结",
    "市场复盘",
)
_MACRO_TERMS = (
    "宏观",
    "降息",
    "加息",
    "美债",
    "通胀",
    "流动性",
    "cpi",
    "ppi",
    "pmi",
    "macro",
)

_A_SHARE_STOCK_CODE = re.compile(
    r"(?<!\d)(?:000|001|002|003|300|301|302|600|601|603|605|688|689|920)\d{3}(?!\d)"
)
_KNOWN_INDEX_CODES = frozenset(
    {"000001", "000016", "000300", "000852", "000905", "000985", "399001", "399006", "399300", "399673"}
)


def classify_research_workflow(message: str) -> ResearchWorkflow:
    return classify_research_route(message).workflow


def classify_research_route(message: str) -> ResearchRouteDecision:
    normalized = message.casefold()
    scoring_text = re.sub(r"nasdaq\s*composite(?:\s+index)?", "", normalized)
    has_macro_scoring = _contains_any(scoring_text, _MACRO_SCORING_TERMS)
    has_deepresearch = _contains_any(normalized, _COMPANY_STRONG_TERMS) or _contains_any(
        normalized,
        _INDUSTRY_STRONG_TERMS,
    )
    has_outlook = not has_deepresearch and _contains_any(normalized, ("市场展望", "宏观展望", "market outlook"))
    if has_macro_scoring and has_outlook:
        return ResearchRouteDecision(ResearchWorkflow.MIXED, "macro_plus_outlook")
    if has_macro_scoring and has_deepresearch:
        return ResearchRouteDecision(ResearchWorkflow.MIXED, "macro_plus_deepresearch")
    if has_macro_scoring:
        return ResearchRouteDecision(ResearchWorkflow.MACRO, "macro_scoring_or_data_audit")
    if has_outlook:
        return ResearchRouteDecision(ResearchWorkflow.OUTLOOK, "conditional_macro_outlook")
    if _contains_any(normalized, _QUANT_TERMS):
        return ResearchRouteDecision(ResearchWorkflow.QUANT, "quant_method_or_metric")

    stock_codes = [code for code in _A_SHARE_STOCK_CODE.findall(normalized) if code not in _KNOWN_INDEX_CODES]
    if _contains_any(normalized, _COMPANY_OBJECT_TERMS):
        return ResearchRouteDecision(ResearchWorkflow.COMPANY, "explicit_company_research_object")
    if stock_codes and (
        _contains_any(normalized, _RESEARCH_VERBS)
        or _contains_any(normalized, _POSITION_ACTION_TERMS)
        or _contains_any(normalized, _COMPANY_STRONG_TERMS)
        or _contains_any(normalized, _INDUSTRY_STRONG_TERMS)
    ):
        return ResearchRouteDecision(ResearchWorkflow.COMPANY, "a_share_company_code_with_research_intent")

    if _contains_any(normalized, _INDUSTRY_STRONG_TERMS):
        return ResearchRouteDecision(ResearchWorkflow.INDUSTRY, "industry_or_value_chain_research")
    if _contains_any(normalized, _COMPANY_STRONG_TERMS):
        return ResearchRouteDecision(ResearchWorkflow.COMPANY, "company_fundamental_or_filing_research")

    if _contains_any(normalized, _POSITION_ACTION_TERMS):
        return ResearchRouteDecision(ResearchWorkflow.MACRO, "portfolio_position_decision")
    if _contains_any(normalized, _INDUSTRY_TOPICS) and _contains_any(normalized, _RESEARCH_VERBS):
        return ResearchRouteDecision(ResearchWorkflow.INDUSTRY, "industry_topic_with_research_intent")
    if _contains_any(normalized, _MARKET_TERMS) or _contains_any(normalized, _MACRO_TERMS):
        return ResearchRouteDecision(ResearchWorkflow.MACRO, "market_position_or_macro_request")
    return ResearchRouteDecision(ResearchWorkflow.GENERAL, "general_fallback")


def _contains_any(message: str, terms: tuple[str, ...]) -> bool:
    return any(term in message for term in terms)


def tool_names_for_workflow(workflow: ResearchWorkflow) -> frozenset[str]:
    return WORKFLOW_TOOL_NAMES[workflow]


def resolve_system_state_path(
    config: AppConfig,
    context: ConversationContext,
) -> tuple[Path | None, str]:
    per_user = config.data_dir / "users" / context.user_id / "state" / "system_state.json"
    if per_user.exists():
        return per_user, "per_user"

    if context.platform == "local":
        return config.system_state_path, "global_personal"

    allowed = config.telegram_allowed_user_ids
    if len(allowed) == 1 and context.platform_user_id in allowed:
        return config.system_state_path, "global_personal"

    return None, "withheld_multi_user"


def build_research_context(
    config: AppConfig,
    context: ConversationContext,
    message: str,
    *,
    include_state: bool = True,
) -> ResearchContextBundle:
    route = classify_research_route(message)
    workflow = route.workflow
    sections: list[str] = []
    loaded: list[str] = []
    missing: list[str] = []

    for relative in BASE_CONTEXT_FILES:
        path = config.root_dir / relative
        content = _read_text(path)
        if content is None:
            missing.append(relative)
            continue
        loaded.append(relative)
        sections.append(_render_section(relative, content))

    state_path, state_scope = resolve_system_state_path(config, context)
    if not include_state:
        state_scope = "withheld_plugin_only"
        sections.append("## Data boundary\nLegacy portfolio state is withheld: it has no plugin provenance.")
    elif state_path is None:
        sections.append(
            "## system_state\n"
            "Global portfolio state was withheld because this runtime is not configured as a single-user context."
        )
    else:
        state_content = _read_json_text(state_path)
        state_label = _relative_label(config.root_dir, state_path)
        if state_content is None:
            missing.append(state_label)
        else:
            loaded.append(state_label)
            sections.append(_render_section(state_label, state_content))
            external_sources = _external_state_source_paths(config.root_dir, state_path)
            if external_sources:
                sections.append(
                    "## system_state provenance warning\n"
                    "The current state references source files outside this repository. Treat its scores as "
                    "migrated/stale context, not as verified current-repository evidence, until the dated "
                    "pipeline rebuilds them. External paths:\n"
                    + "\n".join(f"- {path}" for path in external_sources)
                )

    if include_state and workflow in {ResearchWorkflow.MIXED, ResearchWorkflow.MACRO, ResearchWorkflow.QUANT}:
        _append_file_section(config.root_dir, config.pipeline_dir / "OPERATIONS.md", sections, loaded, missing)
    if (not include_state and workflow != ResearchWorkflow.GENERAL) or workflow in {
            ResearchWorkflow.MIXED, ResearchWorkflow.COMPANY, ResearchWorkflow.INDUSTRY, ResearchWorkflow.OUTLOOK}:
        content = _read_text(config.research_operations_path)
        label = _relative_label(config.root_dir, config.research_operations_path)
        if content is None:
            missing.append(label)
        else:
            loaded.append(label)
            sections.append(_render_section(label, content))

    if missing:
        guideline = config.root_dir / "GUIDELINE.md"
        guideline_content = _read_text(guideline)
        if guideline_content is not None:
            loaded.append("GUIDELINE.md")
            sections.append(_render_section("GUIDELINE.md (recovery only)", guideline_content))

    header = (
        f"# Active research context\n"
        f"workflow: {workflow.value}\n"
        f"routing_reason: {route.reason}\n"
        f"state_scope: {state_scope}\n"
        "Use only the loaded files below as active workspace policy. "
        "Archives and unloaded manuals are not startup context."
    )
    if workflow is ResearchWorkflow.MIXED:
        header += (
            "\nactive_slices: macro, deepresearch. Keep evidence and outputs separate. "
            "The official macro slice waits for the host close gate. "
            "Continue the deepresearch slice while independent evidence work is possible. "
            "Do not announce combined completion while a required slice remains waiting or blocked."
        )
    if workflow in {ResearchWorkflow.MIXED, ResearchWorkflow.COMPANY, ResearchWorkflow.INDUSTRY, ResearchWorkflow.OUTLOOK}:
        header += (
            "\nFollow the versioned research execution protocol: "
            "PLAN -> EVIDENCE_GATE -> COMPUTE_OR_SYNTHESIZE -> EVALUATE -> ARCHIVE -> PUBLISH. "
            "All roles use the same scoped packet/version and cite admitted facts; "
            "debate only after evidence admission and only for genuine two-sided uncertainty. "
            "Missing protocol or required evidence blocks completion; no fallback to archived SOPs."
        )
    if missing:
        sections.append("## Missing required context\n" + "\n".join(f"- {item}" for item in missing))

    return ResearchContextBundle(
        workflow=workflow,
        routing_reason=route.reason,
        instructions="\n\n".join([header, *sections]),
        loaded_files=tuple(loaded),
        missing_files=tuple(missing),
        state_scope=state_scope,
    )


def _append_file_section(
    root: Path,
    path: Path,
    sections: list[str],
    loaded: list[str],
    missing: list[str],
) -> None:
    label = _relative_label(root, path)
    content = _read_text(path)
    if content is None:
        missing.append(label)
        return
    loaded.append(label)
    sections.append(_render_section(label, content))


def _read_text(path: Path, max_chars: int = 20_000) -> str | None:
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) <= max_chars:
        return content
    return content[:max_chars].rstrip() + "\n\n[truncated by context budget]"


def _read_json_text(path: Path, max_chars: int = 16_000) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    if len(rendered) <= max_chars:
        return rendered
    return rendered[:max_chars].rstrip() + "\n\n[truncated by context budget]"


def _external_state_source_paths(root: Path, state_path: Path) -> tuple[str, ...]:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    audit = data.get("data_audit") if isinstance(data, dict) else None
    source_files = audit.get("source_files") if isinstance(audit, dict) else None
    if not isinstance(source_files, list):
        return ()
    resolved_root = root.resolve()
    external: list[str] = []
    for item in source_files:
        raw_path = item.get("path") if isinstance(item, dict) else None
        if not isinstance(raw_path, str) or not raw_path:
            continue
        source_path = Path(raw_path).expanduser()
        if not source_path.is_absolute():
            source_path = root / source_path
        try:
            source_path.resolve().relative_to(resolved_root)
        except ValueError:
            external.append(raw_path)
    return tuple(external)


def _render_section(label: str, content: str) -> str:
    return f"## Loaded file: {label}\n\n{content.strip()}"


def _relative_label(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())
