"""Load the single executable universe used by all pipeline stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pipeline_paths import MARKET_DIR


UNIVERSE_PATH = Path(__file__).with_name("pipeline_universe.json")


def load_universe() -> dict[str, Any]:
    data = json.loads(UNIVERSE_PATH.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise RuntimeError(f"unsupported pipeline universe schema: {data.get('schema_version')}")
    return data


def scoring_assets() -> dict[str, dict[str, Any]]:
    return load_universe()["scoring_assets"]


def safe_symbol(symbol: str) -> str:
    return symbol.replace(".", "_")


def market_csv_path(symbol: str) -> Path:
    return MARKET_DIR / f"{safe_symbol(symbol)}_daily.csv"
