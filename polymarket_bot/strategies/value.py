"""Value strategy - finds mispriced markets where odds don't add up."""

import logging
from typing import Optional

from .base import BaseStrategy, Signal
from ..analyzer import MarketSnapshot

logger = logging.getLogger(__name__)


class ValueStrategy(BaseStrategy):
    """
    Finds value by detecting mispricing between YES and NO tokens.

    Logic:
    - In a binary market, YES + NO should equal ~$1.00
    - When YES + NO < 1.00 (vig is negative) → arbitrage opportunity
    - When spread is wide, there's value buying the cheaper side
    - Also detects when a market's implied probability is extreme
      but not yet at 0.95+ (potential resolution play)
    """

    name = "value"

    def __init__(
        self,
        min_edge_pct: float = 3.0,
        resolution_threshold: float = 0.85,
        near_certain_cap: float = 0.95,
    ):
        self.min_edge_pct = min_edge_pct
        # Markets at/above this implied probability (or at/below its mirror) are
        # treated as strong favorites for a resolution play.
        self.resolution_threshold = resolution_threshold
        # ...but markets at/above this cap (or at/below its mirror) are too close
        # to certainty — little upside left — so the resolution play skips them.
        self.near_certain_cap = near_certain_cap

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        yes_price = market.outcome_prices.get("Yes", 0.5)
        no_price = market.outcome_prices.get("No", 0.5)

        total = yes_price + no_price

        # Look for arbitrage: total significantly below 1.0
        if total < 0.97:
            # Both sides are cheap — buy the one with better expected value
            edge = (1.0 - total) / total * 100
            if edge >= self.min_edge_pct:
                # Buy whichever side is closer to 0.5 (more uncertain = more upside)
                if abs(yes_price - 0.5) <= abs(no_price - 0.5):
                    return Signal(
                        market=market,
                        side="BUY",
                        token_id=market.token_yes,
                        confidence=min(0.9, 0.6 + edge / 100),
                        strategy_name=self.name,
                        reason=f"Negative vig arb: YES={yes_price:.3f} + NO={no_price:.3f} = {total:.3f} (edge={edge:.1f}%)",
                    )
                else:
                    return Signal(
                        market=market,
                        side="BUY",
                        token_id=market.token_no,
                        confidence=min(0.9, 0.6 + edge / 100),
                        strategy_name=self.name,
                        reason=f"Negative vig arb: YES={yes_price:.3f} + NO={no_price:.3f} = {total:.3f} (edge={edge:.1f}%)",
                    )

        # Wide spread value play: buy at bid when spread is juicy
        if market.spread >= 0.04:
            spread_edge = market.spread / market.mid * 100 if market.mid > 0 else 0
            if spread_edge >= self.min_edge_pct:
                # Buy YES at bid if mid < 0.5, buy NO at bid if mid > 0.5
                if market.mid < 0.50:
                    return Signal(
                        market=market,
                        side="BUY",
                        token_id=market.token_yes,
                        confidence=min(0.85, 0.5 + spread_edge / 50),
                        strategy_name=self.name,
                        target_price=market.bid + 0.01,
                        reason=f"Wide spread value: spread={market.spread:.3f} ({spread_edge:.1f}% edge), buying YES near bid",
                    )
                else:
                    return Signal(
                        market=market,
                        side="BUY",
                        token_id=market.token_no,
                        confidence=min(0.85, 0.5 + spread_edge / 50),
                        strategy_name=self.name,
                        target_price=1.0 - market.ask + 0.01,
                        reason=f"Wide spread value: spread={market.spread:.3f} ({spread_edge:.1f}% edge), buying NO near bid",
                    )

        # Resolution play: implied probability is extreme (a strong favorite)
        # but not yet near-certain. We look for the 0.85–0.95 band (and its
        # mirrored 0.05–0.15 band) and back the favorite to drift toward
        # resolution. The band is enforced directly on the implied probability
        # here — the analyzer's liquidity filter gates on orderbook ``mid``,
        # which can diverge from the Gamma ``Yes`` outcome price, so we cannot
        # rely on it to exclude near-certain markets.
        prob = market.implied_probability
        upper_threshold = self.resolution_threshold  # e.g. 0.85
        upper_cap = self.near_certain_cap            # e.g. 0.95
        lower_threshold = 1.0 - self.resolution_threshold  # e.g. 0.15
        lower_cap = 1.0 - self.near_certain_cap            # e.g. 0.05
        if upper_threshold <= prob < upper_cap:
            # YES is the strong favorite; remaining gap to certainty is the edge.
            edge = (1.0 - prob) * 100
            if edge >= self.min_edge_pct:
                return Signal(
                    market=market,
                    side="BUY",
                    token_id=market.token_yes,
                    confidence=min(0.88, 0.6 + (prob - upper_threshold)),
                    strategy_name=self.name,
                    reason=f"Resolution play: YES favored at {prob:.3f}, {edge:.1f}% gap to certainty",
                )
        elif lower_cap < prob <= lower_threshold:
            # NO is the strong favorite (YES implied prob is very low).
            edge = prob * 100
            if edge >= self.min_edge_pct:
                return Signal(
                    market=market,
                    side="BUY",
                    token_id=market.token_no,
                    confidence=min(0.88, 0.6 + (lower_threshold - prob)),
                    strategy_name=self.name,
                    reason=f"Resolution play: NO favored (YES at {prob:.3f}), {edge:.1f}% gap to certainty",
                )

        return None
