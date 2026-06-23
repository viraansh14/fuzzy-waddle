"""Tests for PolymarketBot._cycle state-persistence around stale cleanup.

The full bot constructor builds a live API client, so these tests assemble a
bot instance via object.__new__ and inject fakes for just the pieces _cycle
touches.
"""

import pytest

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
