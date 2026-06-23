"""Tests for ValueStrategy: arbitrage, wide spread, and resolution play.

These pin down the behaviour fixed across several review rounds:
- resolution band enforced on each side's own outcome price
- NO play symmetric with YES
- when both sides qualify, the stronger favorite is backed
- near_certain_cap excludes prices at/above the cap
"""

import pytest

from polymarket_bot.strategies.value import ValueStrategy, _clamp_price

from .conftest import make_snapshot


# ── _clamp_price ────────────────────────────────────────────────────────

def test_clamp_price_in_range():
    assert _clamp_price(0.5) == 0.5


def test_clamp_price_bounds():
    assert _clamp_price(-1.0) == 0.0
    assert _clamp_price(2.0) == 1.0


def test_clamp_price_nan_defaults_to_half():
    assert _clamp_price(float("nan")) == 0.5


def test_clamp_price_unparseable_defaults_to_half():
    assert _clamp_price("abc") == 0.5


# ── constructor validation ──────────────────────────────────────────────

def test_invalid_thresholds_rejected():
    with pytest.raises(ValueError):
        ValueStrategy(resolution_threshold=0.95, near_certain_cap=0.85)


def test_negative_min_edge_rejected():
    with pytest.raises(ValueError):
        ValueStrategy(min_edge_pct=-1.0)


# ── resolution play ─────────────────────────────────────────────────────

def _resolution_strategy():
    # Disable the spread branch by keeping spread tiny in the snapshots.
    return ValueStrategy(min_edge_pct=3.0, resolution_threshold=0.85, near_certain_cap=0.95)


def test_resolution_backs_yes_in_band():
    strat = _resolution_strategy()
    market = make_snapshot(yes_price=0.90, no_price=0.10, spread=0.01, mid=0.90)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_yes
    assert "Resolution play" in sig.reason


def test_resolution_backs_no_in_band():
    strat = _resolution_strategy()
    market = make_snapshot(yes_price=0.10, no_price=0.90, spread=0.01, mid=0.10)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_no


def test_resolution_skips_at_or_above_cap():
    strat = _resolution_strategy()
    # NO at 0.96 is past the near_certain_cap -> no resolution signal.
    market = make_snapshot(yes_price=0.04, no_price=0.96, spread=0.01, mid=0.50)
    # mid kept at 0.50 so the snapshot isn't otherwise special.
    assert strat._check_resolution(market, 0.04, 0.96) is None


def test_resolution_skips_below_threshold():
    strat = _resolution_strategy()
    market = make_snapshot(spread=0.01)
    assert strat._check_resolution(market, 0.80, 0.20) is None


def test_resolution_dual_band_backs_stronger_favorite():
    # Both sides land in [0.85, 0.95) (prices sum > 1). NO is stronger.
    strat = _resolution_strategy()
    market = make_snapshot(token_yes="y", token_no="n", spread=0.01)
    sig = strat._check_resolution(market, yes_price=0.88, no_price=0.92)
    assert sig is not None
    assert sig.token_id == "n"


def test_resolution_dual_band_backs_yes_when_stronger():
    strat = _resolution_strategy()
    market = make_snapshot(token_yes="y", token_no="n", spread=0.01)
    sig = strat._check_resolution(market, yes_price=0.93, no_price=0.87)
    assert sig is not None
    assert sig.token_id == "y"


def test_resolution_edge_uses_own_price():
    # YES at 0.90 -> edge is (1 - 0.90)*100 = 10% gap to certainty.
    strat = _resolution_strategy()
    market = make_snapshot(token_yes="y", token_no="n", spread=0.01)
    sig = strat._check_resolution(market, yes_price=0.90, no_price=0.05)
    assert sig is not None
    assert "10.0%" in sig.reason


# ── arbitrage ───────────────────────────────────────────────────────────

def test_arbitrage_negative_vig():
    strat = ValueStrategy(min_edge_pct=3.0)
    # YES + NO = 0.90 -> ~11% edge, both underpriced.
    market = make_snapshot(yes_price=0.45, no_price=0.45, spread=0.01, mid=0.45)
    sig = strat._check_arbitrage(market, 0.45, 0.45)
    assert sig is not None
    assert "arb" in sig.reason.lower()


def test_arbitrage_skips_when_prices_sum_near_one():
    strat = ValueStrategy(min_edge_pct=3.0)
    assert strat._check_arbitrage(make_snapshot(), 0.50, 0.49) is None


# ── wide spread ─────────────────────────────────────────────────────────

def test_wide_spread_value_signal():
    strat = ValueStrategy(min_edge_pct=3.0)
    market = make_snapshot(spread=0.06, mid=0.40, bid=0.37, ask=0.43)
    sig = strat._check_wide_spread(market)
    assert sig is not None
    assert sig.target_price is not None


def test_wide_spread_skips_tight_book():
    strat = ValueStrategy(min_edge_pct=3.0)
    assert strat._check_wide_spread(make_snapshot(spread=0.01, mid=0.5)) is None
