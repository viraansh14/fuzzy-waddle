"""Mean reversion strategy - bets on prices reverting to historical averages."""

import logging
import math
from typing import Optional

from .base import BaseStrategy, Signal, extract_prices
from ..analyzer import MarketSnapshot

logger = logging.getLogger(__name__)


class MeanReversionStrategy(BaseStrategy):
    """
    Trades against extreme moves, betting that prices revert to the mean.

    Logic:
    - Calculate z-score of current price vs recent history
    - If price is 2+ standard deviations above mean → expect pullback → BUY NO
    - If price is 2+ standard deviations below mean → expect bounce → BUY YES
    - Only fires when move happened rapidly (likely overreaction)
    - Avoids markets near resolution (where extreme prices are justified)
    """

    name = "mean_reversion"
    kind = "counter"

    def __init__(
        self,
        z_threshold: float = 1.8,
        lookback: int = 30,
        min_hours_to_resolution: float = 24.0,
    ):
        self.z_threshold = z_threshold
        self.lookback = lookback
        # Near resolution an extreme price is usually justified by real
        # information, so a reversion bet there is dangerous — skip it.
        self.min_hours_to_resolution = min_hours_to_resolution

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        # Honour the long-standing docstring promise: do not fade extremes when
        # the market is about to resolve.
        hours = market.hours_to_resolution
        if hours is not None and 0 <= hours < self.min_hours_to_resolution:
            return None

        prices = extract_prices(market.price_history)
        if len(prices) < self.lookback:
            return None

        lookback_prices = prices[-self.lookback:]
        current_price = prices[-1]

        n = len(lookback_prices)
        mean = sum(lookback_prices) / n
        # Sample variance (n-1) for an unbiased estimate of population variance.
        variance = sum((p - mean) ** 2 for p in lookback_prices) / (n - 1) if n > 1 else 0
        std = math.sqrt(variance) if variance > 0 else 0

        if std < 0.01:
            return None

        z_score = (current_price - mean) / std

        if abs(z_score) < self.z_threshold:
            return None

        # Check that move was rapid (happened in last few candles, not gradual)
        recent_prices = prices[-5:]
        recent_move = abs(recent_prices[-1] - recent_prices[0])
        total_move = abs(current_price - mean)

        if total_move == 0:
            return None

        recency_ratio = recent_move / total_move
        if recency_ratio < 0.4:
            # Move was gradual, not a spike — skip
            return None

        confidence = min(0.85, 0.5 + (abs(z_score) - self.z_threshold) * 0.2 + recency_ratio * 0.1)

        if z_score > 0:
            # Overbought → expect reversion down → BUY NO
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_no,
                confidence=confidence,
                strategy_name=self.name,
                reason=f"Overbought reversion: z={z_score:.2f}, price={current_price:.3f} vs mean={mean:.3f}",
            )
        else:
            # Oversold → expect reversion up → BUY YES
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_yes,
                confidence=confidence,
                strategy_name=self.name,
                reason=f"Oversold reversion: z={z_score:.2f}, price={current_price:.3f} vs mean={mean:.3f}",
            )
