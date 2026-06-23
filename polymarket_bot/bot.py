"""Main bot orchestrator - the brain that runs the trading loop."""

import json
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .aggregation import aggregate_signals
from .analyzer import MarketAnalyzer
from .client import PolymarketClient
from .config import Config
from .executor import ExecutionEngine
from .risk_manager import RiskManager
from .strategies import (
    MeanReversionStrategy,
    MomentumStrategy,
    OrderBookImbalanceStrategy,
    ResolutionDriftStrategy,
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
            OrderBookImbalanceStrategy(min_imbalance=0.30, min_book_liquidity=2000),
            ResolutionDriftStrategy(max_hours=72.0),
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

        # 1. Confirm fills on resting limit orders first, so a filled limit
        #    becomes a tracked holding before exits are evaluated this cycle.
        reconciled = self.executor.confirm_filled_limits()

        # 2. Check exits on existing positions (now including just-confirmed fills)
        self._check_exits()

        # 3. Cancel stale GTC limit orders unconditionally so aged phantom
        #    positions are freed even on cycles where no markets or signals
        #    are found. Persist immediately if anything changed: the steps
        #    below (market scan, strategy eval, execution) can raise, and the
        #    caller swallows the exception, so a deferred save would lose the
        #    cleanup and let a restart reload phantom positions from disk.
        stale_modified = self.executor.cancel_stale_orders(
            min_age_seconds=self.config.trade_loop_interval * 2
        )
        if reconciled or stale_modified:
            self._save_state()

        # 3. Scan markets
        markets = self.analyzer.scan_markets(limit=100)
        if not markets:
            logger.warning("No markets passed filters")
            return

        # 4. Generate signals from all strategies
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

        # 5. Regime-aware aggregation: one signal per market, with trend/counter
        #    signals filtered by the market's regime and directional conflicts
        #    resolved. The confidence penalty for conflict can drop a signal
        #    below threshold, so re-apply the floor afterwards.
        kind_by_strategy = {s.name: s.kind for s in self.strategies}
        top_signals = aggregate_signals(
            all_signals, kind_by_strategy=kind_by_strategy, max_signals=5
        )
        top_signals = [s for s in top_signals if s.confidence >= 0.55]

        if not top_signals:
            logger.info("No signals survived regime aggregation this cycle")
            self._log_portfolio()
            return

        logger.info("Top signals this cycle:")
        for i, sig in enumerate(top_signals, 1):
            logger.info(
                "  %d. [%.0f%%] %s: %s %s — %s",
                i, sig.confidence * 100, sig.strategy_name,
                sig.side, sig.market.question[:40], sig.reason,
            )

        # 6. Execute top signals
        executed = 0
        for sig in top_signals:
            if self.executor.execute_signal(sig):
                executed += 1

        logger.info("Executed %d/%d signals", executed, len(top_signals))

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
                    "order_type": p.order_type,
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
                # Migrate state written before order_type was persisted: infer
                # it from the order_id so legacy resting limits stay eligible
                # for stale-order cancellation (a real, non-dry-run order_id
                # means it was placed as a cancellable order).
                if "order_type" not in pdata:
                    oid = pdata.get("order_id", "")
                    pdata["order_type"] = "limit" if oid and oid != "dry-run" else "market"
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
