"""Resolution drift strategy - backs stable favorites as resolution nears."""

import logging
from typing import Optional

from .base import BaseStrategy, Signal, extract_prices
from ..analyzer import MarketSnapshot


def _clamp_price(value: float) -> float:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return 0.5
    if price != price:  # NaN
        return 0.5
    return max(0.0, min(1.0, price))


logger = logging.getLogger(__name__)


class ResolutionDriftStrategy(BaseStrategy):
    """
    As a market approaches resolution, a clear-but-not-extreme favorite that has
    been stable tends to converge toward 1.0. This strategy backs that drift.

    Logic:
    - Only fires inside a resolution window (end date known and within
      ``max_hours``, and not already past).
    - The favorite must sit in a moderate-strong band (``fav_low`` ..
      ``fav_high``). This is deliberately *below* ValueStrategy's resolution
      play (0.85-0.95) so the two don't compete for the same markets.
    - Recent price action must be calm (range <= ``max_volatility``); a favorite
      still swinging is not a confident convergence candidate.
    - Confidence grows as resolution nears and as the favorite is stronger.

    Time-based structural edge, so tagged "neutral".
    """

    name = "resolution_drift"
    kind = "neutral"

    def __init__(
        self,
        max_hours: float = 72.0,
        fav_low: float = 0.62,
        fav_high: float = 0.88,
        max_volatility: float = 0.06,
    ):
        if max_hours <= 0:
            raise ValueError("max_hours must be positive")
        if not 0.5 <= fav_low < fav_high <= 1.0:
            raise ValueError("require 0.5 <= fav_low < fav_high <= 1.0")
        if max_volatility <= 0:
            raise ValueError("max_volatility must be positive")
        self.max_hours = max_hours
        self.fav_low = fav_low
        self.fav_high = fav_high
        self.max_volatility = max_volatility

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        hours = market.hours_to_resolution
        if hours is None or hours <= 0 or hours > self.max_hours:
            return None

        yes_price = _clamp_price(market.outcome_prices.get("Yes", 0.5))
        no_price = _clamp_price(market.outcome_prices.get("No", 0.5))

        # Back whichever side is the favorite (higher price).
        if yes_price >= no_price:
            fav_price, token_id, label = yes_price, market.token_yes, "YES"
        else:
            fav_price, token_id, label = no_price, market.token_no, "NO"

        if not (self.fav_low <= fav_price <= self.fav_high):
            return None

        # Require calm recent price action: a wide range means the favorite is
        # not yet a confident convergence candidate.
        prices = extract_prices(market.price_history)
        if len(prices) >= 10:
            recent = prices[-10:]
            volatility = max(recent) - min(recent)
            if volatility > self.max_volatility:
                return None

        # Closer to resolution -> more conviction.
        time_factor = 1.0 - (hours / self.max_hours)
        confidence = min(0.85, 0.55 + (fav_price - 0.5) * 0.4 + time_factor * 0.2)

        return Signal(
            market=market,
            side="BUY",
            token_id=token_id,
            confidence=confidence,
            strategy_name=self.name,
            reason=(
                f"Resolution drift: {label} favorite at {fav_price:.2f}, "
                f"{hours:.0f}h to close"
            ),
        )
