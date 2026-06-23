"""Tests for regime detection and regime-aware signal aggregation."""

import pytest

from polymarket_bot.aggregation import (
    aggregate_signals,
    detect_regime,
    regime_allows,
)
from polymarket_bot.strategies.base import Signal

from .conftest import make_snapshot, price_series


def _sig(market, token_id, confidence, strategy_name="s"):
    return Signal(
        market=market,
        side="BUY",
        token_id=token_id,
        confidence=confidence,
        strategy_name=strategy_name,
    )


# ── detect_regime ───────────────────────────────────────────────────────

def test_detect_regime_trend_up():
    rising = [0.30 + 0.01 * i for i in range(25)]
    assert detect_regime(price_series(rising)) == "trend_up"


def test_detect_regime_trend_down():
    falling = [0.80 - 0.01 * i for i in range(25)]
    assert detect_regime(price_series(falling)) == "trend_down"


def test_detect_regime_range_when_flat():
    flat = [0.50] * 25
    assert detect_regime(price_series(flat)) == "range"


def test_detect_regime_range_when_insufficient_history():
    assert detect_regime(price_series([0.5] * 5)) == "range"


# ── regime_allows ───────────────────────────────────────────────────────

def test_neutral_allowed_in_any_regime():
    for regime in ("trend_up", "trend_down", "range"):
        assert regime_allows(regime, "neutral") is True


def test_trend_suppressed_in_range():
    assert regime_allows("range", "trend") is False
    assert regime_allows("trend_up", "trend") is True
    assert regime_allows("trend_down", "trend") is True


def test_counter_only_in_range():
    assert regime_allows("range", "counter") is True
    assert regime_allows("trend_up", "counter") is False
    assert regime_allows("trend_down", "counter") is False


def test_unknown_kind_permissive():
    assert regime_allows("range", "mystery") is True


# ── aggregate_signals ───────────────────────────────────────────────────

def test_aggregate_dedupes_to_one_per_market():
    market = make_snapshot(price_history=price_series([0.5] * 25))
    sigs = [
        _sig(market, market.token_yes, 0.7, "value"),
        _sig(market, market.token_yes, 0.8, "orderbook_imbalance"),
    ]
    out = aggregate_signals(sigs, kind_by_strategy={"value": "neutral", "orderbook_imbalance": "neutral"})
    assert len(out) == 1
    assert out[0].confidence == 0.8  # highest of the agreeing signals


def test_aggregate_suppresses_trend_in_range():
    # Flat market => range regime => trend-following signal is dropped.
    market = make_snapshot(price_history=price_series([0.5] * 25))
    sigs = [_sig(market, market.token_yes, 0.9, "momentum")]
    out = aggregate_signals(sigs, kind_by_strategy={"momentum": "trend"})
    assert out == []


def test_aggregate_keeps_counter_in_range():
    market = make_snapshot(price_history=price_series([0.5] * 25))
    sigs = [_sig(market, market.token_no, 0.8, "mean_reversion")]
    out = aggregate_signals(sigs, kind_by_strategy={"mean_reversion": "counter"})
    assert len(out) == 1


def test_aggregate_suppresses_counter_in_trend():
    rising = [0.30 + 0.01 * i for i in range(25)]
    market = make_snapshot(price_history=price_series(rising))
    sigs = [_sig(market, market.token_no, 0.8, "mean_reversion")]
    out = aggregate_signals(sigs, kind_by_strategy={"mean_reversion": "counter"})
    assert out == []


def test_conflict_backs_stronger_side_with_penalty():
    # Two neutral signals disagree on direction; YES side is stronger.
    market = make_snapshot(price_history=price_series([0.5] * 25))
    sigs = [
        _sig(market, market.token_yes, 0.9, "a"),
        _sig(market, market.token_no, 0.6, "b"),
    ]
    out = aggregate_signals(sigs, kind_by_strategy={"a": "neutral", "b": "neutral"})
    assert len(out) == 1
    winner = out[0]
    assert winner.direction == "YES"
    # Penalty = 0.6 / (0.9 + 0.6) = 0.4 -> 0.9 * 0.6 = 0.54
    assert winner.confidence == pytest.approx(0.54)


def test_conflict_too_close_stands_aside():
    market = make_snapshot(price_history=price_series([0.5] * 25))
    sigs = [
        _sig(market, market.token_yes, 0.70, "a"),
        _sig(market, market.token_no, 0.68, "b"),
    ]
    out = aggregate_signals(sigs, kind_by_strategy={"a": "neutral", "b": "neutral"})
    assert out == []  # within CONFLICT_EPSILON -> no position


def test_aggregate_respects_max_signals():
    sigs = []
    kinds = {}
    for i in range(8):
        m = make_snapshot(condition_id=f"c{i}", price_history=price_series([0.5] * 25))
        sigs.append(_sig(m, m.token_yes, 0.6 + i * 0.01, "value"))
    kinds["value"] = "neutral"
    out = aggregate_signals(sigs, kind_by_strategy=kinds, max_signals=5)
    assert len(out) == 5
    # Sorted by confidence descending.
    confs = [s.confidence for s in out]
    assert confs == sorted(confs, reverse=True)


def test_unmapped_strategy_treated_as_neutral():
    market = make_snapshot(price_history=price_series([0.5] * 25))
    sigs = [_sig(market, market.token_yes, 0.7, "unknown")]
    # No kind map at all -> neutral -> kept even in range regime.
    out = aggregate_signals(sigs)
    assert len(out) == 1
