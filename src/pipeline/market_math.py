"""Return and regression math shared by P1 and CN style analysis."""

from __future__ import annotations

import math
from collections.abc import Iterable


def simple_returns(prices: Iterable[float]) -> list[float]:
    values = [float(value) for value in prices]
    returns: list[float] = []
    for previous, current in zip(values, values[1:]):
        if previous <= 0:
            raise ValueError("prices must be positive")
        returns.append(current / previous - 1.0)
    return returns


def compound_simple_returns(returns: Iterable[float]) -> float:
    growth = 1.0
    for value in returns:
        growth *= 1.0 + float(value)
    return growth - 1.0


def annualized_sharpe(daily_returns: Iterable[float], annual_risk_free: float = 0.0) -> float:
    values = [float(value) for value in daily_returns]
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    volatility = math.sqrt(variance) * math.sqrt(252)
    return ((mean * 252) - annual_risk_free) / volatility if volatility > 0 else 0.0
