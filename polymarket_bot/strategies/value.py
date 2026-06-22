"""Value strategy - finds mispriced markets where odds don't add up."""

import logging
from typing import Optional

from .base import BaseStrategy, Signal
from ..analyzer import MarketSnapshot

logger = logging.getLogger(__name__)


def _clamp_price(value: float) -> float:
    """Coerce a (possibly malformed) outcome price into the valid [0, 1] range."""
    try:
        price = float(value)
    except (TypeError, ValueError):
        return 0.5
    if price != price:  # NaN
        return 0.5
    return max(0.0, min(1.0, price))


class ValueStrategy(BaseStrategy):
    """
    Finds value by detecting mispricing between YES and NO tokens.

    Logic (checked in priority order, first match wins):
    1. Negative vig arbitrage -- YES + NO < ~1.00 means both sides are
       collectively underpriced; buy the more uncertain side.
    2. Wide spread value -- a juicy bid/ask spread lets us buy near the bid.
    3. Resolution play -- one side is a strong favorite (priced in the
       0.85-0.95 band) but not yet near-certain; back it to drift to 1.00.
    """

    name = "value"

    def __init__(
        self,
        min_edge_pct: float = 3.0,
        resolution_threshold: float = 0.85,
        near_certain_cap: float = 0.95,
    ):
        if min_edge_pct < 0:
            raise ValueError("min_edge_pct must be non-negative")
        if not 0.0 < resolution_threshold < near_certain_cap <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0 < resolution_threshold < "
                f"near_certain_cap <= 1.0 (got {resolution_threshold} and "
                f"{near_certain_cap})"
            )
        self.min_edge_pct = min_edge_pct
        # A side priced at/above this is a strong favorite worth a resolution play...
        self.resolution_threshold = resolution_threshold
        # ...but at/above this cap it is too close to certainty (little upside
        # left), so the resolution play skips it.
        self.near_certain_cap = near_certain_cap

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        yes_price = _clamp_price(market.outcome_prices.get("Yes", 0.5))
        no_price = _clamp_price(market.outcome_prices.get("No", 0.5))

        return (
            self._check_arbitrage(market, yes_price, no_price)
            or self._check_wide_spread(market)
            or self._check_resolution(market, yes_price, no_price)
        )

    def _signal(
        self,
        market: MarketSnapshot,
        token_id: str,
        confidence: float,
        reason: str,
        target_price: Optional[float] = None,
    ) -> Signal:
        """Build a BUY signal, clamping confidence into [0, 1]."""
        return Signal(
            market=market,
            side="BUY",
            token_id=token_id,
            confidence=max(0.0, min(1.0, confidence)),
            strategy_name=self.name,
            target_price=target_price,
            reason=reason,
        )

    def _check_arbitrage(
        self, market: MarketSnapshot, yes_price: float, no_price: float
    ) -> Optional[Signal]:
        """Negative-vig arbitrage: YES + NO meaningfully below 1.00."""
        total = yes_price + no_price
        if total <= 0 or total >= 0.97:
            return None

        edge = (1.0 - total) / total * 100
        if edge < self.min_edge_pct:
            return None

        # Buy whichever side is closer to 0.5 (more uncertain = more upside).
        if abs(yes_price - 0.5) <= abs(no_price - 0.5):
            token_id = market.token_yes
        else:
            token_id = market.token_no
        return self._signal(
            market,
            token_id,
            confidence=min(0.9, 0.6 + edge / 100),
            reason=(
                f"Negative vig arb: YES={yes_price:.3f} + NO={no_price:.3f} "
                f"= {total:.3f} (edge={edge:.1f}%)"
            ),
        )

    def _check_wide_spread(self, market: MarketSnapshot) -> Optional[Signal]:
        """Wide bid/ask spread: buy the cheaper side near the bid."""
        if market.spread < 0.04 or market.mid <= 0:
            return None

        spread_edge = market.spread / market.mid * 100
        if spread_edge < self.min_edge_pct:
            return None

        confidence = min(0.85, 0.5 + spread_edge / 50)
        # Buy YES near bid if mid < 0.5, otherwise buy NO near bid.
        if market.mid < 0.50:
            return self._signal(
                market,
                market.token_yes,
                confidence=confidence,
                target_price=market.bid + 0.01,
                reason=(
                    f"Wide spread value: spread={market.spread:.3f} "
                    f"({spread_edge:.1f}% edge), buying YES near bid"
                ),
            )
        return self._signal(
            market,
            market.token_no,
            confidence=confidence,
            target_price=1.0 - market.ask + 0.01,
            reason=(
                f"Wide spread value: spread={market.spread:.3f} "
                f"({spread_edge:.1f}% edge), buying NO near bid"
            ),
        )

    def _check_resolution(
        self, market: MarketSnapshot, yes_price: float, no_price: float
    ) -> Optional[Signal]:
        """
        Resolution play: a side is a strong favorite (priced in the
        [resolution_threshold, near_certain_cap) band) but not yet near-certain.

        Each side is gated and sized on its *own* outcome price -- YES and NO
        prices need not sum to 1.0 -- so the gap-to-certainty edge and the
        near-certainty cap always reference the token actually being bought.
        The band is enforced directly on the outcome price rather than the
        analyzer's orderbook ``mid``, which can diverge from the outcome prices.
        """
        threshold = self.resolution_threshold
        cap = self.near_certain_cap

        # Collect every side that qualifies. Both can land in the band when the
        # outcome prices sum well above 1.0, so back the *stronger* favorite
        # (highest price) rather than whichever happens to be checked first.
        candidates = []
        if threshold <= yes_price < cap:
            candidates.append((yes_price, market.token_yes, "YES"))
        if threshold <= no_price < cap:
            candidates.append((no_price, market.token_no, "NO"))
        if not candidates:
            return None

        favorite, token_id, side_label = max(candidates, key=lambda c: c[0])

        edge = (1.0 - favorite) * 100
        if edge < self.min_edge_pct:
            return None

        return self._signal(
            market,
            token_id,
            confidence=min(0.88, 0.6 + (favorite - threshold)),
            reason=(
                f"Resolution play: {side_label} favored at {favorite:.3f}, "
                f"{edge:.1f}% gap to certainty"
            ),
        )
