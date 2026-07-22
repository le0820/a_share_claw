from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from a_share_claw.deepresearch_gate import DeepResearchEvidenceGate


class DeepResearchEvidenceGateTest(unittest.TestCase):
    def test_sufficient_decision_blocks_qveris_call(self) -> None:
        gate = DeepResearchEvidenceGate()
        result = json.loads(
            gate.assess(
                decision="SUFFICIENT",
                tavily_source_urls=["https://issuer.example/a", "https://exchange.example/b"],
                qveris_search_id="search-1",
                inspected_tool_ids=["finance.read.v1"],
                missing_information=[],
                rationale="Primary filing and exchange data answer every planned question.",
            )
        )
        self.assertEqual(result["decision"], "SUFFICIENT")
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            gate.authorize_qveris_call(tool_id="finance.read.v1", search_id="search-1")

    def test_need_call_allows_one_reviewed_tool_then_requires_reassessment(self) -> None:
        gate = DeepResearchEvidenceGate()
        result = json.loads(
            gate.assess(
                decision="NEED_QVERIS_CALL",
                tavily_source_urls=["https://issuer.example/a", "https://exchange.example/b"],
                qveris_search_id="search-1",
                inspected_tool_ids=["finance.read.v1"],
                missing_information=["daily OHLCV for the requested date range"],
                rationale="The filings do not contain market price history.",
            )
        )
        self.assertEqual(result["decision"], "NEED_QVERIS_CALL")
        gate.authorize_qveris_call(tool_id="finance.read.v1", search_id="search-1")
        with self.assertRaisesRegex(ValueError, "not included"):
            gate.authorize_qveris_call(tool_id="finance.write.v1", search_id="search-1")

        gate.consume_qveris_call()
        with self.assertRaisesRegex(ValueError, "checkpoint"):
            gate.authorize_qveris_call(tool_id="finance.read.v1", search_id="search-1")

    def test_need_call_requires_missing_question_and_inspected_tool(self) -> None:
        gate = DeepResearchEvidenceGate()
        with self.assertRaisesRegex(ValueError, "missing_information"):
            gate.assess(
                decision="NEED_QVERIS_CALL",
                tavily_source_urls=["https://issuer.example/a", "https://exchange.example/b"],
                qveris_search_id="search-1",
                inspected_tool_ids=["finance.read.v1"],
                missing_information=[],
                rationale="More data might be useful.",
            )


if __name__ == "__main__":
    unittest.main()
