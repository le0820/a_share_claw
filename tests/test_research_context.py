from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.config import AppConfig
from a_share_claw.context import ConversationContext
from a_share_claw.research_context import (
    ResearchWorkflow,
    build_research_context,
    classify_research_route,
    classify_research_workflow,
    tool_names_for_workflow,
)


class ResearchContextTest(unittest.TestCase):
    def _workspace(self, root: Path) -> AppConfig:
        for name in ("AGENTS.md", "README.md", "IDENTITY.md", "DATA_CONTRACT.md", "GUIDELINE.md"):
            (root / name).write_text(f"# {name}\n{name}-marker\n", encoding="utf-8")
        (root / "src" / "pipeline").mkdir(parents=True)
        (root / "src" / "pipeline" / "OPERATIONS.md").write_text(
            "# Macro Operations\nmacro-operation-marker\n",
            encoding="utf-8",
        )
        (root / "src" / "a_share_claw").mkdir(parents=True)
        (root / "src" / "a_share_claw" / "RESEARCH_OPERATIONS.md").write_text(
            "# Industry Operations\n\n"
            "## Active Contract\nindustry-contract-marker\n\n"
            "## Execution protocol\nPLAN -> EVIDENCE_GATE -> EVALUATE\n",
            encoding="utf-8",
        )
        (root / "data" / "deepresearch").mkdir(parents=True)
        (root / "data" / "deepresearch" / "OPERATIONS.md").write_text("private-industry-body")
        (root / "data" / "state").mkdir(parents=True)
        (root / "data" / "state" / "system_state.json").write_text(
            '{"as_of_date":"20260713","portfolio_secret":"owner-only"}',
            encoding="utf-8",
        )
        return AppConfig.from_env(root)

    def test_macro_load_order_and_archive_exclusion(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = self._workspace(root)
            context = ConversationContext("u", "c", "s", "local", "local", "local")
            bundle = build_research_context(config, context, "请生成宏观每日评分和数据审计")

            self.assertEqual(bundle.workflow, ResearchWorkflow.MACRO)
            self.assertEqual(
                bundle.loaded_files,
                (
                    "AGENTS.md",
                    "README.md",
                    "IDENTITY.md",
                    "DATA_CONTRACT.md",
                    "data/state/system_state.json",
                    "src/pipeline/OPERATIONS.md",
                ),
            )
            self.assertNotIn("GUIDELINE.md-marker", bundle.instructions)
            self.assertIn("macro-operation-marker", bundle.instructions)
            self.assertNotIn("industry-contract-marker", bundle.instructions)

    def test_multi_user_telegram_withholds_global_portfolio_state(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = dataclasses.replace(
                self._workspace(root),
                telegram_allowed_user_ids=frozenset({"100", "200"}),
            )
            context = ConversationContext("u", "c", "s", "telegram", "100", "chat")
            bundle = build_research_context(config, context, "分析 HBM 产业链")

            self.assertEqual(bundle.workflow, ResearchWorkflow.INDUSTRY)
            self.assertEqual(bundle.state_scope, "withheld_multi_user")
            self.assertNotIn("owner-only", bundle.instructions)
            self.assertIn("industry-contract-marker", bundle.instructions)
            self.assertIn("PLAN -> EVIDENCE_GATE -> EVALUATE", bundle.instructions)
            self.assertNotIn("private-industry-body", bundle.instructions)

    def test_realistic_workflow_classification(self) -> None:
        cases = {
            ResearchWorkflow.MIXED: [
                "针对北京时间7月13日与美东时间7月10日的市场走势和A股最新业绩预告，运行北京时间7月14的每日评分数据管线、7月14日市场前瞻分析展望和7月13日市场总结并交由团队进行分析与分数判断。",
                "运行今日L1/L2/L3评分，同时调研A股最新业绩预告和公司公告",
                "生成每日评分，并补充半导体产业链最新证据",
            ],
            ResearchWorkflow.MACRO: [
                "测试工具、上下文、数据接口。但不调用模型。数据交由你来分析A股7月14日早盘行情，并评估是否可以对159682、159516进行加仓？",
                "今天A股早盘怎么看？结合当前仓位，159682和159516可以加仓吗？",
                "根据最新L1/L2/L3评分，把权益仓位从55%调整到多少？",
                "沪深300今天收盘如何，是否需要减仓？",
                "分析半导体ETF 159516是否可以补仓",
            ],
            ResearchWorkflow.QUANT: [
                "用过去三年数据回测159682的20日均线策略，并给最大回撤和夏普比率",
                "计算159516与创业板指60日相关性、波动率和RSI",
                "做一个ETF动量因子排名，不要主观判断",
                "量化回测半导体ETF的择时策略",
                "回测300750财报发布后20日的超额收益和胜率",
            ],
            ResearchWorkflow.COMPANY: [
                "个股调研：分析宁德时代2026年一季报、现金流、ROE和估值",
                "分析000333的财报和护城河，列出多空证据",
                "300750还能加仓吗？结合估值和最新公告",
                "个股调研：宁德时代在动力电池供应链中的竞争力",
                "分析300750在动力电池产业链的议价权和增长风险",
            ],
            ResearchWorkflow.INDUSTRY: [
                "梳理半导体设备产业链、上下游和利润池",
                "做HBM供应链深度调研，比较各环节议价权",
                "分析光模块行业竞争格局和技术路线",
                "研究半导体设备产业链后判断159516是否值得配置",
                "比较半导体设备产业链内龙头公司的财报和利润率",
            ],
            ResearchWorkflow.GENERAL: [
                "记住我的风险偏好",
                "把这段英文翻译成中文",
            ],
        }

        for expected, messages in cases.items():
            for message in messages:
                with self.subTest(expected=expected.value, message=message):
                    decision = classify_research_route(message)
                    self.assertEqual(decision.workflow, expected)
                    self.assertNotEqual(decision.reason, "")
                    self.assertEqual(classify_research_workflow(message), expected)

    def test_workflow_tool_routes_are_bounded(self) -> None:
        expected = {"list_data_plugins", "plan_data", "fetch_data", "data_gap_report",
                    "get_compiled_rule", "get_market_session_status"}
        for workflow in ResearchWorkflow:
            self.assertEqual(set(tool_names_for_workflow(workflow)), expected)

    def test_quant_and_company_load_their_required_operations(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = self._workspace(root)
            context = ConversationContext("u", "c", "s", "local", "local", "local")

            quant = build_research_context(config, context, "回测159682动量策略并计算最大回撤")
            self.assertEqual(quant.workflow, ResearchWorkflow.QUANT)
            self.assertIn("src/pipeline/OPERATIONS.md", quant.loaded_files)
            self.assertIn("routing_reason: quant_method_or_metric", quant.instructions)

            company = build_research_context(config, context, "个股调研：分析000333最新财报")
            self.assertEqual(company.workflow, ResearchWorkflow.COMPANY)
            self.assertIn("src/a_share_claw/RESEARCH_OPERATIONS.md", company.loaded_files)
            self.assertIn("routing_reason: explicit_company_research_object", company.instructions)
            self.assertIn("PLAN -> EVIDENCE_GATE -> COMPUTE_OR_SYNTHESIZE", company.instructions)
            self.assertNotIn("qveris_readonly_call", company.instructions)
            self.assertNotIn("get_operation_manual", company.instructions.split("## Loaded file:")[0])

            mixed = build_research_context(
                config,
                context,
                "运行每日评分并调研A股最新业绩预告",
            )
            self.assertEqual(mixed.workflow, ResearchWorkflow.MIXED)
            self.assertIn("src/pipeline/OPERATIONS.md", mixed.loaded_files)
            self.assertIn("src/a_share_claw/RESEARCH_OPERATIONS.md", mixed.loaded_files)
            self.assertIn("active_slices: macro, deepresearch", mixed.instructions)
            self.assertIn("Continue the deepresearch slice", mixed.instructions)

    def test_guideline_loads_only_when_required_context_is_missing(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = self._workspace(root)
            (root / "IDENTITY.md").unlink()
            context = ConversationContext("u", "c", "s", "local", "local", "local")
            bundle = build_research_context(config, context, "普通问题")
            self.assertIn("IDENTITY.md", bundle.missing_files)
            self.assertIn("GUIDELINE.md", bundle.loaded_files)
            self.assertIn("GUIDELINE.md-marker", bundle.instructions)

    def test_external_legacy_state_sources_are_flagged_in_active_context(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = self._workspace(root)
            config.system_state_path.write_text(
                '{"data_audit":{"source_files":[{"path":"/legacy/openclaw/raw_macro_20260713.json"}]}}',
                encoding="utf-8",
            )
            context = ConversationContext("u", "c", "s", "local", "local", "local")
            bundle = build_research_context(config, context, "请生成宏观每日评分")

            self.assertIn("system_state provenance warning", bundle.instructions)
            self.assertIn("migrated/stale context", bundle.instructions)
            self.assertIn("/legacy/openclaw/raw_macro_20260713.json", bundle.instructions)


if __name__ == "__main__":
    unittest.main()
