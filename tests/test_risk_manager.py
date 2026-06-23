"""Tests for RiskManager position accounting, sizing, and exit logic."""

import time

import pytest

from polymarket_bot.risk_manager import Position, RiskManager
from polymarket_bot.strategies.base import Signal

from .conftest import make_config, make_snapshot


def make_signal(market=None, token_id="tok-yes", confidence=0.8):
    market = market or make_snapshot()
    return Signal(
        market=market,
        side="BUY",
        token_id=token_id,
        confidence=confidence,
        strategy_name="test",
    )


def test_record_entry_updates_invested_and_count():
    risk = RiskManager(make_config())
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.5, size=100, cost=50.0)

    assert risk.total_invested == 50.0
    assert risk.trade_count == 1
    assert sig.token_id in risk.positions
    assert risk.positions[sig.token_id].side == "YES"


def test_record_exit_releases_invested_and_books_pnl():
    risk = RiskManager(make_config())
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.5, size=100, cost=50.0)

    pnl = risk.record_exit(sig.token_id, exit_price=0.6, proceeds=60.0)

    assert pnl == pytest.approx(10.0)
    assert risk.realized_pnl == pytest.approx(10.0)
    assert risk.total_invested == pytest.approx(0.0)
    assert sig.token_id not in risk.positions


def test_record_exit_unknown_token_is_noop():
    risk = RiskManager(make_config())
    assert risk.record_exit("nope", 0.5, 1.0) == 0.0


def test_entry_then_exit_roundtrip_keeps_invested_consistent():
    risk = RiskManager(make_config())
    s1 = make_signal(make_snapshot(condition_id="c1", token_yes="y1"), token_id="y1")
    s2 = make_signal(make_snapshot(condition_id="c2", token_yes="y2"), token_id="y2")
    risk.record_entry(s1, fill_price=0.5, size=100, cost=50.0)
    risk.record_entry(s2, fill_price=0.4, size=100, cost=40.0)
    assert risk.total_invested == pytest.approx(90.0)

    risk.record_exit("y1", 0.5, 50.0)
    assert risk.total_invested == pytest.approx(40.0)
    assert len(risk.positions) == 1


def test_can_trade_blocks_duplicate_token():
    risk = RiskManager(make_config())
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.5, size=100, cost=50.0)
    ok, _ = risk.can_trade(sig)
    assert ok is False


def test_can_trade_blocks_opposite_side_same_market():
    risk = RiskManager(make_config())
    market = make_snapshot(condition_id="c1", token_yes="y1", token_no="n1")
    yes_sig = make_signal(market, token_id="y1")
    risk.record_entry(yes_sig, fill_price=0.5, size=100, cost=50.0)

    no_sig = make_signal(market, token_id="n1")
    ok, reason = risk.can_trade(no_sig)
    assert ok is False
    assert "opposite" in reason.lower() or "market" in reason.lower()


def test_can_trade_blocks_when_max_exposure_reached():
    risk = RiskManager(make_config(max_total_exposure_usdc=100.0))
    risk.total_invested = 100.0
    ok, _ = risk.can_trade(make_signal())
    assert ok is False


def test_can_trade_blocked_when_loss_limit_reached():
    risk = RiskManager(make_config(max_total_loss_usdc=100.0))
    risk.realized_pnl = -100.0  # at the limit
    ok, reason = risk.can_trade(make_signal())
    assert ok is False
    assert "Loss limit" in reason


def test_can_trade_allowed_within_loss_limit():
    risk = RiskManager(make_config(max_total_loss_usdc=100.0))
    risk.realized_pnl = -50.0
    ok, _ = risk.can_trade(make_signal())
    assert ok is True


def test_loss_limit_disabled_when_zero():
    risk = RiskManager(make_config(max_total_loss_usdc=0.0))
    risk.realized_pnl = -1000.0  # huge loss, but breaker disabled
    ok, _ = risk.can_trade(make_signal())
    assert ok is True


def test_position_size_respects_per_position_cap():
    risk = RiskManager(make_config(max_position_size_usdc=20.0))
    size = risk.calculate_position_size(make_signal(confidence=1.0))
    assert size <= 20.0


def test_position_size_zero_when_exposure_exhausted():
    risk = RiskManager(make_config(max_total_exposure_usdc=100.0))
    risk.total_invested = 100.0
    assert risk.calculate_position_size(make_signal()) == 0.0


def test_check_exits_triggers_stop_loss():
    risk = RiskManager(make_config(stop_loss_pct=15.0))
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0)
    # Price drops 20% -> below the 15% stop loss threshold.
    exits = risk.check_exits(lambda _t: 0.40)
    assert any("STOP LOSS" in reason for _t, reason in exits)


def test_check_exits_triggers_take_profit():
    risk = RiskManager(make_config(take_profit_pct=40.0))
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0)
    # Price rises 50% -> above the 40% take profit threshold.
    exits = risk.check_exits(lambda _t: 0.75)
    assert any("TAKE PROFIT" in reason for _t, reason in exits)


def test_check_exits_no_trigger_within_band():
    risk = RiskManager(make_config(stop_loss_pct=15.0, take_profit_pct=40.0))
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0)
    exits = risk.check_exits(lambda _t: 0.52)  # +4%, within band
    assert exits == []


def test_check_exits_stale_position():
    risk = RiskManager(make_config())
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0)
    # Backdate entry beyond the 48h stale threshold.
    risk.positions[sig.token_id].entry_time = time.time() - 49 * 3600
    exits = risk.check_exits(lambda _t: 0.50)  # flat
    assert any("STALE" in reason for _t, reason in exits)


def test_check_exits_skips_unconfirmed_limit():
    # A resting limit order recorded at full size, fill not yet confirmed.
    risk = RiskManager(make_config(stop_loss_pct=15.0))
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0,
                      order_id="ord-1", order_type="limit")
    # Price would normally trigger a stop loss, but we may not hold the shares.
    exits = risk.check_exits(lambda _t: 0.40)
    assert exits == []


def test_check_exits_runs_once_limit_reconciled():
    risk = RiskManager(make_config(stop_loss_pct=15.0))
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0,
                      order_id="ord-1", order_type="limit")
    # cancel_stale_orders clears order_id on a partial/unknown fill -> now a
    # genuinely held position that can be exited.
    risk.positions[sig.token_id].order_id = ""
    exits = risk.check_exits(lambda _t: 0.40)
    assert any("STOP LOSS" in reason for _t, reason in exits)


def test_check_exits_runs_on_dry_run_limit():
    # Paper-trading limit positions must still exercise the exit path.
    risk = RiskManager(make_config(stop_loss_pct=15.0))
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0,
                      order_id="dry-run", order_type="limit")
    exits = risk.check_exits(lambda _t: 0.40)
    assert any("STOP LOSS" in reason for _t, reason in exits)


def test_check_exits_runs_on_market_position():
    risk = RiskManager(make_config(stop_loss_pct=15.0))
    sig = make_signal()
    risk.record_entry(sig, fill_price=0.50, size=100, cost=50.0,
                      order_id="mkt-1", order_type="market")
    exits = risk.check_exits(lambda _t: 0.40)
    assert any("STOP LOSS" in reason for _t, reason in exits)


def test_position_pnl_helpers():
    pos = Position(
        token_id="t", condition_id="c", side="YES", question="q",
        entry_price=0.5, size=100, cost_basis=50.0,
    )
    assert pos.pnl(0.6) == pytest.approx(10.0)
    assert pos.pnl_pct(0.6) == pytest.approx(20.0)
    # Zero cost basis must not divide by zero.
    pos.cost_basis = 0
    assert pos.pnl_pct(0.6) == 0.0
