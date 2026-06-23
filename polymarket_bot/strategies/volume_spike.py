"""Volume spike strategy - detects unusual volume that precedes big moves."""

import logging
from typing import Optional

from .base import BaseStrategy, Signal, extract_prices
from ..analyzer import MarketSnapshot

logger = logging.getLogger(__name__)


class VolumeSpikeStrategy(BaseStrategy):
    """
    Detects markets with unusual volume spikes and trades in the
    direction of the move.

    Logic:
    - Compare 24h volume to total volume to detect relative spikes
    - High recent volume + price movement = informed trading
    - Volume spike toward YES with price rising → strong BUY YES
    - Volume spike toward NO (YES price dropping) → BUY NO
    """

    name = "volume_spike"

    def __init__(self, volume_spike_ratio: float = 0.10, min_24h_volume: float = 5000):
        self.volume_spike_ratio = volume_spike_ratio
        self.min_24h_volume = min_24h_volume

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        if market.volume_24h < self.min_24h_volume:
            return None

        if market.total_volume <= 0:
            return None

        # Volume spike ratio: what fraction of all-time volume happened in last 24h
        spike_ratio = market.volume_24h / market.total_volume

        if spike_ratio < self.volume_spike_ratio:
            return None

        # Determine direction from price history
        prices = extract_prices(market.price_history)
        if len(prices) < 5:
            return None

        recent_avg = sum(prices[-5:]) / 5

        # Use the 5 prices immediately before the recent window as the baseline.
        # `prices[-15:-5]` (up to 10 prices) or whatever non-overlapping
        # history precedes prices[-5:] — always distinct from the recent window.
        n = len(prices)
        older = prices[max(0, n - 10) : n - 5]
        if not older:
            return None
        older_avg = sum(older) / len(older)

        if older_avg == 0:
            return None

        price_change = (recent_avg - older_avg) / older_avg

        # Need both volume spike AND price movement
        if abs(price_change) < 0.02:
            return None

        confidence = min(0.90, 0.55 + spike_ratio * 2 + abs(price_change) * 2)

        if price_change > 0:
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_yes,
                confidence=confidence,
                strategy_name=self.name,
                reason=(
                    f"Volume spike: 24h_vol=${market.volume_24h:,.0f} "
                    f"({spike_ratio:.1%} of total), price +{price_change:.1%}"
                ),
            )
        else:
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_no,
                confidence=confidence,
                strategy_name=self.name,
                reason=(
                    f"Volume spike: 24h_vol=${market.volume_24h:,.0f} "
                    f"({spike_ratio:.1%} of total), price {price_change:.1%}"
                ),
            )
