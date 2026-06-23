"""Base strategy interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from ..analyzer import MarketSnapshot


def extract_prices(history: list[dict]) -> list[float]:
    """Extract valid (non-zero, finite) prices from a price history list."""
    prices = []
    for h in history:
        try:
            p = float(h.get("p", h.get("price", 0)))
        except (TypeError, ValueError):
            continue
        if p > 0 and p == p:  # exclude zero and NaN
            prices.append(p)
    return prices


def round_trip_cost_pct(market: "MarketSnapshot") -> float:
    """Approximate the round-trip transaction cost of a position as a percentage
    of the mid price. Entering and later exiting crosses the bid/ask spread, so
    the full spread is a conservative estimate of the cost that any directional
    edge must clear to be profitable. Returns infinity for a degenerate book."""
    if market.mid <= 0:
        return float("inf")
    return market.spread / market.mid * 100


@dataclass
class Signal:
    """Trading signal produced by a strategy."""

    market: MarketSnapshot
    side: str  # "BUY" or "SELL"
    token_id: str  # which token to trade
    confidence: float  # 0.0 to 1.0
    strategy_name: str
    target_price: Optional[float] = None
    reason: str = ""

    @property
    def is_strong(self) -> bool:
        return self.confidence >= 0.7

    def __repr__(self) -> str:
        return (
            f"Signal({self.strategy_name}: {self.side} "
            f"{self.market.question[:40]}... conf={self.confidence:.2f})"
        )

    @property
    def direction(self) -> str:
        """Which outcome this signal backs: 'YES' or 'NO'."""
        return "YES" if self.token_id == self.market.token_yes else "NO"


class BaseStrategy(ABC):
    """All strategies implement this interface."""

    name: str = "base"
    # Behavioural family, used by the regime-aware aggregator to suppress
    # contradictory signals: "trend" (trades with momentum), "counter" (trades
    # against extremes), or "neutral" (structural/microstructure edge).
    kind: str = "neutral"

    @abstractmethod
    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        """Evaluate a market and return a Signal if there's an opportunity."""
        ...
