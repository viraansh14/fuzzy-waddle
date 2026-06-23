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


class BaseStrategy(ABC):
    """All strategies implement this interface."""

    name: str = "base"

    @abstractmethod
    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        """Evaluate a market and return a Signal if there's an opportunity."""
        ...
