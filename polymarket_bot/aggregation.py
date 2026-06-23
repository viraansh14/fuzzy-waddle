"""Regime-aware signal aggregation.

The strategies can disagree on the same market: momentum (trend-following) and
mean reversion (counter-trend) will routinely fire opposite-direction signals.
Naively picking the highest-confidence signal per market ignores that the two
contradict each other and that each only has edge in a particular regime.

This module:
1. Classifies each market's regime (trend up / trend down / range) from its
   price history.
2. Suppresses signals whose behavioural ``kind`` is wrong for that regime
   (trend-followers in a range, counter-trend bets in a strong trend).
3. Resolves any remaining directional conflict per market, backing the side
   with greater aggregate confidence and penalising it for the disagreement.
"""

import logging
from collections import defaultdict
from dataclasses import replace

from .strategies.base import Signal, extract_prices

logger = logging.getLogger(__name__)

# A short-vs-long MA gap beyond this (percent) marks a trending regime.
TREND_THRESHOLD_PCT = 4.0
# If the two sides' aggregate confidence are within this, the market is a
# genuine coin-flip and we take no position.
CONFLICT_EPSILON = 0.05


def detect_regime(price_history: list[dict], short: int = 6, long: int = 20) -> str:
    """Classify a market as 'trend_up', 'trend_down', or 'range'.

    Falls back to 'range' (the neutral default) when there isn't enough history
    to judge a trend."""
    prices = extract_prices(price_history)
    if len(prices) < long:
        return "range"

    short_ma = sum(prices[-short:]) / short
    long_ma = sum(prices[-long:]) / long
    if long_ma == 0:
        return "range"

    momentum = (short_ma - long_ma) / long_ma * 100
    if momentum >= TREND_THRESHOLD_PCT:
        return "trend_up"
    if momentum <= -TREND_THRESHOLD_PCT:
        return "trend_down"
    return "range"


def regime_allows(regime: str, kind: str) -> bool:
    """Whether a signal of the given behavioural kind has edge in this regime."""
    if kind == "neutral":
        return True
    if kind == "trend":
        # Trend-following is noise in a choppy/range market.
        return regime in ("trend_up", "trend_down")
    if kind == "counter":
        # Don't fight a strong trend; mean reversion belongs in a range.
        return regime == "range"
    # Unknown kind: be permissive rather than silently dropping signals.
    return True


def _resolve_conflict(signals: list[Signal]) -> Signal | None:
    """Pick a single signal for one market from a regime-filtered list.

    If every signal agrees on direction, return the most confident. If the sides
    disagree, back the direction with greater aggregate confidence, scaling the
    winner down by the strength of the opposition; if the two sides are too
    close, return None (no clear edge)."""
    yes = [s for s in signals if s.direction == "YES"]
    no = [s for s in signals if s.direction == "NO"]

    if not no:
        return max(yes, key=lambda s: s.confidence)
    if not yes:
        return max(no, key=lambda s: s.confidence)

    yes_strength = sum(s.confidence for s in yes)
    no_strength = sum(s.confidence for s in no)
    if abs(yes_strength - no_strength) < CONFLICT_EPSILON:
        return None  # genuine disagreement -> stand aside

    if yes_strength > no_strength:
        winners, win_strength, lose_strength = yes, yes_strength, no_strength
    else:
        winners, win_strength, lose_strength = no, no_strength, yes_strength

    best = max(winners, key=lambda s: s.confidence)
    penalty = lose_strength / (win_strength + lose_strength)
    adjusted = best.confidence * (1.0 - penalty)
    return replace(best, confidence=adjusted)


def aggregate_signals(
    signals: list[Signal],
    kind_by_strategy: dict[str, str] | None = None,
    max_signals: int = 5,
) -> list[Signal]:
    """Reduce raw strategy signals to at most one per market, regime-filtered and
    conflict-resolved, sorted by (adjusted) confidence descending.

    ``kind_by_strategy`` maps a strategy's ``name`` to its behavioural ``kind``;
    any strategy absent from the map (or a None map) is treated as "neutral"."""
    kind_by_strategy = kind_by_strategy or {}

    by_market: dict[str, list[Signal]] = defaultdict(list)
    for sig in signals:
        by_market[sig.market.condition_id].append(sig)

    def kind_of(sig: Signal) -> str:
        return kind_by_strategy.get(sig.strategy_name, "neutral")

    chosen: list[Signal] = []
    for market_signals in by_market.values():
        regime = detect_regime(market_signals[0].market.price_history)
        eligible = [s for s in market_signals if regime_allows(regime, kind_of(s))]
        if not eligible:
            continue
        best = _resolve_conflict(eligible)
        if best is not None:
            chosen.append(best)

    chosen.sort(key=lambda s: s.confidence, reverse=True)
    return chosen[:max_signals]
