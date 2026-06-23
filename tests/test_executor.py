"""Tests for ExecutionEngine cancel_stale_orders and fill parsing.

Covers the behaviour hardened across review rounds:
- only aged limit orders (non-empty, non-dry-run order_id) are eligible
- zero fill -> position fully removed, capital released
- partial fill -> position shrunk, only unfilled capital released
- partial fill capped to recorded size (no negative unfilled_cost)
- unknown fill -> order_id cleared (no infinite retry), position kept
- SELL exit sizes the order in shares, not USDC
"""

import time

import pytest

from polymarket_bot.executor import ExecutionEngine, _parse_filled_size
from polymarket_bot.risk_manager import RiskManager
from polymarket_bot.strategies.base import Signal

from .conftest import FakeClient, make_config, make_snapshot


# ── _parse_filled_size ──────────────────────────────────────────────────

def test_parse_filled_size_variants():
    assert _parse_filled_size({"sizeMatched": "5"}) == 5.0
    assert _parse_filled_size({"size_matched": 7}) == 7.0
    assert _parse_filled_size({"matchedAmount": 3.5}) == 3.5
    assert _parse_filled_size({"filled": "2"}) == 2.0


def test_parse_filled_size_missing_returns_none():
    assert _parse_filled_size({"foo": 1}) is None
    assert _parse_filled_size({}) is None


def test_parse_filled_size_unparseable_returns_none():
    assert _parse_filled_size({"sizeMatched": "not-a-number"}) is None


# ── helpers ─────────────────────────────────────────────────────────────

def _engine_with_position(client, *, order_id="ord-1", age_seconds=300, cost=50.0,
                          size=100.0, order_type="limit"):
    cfg = make_config()
    risk = RiskManager(cfg)
    engine = ExecutionEngine(client, risk, cfg)
    market = make_snapshot()
    sig = Signal(market=market, side="BUY", token_id="tok-yes",
                 confidence=0.8, strategy_name="value")
    risk.record_entry(sig, fill_price=0.5, size=size, cost=cost,
                      order_id=order_id, order_type=order_type)
    risk.positions["tok-yes"].entry_time = time.time() - age_seconds
    return engine, risk


# ── cancel_stale_orders: eligibility ────────────────────────────────────

def test_fresh_order_not_cancelled():
    client = FakeClient(cancel_response={"sizeMatched": 0})
    engine, risk = _engine_with_position(client, age_seconds=10)
    engine.cancel_stale_orders(min_age_seconds=120)
    assert client.cancelled_orders == []
    assert "tok-yes" in risk.positions


def test_dry_run_order_not_cancelled():
    client = FakeClient(cancel_response={"sizeMatched": 0})
    engine, risk = _engine_with_position(client, order_id="dry-run")
    engine.cancel_stale_orders(min_age_seconds=120)
    assert client.cancelled_orders == []


def test_market_order_position_not_cancelled():
    # A filled FOK market entry carries an order_id but must never be retried
    # for cancellation by cancel_stale_orders.
    client = FakeClient(cancel_response={"sizeMatched": 0})
    engine, risk = _engine_with_position(client, order_id="mkt-1", order_type="market")
    count = engine.cancel_stale_orders(min_age_seconds=120)
    assert count == 0
    assert client.cancelled_orders == []
    assert "tok-yes" in risk.positions  # position preserved


def test_dry_run_config_short_circuits():
    client = FakeClient(cancel_response={"sizeMatched": 0})
    cfg = make_config(dry_run=True)
    risk = RiskManager(cfg)
    engine = ExecutionEngine(client, risk, cfg)
    assert engine.cancel_stale_orders(min_age_seconds=0) == 0


# ── cancel_stale_orders: zero fill ──────────────────────────────────────

def test_zero_fill_removes_position_and_frees_capital():
    client = FakeClient(cancel_response={"sizeMatched": 0})
    engine, risk = _engine_with_position(client, cost=50.0)
    count = engine.cancel_stale_orders(min_age_seconds=120)
    assert count == 1
    assert "tok-yes" not in risk.positions
    assert risk.total_invested == pytest.approx(0.0)
    assert client.cancelled_orders == ["ord-1"]


# ── cancel_stale_orders: partial fill ───────────────────────────────────

def test_partial_fill_shrinks_position_and_releases_unfilled():
    # 100 shares ordered at 0.5 (cost 50). 40 shares filled.
    client = FakeClient(cancel_response={"sizeMatched": 40})
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    engine.cancel_stale_orders(min_age_seconds=120)

    pos = risk.positions["tok-yes"]
    assert pos.size == pytest.approx(40.0)
    assert pos.cost_basis == pytest.approx(20.0)  # 40 * 0.5
    assert pos.order_id == ""  # cleared so it isn't re-evaluated
    # Released the unfilled 30.0 (50 - 20), keeping 20 reserved.
    assert risk.total_invested == pytest.approx(20.0)


def test_partial_fill_uses_order_status_fallback():
    # Cancel response has no fill info; get_order supplies it.
    client = FakeClient(cancel_response={}, order_status={"sizeMatched": 25})
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    engine.cancel_stale_orders(min_age_seconds=120)

    assert client.get_order_calls == ["ord-1"]
    pos = risk.positions["tok-yes"]
    assert pos.size == pytest.approx(25.0)


def test_filled_size_capped_to_recorded_size():
    # API over-reports 150 filled on a 100-share order. Must cap to 100,
    # keeping unfilled_cost >= 0 and total_invested non-negative.
    client = FakeClient(cancel_response={"sizeMatched": 150})
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    engine.cancel_stale_orders(min_age_seconds=120)

    pos = risk.positions["tok-yes"]
    assert pos.size == pytest.approx(100.0)
    assert pos.cost_basis == pytest.approx(50.0)
    assert risk.total_invested == pytest.approx(50.0)  # nothing over-released


# ── cancel_stale_orders: unknown fill ───────────────────────────────────

def test_unknown_fill_assumes_zero_and_frees_capital():
    # Neither cancel response nor get_order yields a parseable fill. A stale
    # GTC limit is overwhelmingly likely unfilled, so assume zero fill: remove
    # the position and release its reserved capital rather than locking it.
    client = FakeClient(cancel_response={}, get_order_raises=True)
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    count = engine.cancel_stale_orders(min_age_seconds=120)

    assert count == 1
    assert "tok-yes" not in risk.positions  # phantom removed
    assert risk.total_invested == pytest.approx(0.0)  # capital freed


def test_cancel_failure_leaves_position_untouched():
    client = FakeClient(cancel_raises=True)
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    count = engine.cancel_stale_orders(min_age_seconds=120)
    assert count == 0
    pos = risk.positions["tok-yes"]
    assert pos.order_id == "ord-1"  # unchanged, can retry later
    assert risk.total_invested == pytest.approx(50.0)


# ── confirm_filled_limits ───────────────────────────────────────────────

def test_confirm_fully_filled_limit_clears_order_id():
    # get_order reports the full size matched -> position becomes a held,
    # exitable holding (order_id cleared), capital unchanged.
    client = FakeClient(order_status={"sizeMatched": 100})
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    count = engine.confirm_filled_limits()

    assert count == 1
    pos = risk.positions["tok-yes"]
    assert pos.order_id == ""
    assert pos.size == pytest.approx(100.0)
    assert risk.total_invested == pytest.approx(50.0)
    assert client.cancelled_orders == []  # a full fill is not cancelled


def test_confirm_partial_fill_cancels_remainder_and_frees_capital():
    client = FakeClient(order_status={"sizeMatched": 40})
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    count = engine.confirm_filled_limits()

    assert count == 1
    pos = risk.positions["tok-yes"]
    assert pos.size == pytest.approx(40.0)
    assert pos.cost_basis == pytest.approx(20.0)
    assert pos.order_id == ""
    assert risk.total_invested == pytest.approx(20.0)
    assert client.cancelled_orders == ["ord-1"]  # remainder cancelled


def test_confirm_unfilled_limit_left_untouched():
    client = FakeClient(order_status={"sizeMatched": 0})
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    count = engine.confirm_filled_limits()

    assert count == 0
    pos = risk.positions["tok-yes"]
    assert pos.order_id == "ord-1"  # still resting, unchanged
    assert risk.total_invested == pytest.approx(50.0)


def test_confirm_ignores_market_positions():
    client = FakeClient(order_status={"sizeMatched": 100})
    engine, risk = _engine_with_position(client, order_id="mkt-1", order_type="market")
    count = engine.confirm_filled_limits()
    assert count == 0
    assert client.get_order_calls == []  # market positions aren't queried


def test_confirm_status_error_leaves_position():
    client = FakeClient(get_order_raises=True)
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    count = engine.confirm_filled_limits()
    assert count == 0
    assert risk.positions["tok-yes"].order_id == "ord-1"


# ── execute_exit ────────────────────────────────────────────────────────

def test_execute_exit_sells_shares_not_usdc():
    client = FakeClient(midpoint=0.6)
    engine, risk = _engine_with_position(client, cost=50.0, size=100.0)
    engine.execute_exit("tok-yes", reason="manual")

    assert len(client.market_orders) == 1
    order = client.market_orders[0]
    assert order["side"] == "SELL"
    # Must pass share count (100), not USDC proceeds (~60).
    assert order["amount"] == pytest.approx(100.0)
    assert "tok-yes" not in risk.positions
