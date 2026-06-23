"""Main bot orchestrator - the brain that runs the trading loop."""

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .analyzer import MarketAnalyzer
from .client import PolymarketClient
from .config import Config
from .executor import ExecutionEngine
from .risk_manager import RiskManager
from .strategies import (
    MeanReversionStrategy,
    MomentumStrategy,
    SentimentStrategy,
    ValueStrategy,
    VolumeSpikeStrategy,
)
from .strategies.base import BaseStrategy, Signal

logger = logging.getLogger(__name__)

STATE_FILE = Path("bot_state.json")


class PolymarketBot:
    """
    Fully automated Polymarket trading bot.

    Loop:
    1. Scan markets via Gamma API
    2. Build enriched snapshots (prices, orderbook, history)
    3. Run all strategies on each market
    4. Aggregate & rank signals by confidence
    5. Execute top signals (respecting risk limits)
    6. Check existing positions for exits (stop-loss, take-profit)
    7. Log portfolio state
    8. Sleep and repeat
    """

    def __init__(self, config: Config):
        self.config = config
        self.client = PolymarketClient(config)
        self.analyzer = MarketAnalyzer(self.client, config)
        self.risk = RiskManager(config)
        self.executor = ExecutionEngine(self.client, self.risk, config)
        self.strategies: list[BaseStrategy] = self._init_strategies()
        self._running = True
        self._cycle_count = 0

        # Graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

    def _init_strategies(self) -> list[BaseStrategy]:
        return [
            MomentumStrategy(short_window=6, long_window=20, min_move_pct=5.0),
            ValueStrategy(min_edge_pct=3.0),
            VolumeSpikeStrategy(volume_spike_ratio=0.10, min_24h_volume=5000),
            SentimentStrategy(config=self.config),
            MeanReversionStrategy(z_threshold=1.8, lookback=30),
        ]

    def run(self):
        """Main trading loop — runs forever until stopped."""
        mode = "DRY RUN" if self.config.dry_run else "LIVE"
        logger.info("=" * 60)
        logger.info("Polymarket Trading Bot started [%s MODE]", mode)
        logger.info("Max exposure: $%.0f | Max positions: %d",
                     self.config.max_total_exposure_usdc, self.config.max_positions)
        logger.info("Stop loss: %.0f%% | Take profit: %.0f%%",
                     self.config.stop_loss_pct, self.config.take_profit_pct)
        logger.info("Strategies: %s", ", ".join(s.name for s in self.strategies))
        logger.info("=" * 60)

        self._load_state()

        while self._running:
            try:
                self._cycle()
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error("Cycle error: %s", e, exc_info=True)

            if self._running:
                logger.info("Sleeping %ds until next cycle...", self.config.trade_loop_interval)
                time.sleep(self.config.trade_loop_interval)

        self._save_state()
        logger.info("Bot stopped. Final P&L: $%.2f", self.risk.realized_pnl)

    def _cycle(self):
        """Single trading cycle."""
        self._cycle_count += 1
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.info("─── Cycle #%d at %s ───", self._cycle_count, ts)

        # 1. Check exits on existing positions first
        self._check_exits()

        # 2. Scan markets
        markets = self.analyzer.scan_markets(limit=100)
        if not markets:
            logger.warning("No markets passed filters")
            return

        # 3. Generate signals from all strategies
        all_signals: list[Signal] = []
        for market in markets:
            for strategy in self.strategies:
                try:
                    sig = strategy.evaluate(market)
                    if sig and sig.confidence >= 0.55:
                        all_signals.append(sig)
                except Exception as e:
                    logger.debug("Strategy %s error on %s: %s",
                                 strategy.name, market.question[:30], e)

        if not all_signals:
            logger.info("No signals generated this cycle")
            self._log_portfolio()
            return

        # 4. Rank by confidence, dedupe by market
        all_signals.sort(key=lambda s: s.confidence, reverse=True)
        seen_markets = set()
        top_signals = []
        for sig in all_signals:
            if sig.market.condition_id not in seen_markets:
                top_signals.append(sig)
                seen_markets.add(sig.market.condition_id)
            if len(top_signals) >= 5:  # Max 5 new trades per cycle
                break

        logger.info("Top signals this cycle:")
        for i, sig in enumerate(top_signals, 1):
            logger.info(
                "  %d. [%.0f%%] %s: %s %s — %s",
                i, sig.confidence * 100, sig.strategy_name,
                sig.side, sig.market.question[:40], sig.reason,
            )

        # 5. Execute top signals
        executed = 0
        for sig in top_signals:
            if self.executor.execute_signal(sig):
                executed += 1

        logger.info("Executed %d/%d signals", executed, len(top_signals))

        # 6. Cancel GTC limit orders that have had at least 2 loop intervals
        #    to fill. Passing min_age_seconds ensures orders placed in the
        #    current cycle are never immediately cancelled.
        self.executor.cancel_stale_orders(
            min_age_seconds=self.config.trade_loop_interval * 2
        )

        # 7. Log portfolio
        self._log_portfolio()
        self._save_state()

    def _check_exits(self):
        """Check all positions for exit conditions."""
        if not self.risk.positions:
            return

        def get_price(token_id):
            return self.client.get_midpoint(token_id)

        exits = self.risk.check_exits(get_price)
        for token_id, reason in exits:
            self.executor.execute_exit(token_id, reason)

        # Persist immediately after exits so a crash mid-cycle doesn't
        # replay them on the next restart.
        if exits:
            self._save_state()

    def _log_portfolio(self):
        """Log current portfolio state."""
        def get_price(token_id):
            try:
                return self.client.get_midpoint(token_id)
            except Exception:
                return 0

        summary = self.risk.portfolio_summary(get_price)
        logger.info(
            "Portfolio: %d positions | Invested: $%.2f | "
            "Realized P&L: $%.2f | Unrealized: $%.2f | Total: $%.2f | Trades: %d",
            summary["open_positions"],
            summary["total_invested"],
            summary["realized_pnl"],
            summary["unrealized_pnl"],
            summary["total_pnl"],
            summary["trade_count"],
        )

    def _save_state(self):
        """Persist bot state to disk for crash recovery."""
        state = {
            "cycle_count": self._cycle_count,
            "realized_pnl": self.risk.realized_pnl,
            "trade_count": self.risk.trade_count,
            "total_invested": self.risk.total_invested,
            "positions": {
                tid: {
                    "token_id": p.token_id,
                    "condition_id": p.condition_id,
                    "side": p.side,
                    "question": p.question,
                    "entry_price": p.entry_price,
                    "size": p.size,
                    "cost_basis": p.cost_basis,
                    "entry_time": p.entry_time,
                    "strategy": p.strategy,
                    "order_id": p.order_id,
                }
                for tid, p in self.risk.positions.items()
            },
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        STATE_FILE.write_text(json.dumps(state, indent=2))

    def _load_state(self):
        """Load previous state if it exists."""
        if not STATE_FILE.exists():
            return
        try:
            state = json.loads(STATE_FILE.read_text())
            self._cycle_count = state.get("cycle_count", 0)
            self.risk.realized_pnl = state.get("realized_pnl", 0)
            self.risk.trade_count = state.get("trade_count", 0)
            self.risk.total_invested = state.get("total_invested", 0)

            from .risk_manager import Position
            for tid, pdata in state.get("positions", {}).items():
                self.risk.positions[tid] = Position(**pdata)

            logger.info(
                "Restored state: %d positions, %d trades, $%.2f realized P&L",
                len(self.risk.positions), self.risk.trade_count, self.risk.realized_pnl,
            )
        except Exception as e:
            logger.warning("Failed to load state: %s", e)

    def _shutdown(self, signum, frame):
        logger.info("Shutdown signal received, finishing current cycle...")
        self._running = False
