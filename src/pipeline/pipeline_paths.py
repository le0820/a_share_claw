"""Single source of truth for investment-pipeline runtime paths."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT_DIR = Path(__file__).resolve().parents[2]
_configured_data_root = Path(os.environ.get("ASCLAW_DATA_DIR", ROOT_DIR / "data")).expanduser()
DATA_ROOT = (_configured_data_root if _configured_data_root.is_absolute() else ROOT_DIR / _configured_data_root).resolve()
RAW_DIR = DATA_ROOT / "raw"
MARKET_DIR = RAW_DIR / "market"
ANALYSIS_DIR = DATA_ROOT / "analysis"
SCORES_DIR = DATA_ROOT / "scores"
REPORTS_DIR = DATA_ROOT / "reports"
STATE_DIR = DATA_ROOT / "state"
STATE_FILE = STATE_DIR / "system_state.json"
RESEARCH_DIR = DATA_ROOT / "research"
RESEARCH_OUTPUT_DIR = RESEARCH_DIR / "output"
AI_RAW_DIR = RAW_DIR / "ai"
AI_MARKET_DIR = AI_RAW_DIR / "market"
AI_FACTORS_DIR = DATA_ROOT / "factors" / "ai"
AI_SIGNALS_DIR = DATA_ROOT / "signals" / "ai"
AI_BACKTEST_DIR = DATA_ROOT / "backtests" / "ai"


def ensure_runtime_dirs() -> None:
    for path in (
        RAW_DIR,
        MARKET_DIR,
        ANALYSIS_DIR,
        SCORES_DIR,
        REPORTS_DIR,
        STATE_DIR,
        RESEARCH_OUTPUT_DIR,
        AI_RAW_DIR,
        AI_MARKET_DIR,
        AI_FACTORS_DIR,
        AI_SIGNALS_DIR,
        AI_BACKTEST_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


def validate_as_of_date(value: str, *, now_utc: datetime | None = None) -> str:
    if not re.fullmatch(r"\d{8}", value):
        raise ValueError("date must use YYYYMMDD")
    parsed = datetime.strptime(value, "%Y%m%d").date()
    today = current_date(now_utc)
    if parsed > today:
        raise ValueError(f"date {value} is in the future relative to {today.isoformat()}")
    return value


def current_date(now_utc: datetime | None = None):
    """Return the A-share market date, independent of the owner/scheduler timezone."""
    market_timezone = os.environ.get("ASCLAW_MARKET_TIMEZONE", "Asia/Shanghai")
    reference = now_utc or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference.astimezone(ZoneInfo(market_timezone)).date()
