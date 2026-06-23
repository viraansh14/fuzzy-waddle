"""Tests for PolymarketBot._cycle state-persistence around stale cleanup.

The full bot constructor builds a live API client, so these tests assemble a
bot instance via object.__new__ and inject fakes for just the pieces _cycle
touches.
"""

import json

import pytest

import polymarket_bot.bot as botmod
from polymarket_bot.bot import PolymarketBot
from polymarket_bot.risk_manager import RiskManager

from .conftest import make_config


class _FakeExecutor:
    def __init__(self, stale_modified):
        self._stale_modified = stale_modified
        self.cancel_calls = 0

    def cancel_stale_orders(self, min_age_seconds):
        self.cancel_calls += 1
        return self._stale_modified


class _RaisingAnalyzer:
    def scan_markets(self, limit=100):
        raise RuntimeError("scan blew up")


class _EmptyAnalyzer:
    def scan_markets(self, limit=100):
        return []


def _make_bot(*, stale_modified, analyzer):
    bot = object.__new__(PolymarketBot)
    bot.config = make_config()
    bot.risk = RiskManager(bot.config)
    bot.executor = _FakeExecutor(stale_modified)
    bot.analyzer = analyzer
    bot.strategies = []
    bot._running = True
    bot._cycle_count = 0
    bot._saves = 0

    def fake_save_state():
        bot._saves += 1

    bot._save_state = fake_save_state
    bot._log_portfolio = lambda: None
    bot._check_exits = lambda: None
    return bot


def test_state_persisted_after_stale_cleanup_even_if_scan_raises():
    # The dangerous case: cleanup mutated state, then a later step raises.
    bot = _make_bot(stale_modified=1, analyzer=_RaisingAnalyzer())
    with pytest.raises(RuntimeError):
        bot._cycle()
    assert bot.executor.cancel_calls == 1
    assert bot._saves == 1  # cleanup was persisted before the raise


def test_no_save_when_nothing_stale_and_scan_raises():
    bot = _make_bot(stale_modified=0, analyzer=_RaisingAnalyzer())
    with pytest.raises(RuntimeError):
        bot._cycle()
    assert bot._saves == 0  # nothing to persist


def test_state_persisted_on_no_markets_path():
    bot = _make_bot(stale_modified=1, analyzer=_EmptyAnalyzer())
    bot._cycle()  # returns cleanly on the "no markets" path
    assert bot._saves == 1


def _legacy_position(order_id):
    # A position dict as written by a build before order_type was persisted.
    return {
        "token_id": "t", "condition_id": "c", "side": "YES", "question": "q?",
        "entry_price": 0.5, "size": 100, "cost_basis": 50.0,
        "entry_time": 0.0, "strategy": "value", "order_id": order_id,
    }


def test_load_state_infers_order_type_for_legacy(tmp_path, monkeypatch):
    # Legacy state lacks order_type; a real order_id should restore as a
    # cancellable limit, an empty/dry-run one as a (non-cancelled) market.
    state = {
        "cycle_count": 3,
        "realized_pnl": 0.0,
        "trade_count": 2,
        "total_invested": 100.0,
        "positions": {
            "lim": {**_legacy_position("ord-9"), "token_id": "lim"},
            "mkt": {**_legacy_position(""), "token_id": "mkt"},
            "dry": {**_legacy_position("dry-run"), "token_id": "dry"},
        },
    }
    state_file = tmp_path / "bot_state.json"
    state_file.write_text(json.dumps(state))
    monkeypatch.setattr(botmod, "STATE_FILE", state_file)

    bot = object.__new__(PolymarketBot)
    bot.config = make_config()
    bot.risk = RiskManager(bot.config)
    bot._cycle_count = 0
    bot._load_state()

    assert bot.risk.positions["lim"].order_type == "limit"
    assert bot.risk.positions["mkt"].order_type == "market"
    assert bot.risk.positions["dry"].order_type == "market"


def test_load_state_preserves_explicit_order_type(tmp_path, monkeypatch):
    pos = {**_legacy_position("ord-1"), "token_id": "t", "order_type": "market"}
    state = {"positions": {"t": pos}}
    state_file = tmp_path / "bot_state.json"
    state_file.write_text(json.dumps(state))
    monkeypatch.setattr(botmod, "STATE_FILE", state_file)

    bot = object.__new__(PolymarketBot)
    bot.config = make_config()
    bot.risk = RiskManager(bot.config)
    bot._cycle_count = 0
    bot._load_state()

    # Explicit order_type in state is not overwritten by the migration.
    assert bot.risk.positions["t"].order_type == "market"
