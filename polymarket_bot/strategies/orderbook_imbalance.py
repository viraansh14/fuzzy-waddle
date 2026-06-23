"""Order-book imbalance strategy - reads resting liquidity for short-term edge."""

import logging
from typing import Optional

from .base import BaseStrategy, Signal
from ..analyzer import MarketSnapshot

logger = logging.getLogger(__name__)


class OrderBookImbalanceStrategy(BaseStrategy):
    """
    Trades on the imbalance between resting bid and ask liquidity.

    Logic:
    - A book stacked with bids (buyers) relative to asks signals support and
      upward pressure on the YES token -> BUY YES.
    - A book stacked with asks (sellers) signals downward pressure on YES,
      i.e. the market leans toward NO -> BUY NO.
    - Requires a minimum total book depth so the imbalance is meaningful, and
      skips near-resolution extremes where the book is thin and noisy.

    This is a structural/microstructure edge, so it is tagged "neutral": it is
    not suppressed by the trend/range regime filter.
    """

    name = "orderbook_imbalance"
    kind = "neutral"

    def __init__(self, min_imbalance: float = 0.30, min_book_liquidity: float = 2000.0):
        if not 0.0 < min_imbalance <= 1.0:
            raise ValueError("min_imbalance must be in (0, 1]")
        if min_book_liquidity < 0:
            raise ValueError("min_book_liquidity must be non-negative")
        self.min_imbalance = min_imbalance
        self.min_book_liquidity = min_book_liquidity

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        bid_liq = market.bid_liquidity
        ask_liq = market.ask_liquidity
        total = bid_liq + ask_liq

        if total < self.min_book_liquidity or total <= 0:
            return None

        # Skip near-resolution extremes: books there are thin and one-sided for
        # reasons unrelated to short-term pressure.
        if market.mid <= 0.05 or market.mid >= 0.95:
            return None

        imbalance = (bid_liq - ask_liq) / total
        if abs(imbalance) < self.min_imbalance:
            return None

        confidence = min(0.85, 0.55 + abs(imbalance) * 0.4)

        if imbalance > 0:
            token_id, label = market.token_yes, "YES"
        else:
            token_id, label = market.token_no, "NO"

        return Signal(
            market=market,
            side="BUY",
            token_id=token_id,
            confidence=confidence,
            strategy_name=self.name,
            reason=(
                f"Book imbalance {imbalance:+.0%} "
                f"(bid_liq=${bid_liq:,.0f} vs ask_liq=${ask_liq:,.0f}) -> {label}"
            ),
        )
