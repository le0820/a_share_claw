from __future__ import annotations

import argparse
from collections.abc import Sequence


def parse_fetch_etf_days(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch ETF and index daily bars used by the scoring pipeline.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=500,
        help="Number of daily bars to fetch (default: 500).",
    )
    return parser.parse_args(argv).days
