"""Tests for OrderBookImbalanceStrategy and ResolutionDriftStrategy, plus the
MarketSnapshot.hours_to_resolution property and round_trip_cost_pct helper."""

from datetime import datetime, timedelta, timezone

import pytest

from polymarket_bot.strategies.base import round_trip_cost_pct
from polymarket_bot.strategies.orderbook_imbalance import OrderBookImbalanceStrategy
from polymarket_bot.strategies.resolution_drift import ResolutionDriftStrategy

from .conftest import make_snapshot, price_series


def _iso_in(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# ── hours_to_resolution ─────────────────────────────────────────────────

def test_hours_to_resolution_future():
    market = make_snapshot(end_date=_iso_in(10))
    hrs = market.hours_to_resolution
    assert hrs is not None
    assert 9.9 < hrs < 10.1


def test_hours_to_resolution_handles_z_suffix():
    market = make_snapshot(end_date="2999-01-01T00:00:00Z")
    assert market.hours_to_resolution is not None
    assert market.hours_to_resolution > 0


def test_hours_to_resolution_none_when_missing():
    assert make_snapshot(end_date=None).hours_to_resolution is None


def test_hours_to_resolution_none_when_unparseable():
    assert make_snapshot(end_date="not-a-date").hours_to_resolution is None


def test_hours_to_resolution_negative_when_past():
    market = make_snapshot(end_date=_iso_in(-5))
    assert market.hours_to_resolution < 0


# ── round_trip_cost_pct ─────────────────────────────────────────────────

def test_round_trip_cost_pct():
    market = make_snapshot(spread=0.04, mid=0.50)
    assert round_trip_cost_pct(market) == pytest.approx(8.0)


def test_round_trip_cost_pct_degenerate_book():
    market = make_snapshot(spread=0.04, mid=0.0)
    assert round_trip_cost_pct(market) == float("inf")


# ── OrderBookImbalanceStrategy ──────────────────────────────────────────

def test_imbalance_bid_heavy_buys_yes():
    strat = OrderBookImbalanceStrategy(min_imbalance=0.30, min_book_liquidity=2000)
    market = make_snapshot(bid_liquidity=8000, ask_liquidity=2000, mid=0.5)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_yes


def test_imbalance_ask_heavy_buys_no():
    strat = OrderBookImbalanceStrategy(min_imbalance=0.30, min_book_liquidity=2000)
    market = make_snapshot(bid_liquidity=2000, ask_liquidity=8000, mid=0.5)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_no


def test_imbalance_below_threshold_no_signal():
    strat = OrderBookImbalanceStrategy(min_imbalance=0.30, min_book_liquidity=2000)
    market = make_snapshot(bid_liquidity=5200, ask_liquidity=4800, mid=0.5)
    assert strat.evaluate(market) is None


def test_imbalance_thin_book_no_signal():
    strat = OrderBookImbalanceStrategy(min_imbalance=0.30, min_book_liquidity=2000)
    market = make_snapshot(bid_liquidity=900, ask_liquidity=100, mid=0.5)
    assert strat.evaluate(market) is None


def test_imbalance_extreme_price_skipped():
    strat = OrderBookImbalanceStrategy(min_imbalance=0.30, min_book_liquidity=2000)
    market = make_snapshot(bid_liquidity=8000, ask_liquidity=2000, mid=0.97)
    assert strat.evaluate(market) is None


def test_imbalance_invalid_params():
    with pytest.raises(ValueError):
        OrderBookImbalanceStrategy(min_imbalance=0)
    with pytest.raises(ValueError):
        OrderBookImbalanceStrategy(min_imbalance=1.5)
    with pytest.raises(ValueError):
        OrderBookImbalanceStrategy(min_book_liquidity=-1)


# ── ResolutionDriftStrategy ─────────────────────────────────────────────

def test_resolution_drift_backs_calm_favorite():
    strat = ResolutionDriftStrategy(max_hours=72.0)
    market = make_snapshot(
        yes_price=0.75, no_price=0.25,
        end_date=_iso_in(12),
        price_history=price_series([0.74, 0.75, 0.75, 0.76, 0.75,
                                    0.75, 0.74, 0.75, 0.76, 0.75]),
    )
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_yes


def test_resolution_drift_backs_no_favorite():
    strat = ResolutionDriftStrategy(max_hours=72.0)
    market = make_snapshot(
        yes_price=0.25, no_price=0.75,
        end_date=_iso_in(12),
        price_history=price_series([0.25] * 10),
    )
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_no


def test_resolution_drift_skips_far_from_resolution():
    strat = ResolutionDriftStrategy(max_hours=72.0)
    market = make_snapshot(yes_price=0.75, no_price=0.25, end_date=_iso_in(200))
    assert strat.evaluate(market) is None


def test_resolution_drift_skips_no_end_date():
    strat = ResolutionDriftStrategy(max_hours=72.0)
    market = make_snapshot(yes_price=0.75, no_price=0.25, end_date=None)
    assert strat.evaluate(market) is None


def test_resolution_drift_skips_extreme_favorite():
    # 0.92 is above fav_high (0.88) -> leave it to the value resolution play.
    strat = ResolutionDriftStrategy(max_hours=72.0)
    market = make_snapshot(yes_price=0.92, no_price=0.08, end_date=_iso_in(12),
                           price_history=price_series([0.92] * 10))
    assert strat.evaluate(market) is None


def test_resolution_drift_skips_volatile_favorite():
    strat = ResolutionDriftStrategy(max_hours=72.0, max_volatility=0.06)
    # Favorite price 0.75 but a wide recent range (0.60..0.85) -> too volatile.
    history = price_series([0.60, 0.85, 0.62, 0.84, 0.63, 0.85, 0.61, 0.84, 0.70, 0.75])
    market = make_snapshot(yes_price=0.75, no_price=0.25, end_date=_iso_in(12),
                           price_history=history)
    assert strat.evaluate(market) is None


def test_resolution_drift_closer_means_higher_confidence():
    strat = ResolutionDriftStrategy(max_hours=72.0)
    hist = price_series([0.75] * 10)
    near = make_snapshot(yes_price=0.75, no_price=0.25, end_date=_iso_in(6), price_history=hist)
    far = make_snapshot(yes_price=0.75, no_price=0.25, end_date=_iso_in(60), price_history=hist)
    assert strat.evaluate(near).confidence > strat.evaluate(far).confidence


def test_resolution_drift_invalid_params():
    with pytest.raises(ValueError):
        ResolutionDriftStrategy(max_hours=0)
    with pytest.raises(ValueError):
        ResolutionDriftStrategy(fav_low=0.9, fav_high=0.8)
    with pytest.raises(ValueError):
        ResolutionDriftStrategy(max_volatility=0)
