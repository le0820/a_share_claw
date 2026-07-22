from __future__ import annotations

from dataclasses import dataclass, field

from .utils import json_dumps


SUFFICIENT = "SUFFICIENT"
NEED_QVERIS_CALL = "NEED_QVERIS_CALL"
_DECISIONS = {SUFFICIENT, NEED_QVERIS_CALL}


@dataclass
class DeepResearchEvidenceGate:
    """One-call authorization gate between evidence discovery and QVeris execution."""

    decision: str | None = None
    qveris_search_id: str | None = None
    inspected_tool_ids: set[str] = field(default_factory=set)
    missing_information: tuple[str, ...] = ()

    def assess(
        self,
        *,
        decision: str,
        tavily_source_urls: list[str],
        qveris_search_id: str | None,
        inspected_tool_ids: list[str],
        missing_information: list[str],
        rationale: str,
    ) -> str:
        normalized = decision.strip().upper()
        if normalized not in _DECISIONS:
            raise ValueError(f"decision must be one of {sorted(_DECISIONS)}")

        sources = _clean_unique(tavily_source_urls)
        tool_ids = _clean_unique(inspected_tool_ids)
        missing = _clean_unique(missing_information)
        reasoning = rationale.strip()
        search_id = (qveris_search_id or "").strip() or None

        if len(sources) < 2:
            raise ValueError("evidence checkpoint requires at least two Tavily/source URLs")
        if not reasoning:
            raise ValueError("evidence checkpoint requires a concise rationale")
        if normalized == SUFFICIENT and missing:
            raise ValueError("SUFFICIENT cannot declare missing_information")
        if normalized == NEED_QVERIS_CALL:
            if not missing:
                raise ValueError("NEED_QVERIS_CALL requires missing_information")
            if not search_id:
                raise ValueError("NEED_QVERIS_CALL requires qveris_search_id from discover")
            if not tool_ids:
                raise ValueError("NEED_QVERIS_CALL requires at least one inspected tool_id")

        self.decision = normalized
        self.qveris_search_id = search_id
        self.inspected_tool_ids = set(tool_ids)
        self.missing_information = tuple(missing)

        if normalized == SUFFICIENT:
            next_action = "Do not call QVeris. Continue to ACTION and TEAM_SYNTHESIS."
        else:
            next_action = (
                "Call qveris_readonly_call once using the same search_id and one inspected tool_id, "
                "then run assess_deepresearch_evidence again."
            )
        return json_dumps(
            {
                "checkpoint": "accepted",
                "decision": normalized,
                "source_count": len(sources),
                "qveris_search_id": search_id,
                "inspected_tool_ids": tool_ids,
                "missing_information": missing,
                "rationale": reasoning,
                "next_action": next_action,
            }
        )

    def authorize_qveris_call(self, *, tool_id: str, search_id: str) -> None:
        if self.decision != NEED_QVERIS_CALL:
            raise ValueError(
                "QVeris call blocked: first submit an evidence checkpoint with NEED_QVERIS_CALL"
            )
        if search_id != self.qveris_search_id:
            raise ValueError("QVeris call blocked: search_id differs from the reviewed discover result")
        if tool_id not in self.inspected_tool_ids:
            raise ValueError("QVeris call blocked: tool_id was not included in the reviewed inspect result")

    def consume_qveris_call(self) -> None:
        """Require a fresh model sufficiency decision after every QVeris call."""
        self.decision = None
        self.qveris_search_id = None
        self.inspected_tool_ids.clear()
        self.missing_information = ()


def _clean_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
