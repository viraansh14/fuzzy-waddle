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

    def __init__(self, min_edge_pct: float = 3.0):
        self.min_edge_pct = min_edge_pct

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

        return None
