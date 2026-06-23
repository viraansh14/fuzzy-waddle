"""Momentum strategy - trades in the direction of recent price movement."""

import logging
from typing import Optional

from .base import BaseStrategy, Signal, extract_prices, round_trip_cost_pct
from ..analyzer import MarketSnapshot

logger = logging.getLogger(__name__)


class MomentumStrategy(BaseStrategy):
    """
    Detects strong price trends and trades with the momentum.

    Logic:
    - Looks at recent price history (last N candles)
    - Calculates short-term vs medium-term moving averages
    - If short MA crosses above long MA with volume confirmation → BUY YES
    - If short MA crosses below long MA → BUY NO (bet against)
    - Higher confidence when move is sustained and accelerating
    - The trend must clear the round-trip spread cost to be worth taking.
    """

    name = "momentum"
    kind = "trend"

    def __init__(self, short_window: int = 6, long_window: int = 20, min_move_pct: float = 5.0):
        self.short_window = short_window
        self.long_window = long_window
        self.min_move_pct = min_move_pct

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        prices = extract_prices(market.price_history)
        if len(prices) < self.long_window + 5:
            return None

        recent = prices[-self.short_window:]
        medium = prices[-self.long_window:]

        short_ma = sum(recent) / len(recent)
        long_ma = sum(medium) / len(medium)

        if long_ma == 0:
            return None

        momentum = (short_ma - long_ma) / long_ma * 100

        if abs(momentum) < self.min_move_pct:
            return None

        # The move has to be worth more than the round-trip spread cost,
        # otherwise the trade is negative expectancy before it even starts.
        if abs(momentum) <= round_trip_cost_pct(market):
            return None

        # Check if the last 3 prices are accelerating in the SAME direction
        # as the overall momentum (not just any monotone run).
        last_3 = prices[-3:]
        if momentum > 0:
            accelerating = last_3[2] > last_3[1] > last_3[0]
        else:
            accelerating = last_3[2] < last_3[1] < last_3[0]

        # Calculate rate of change for confidence
        roc = abs(momentum) / 100
        confidence = min(0.95, 0.5 + roc + (0.15 if accelerating else 0))

        if momentum > 0:
            # Price trending up → buy YES
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_yes,
                confidence=confidence,
                strategy_name=self.name,
                reason=f"Bullish momentum: short_ma={short_ma:.3f} > long_ma={long_ma:.3f} ({momentum:+.1f}%)",
            )
        else:
            # Price trending down → buy NO
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_no,
                confidence=confidence,
                strategy_name=self.name,
                reason=f"Bearish momentum: short_ma={short_ma:.3f} < long_ma={long_ma:.3f} ({momentum:+.1f}%)",
            )
