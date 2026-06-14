"""Risk manager - position sizing, exposure limits, stop losses, take profits."""

import logging
import time
from dataclasses import dataclass, field

from .config import Config
from .strategies.base import Signal

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """Tracks a single open position."""

    token_id: str
    condition_id: str
    side: str  # "YES" or "NO"
    question: str
    entry_price: float
    size: float  # number of shares
    cost_basis: float  # total USDC spent
    entry_time: float = field(default_factory=time.time)
    strategy: str = ""
    order_id: str = ""

    @property
    def age_hours(self) -> float:
        return (time.time() - self.entry_time) / 3600

    def pnl(self, current_price: float) -> float:
        """Unrealized P&L in USDC."""
        current_value = self.size * current_price
        return current_value - self.cost_basis

    def pnl_pct(self, current_price: float) -> float:
        if self.cost_basis == 0:
            return 0.0
        return self.pnl(current_price) / self.cost_basis * 100


class RiskManager:
    """Manages portfolio risk, position sizing, and exit logic."""

    def __init__(self, config: Config):
        self.config = config
        self.positions: dict[str, Position] = {}  # token_id → Position
        self.total_invested: float = 0.0
        self.realized_pnl: float = 0.0
        self.trade_count: int = 0

    # ── Position sizing ─────────────────────────────────────────────

    def calculate_position_size(self, signal: Signal) -> float:
        """
        Kelly-inspired position sizing based on confidence and risk limits.

        Higher confidence → larger position (but always capped).
        """
        remaining_budget = self.config.max_total_exposure_usdc - self.total_invested
        if remaining_budget <= 0:
            logger.info("Max exposure reached ($%.2f), no new positions", self.total_invested)
            return 0.0

        if len(self.positions) >= self.config.max_positions:
            logger.info("Max positions (%d) reached, no new positions", self.config.max_positions)
            return 0.0

        # Kelly fraction: f = edge / odds (simplified)
        # We use confidence as a proxy for edge
        confidence = signal.confidence
        kelly_fraction = max(0.05, (confidence - 0.5) * 2)  # 0 at 50%, 1 at 100%

        # Scale position by kelly fraction, cap at max per position
        raw_size = remaining_budget * kelly_fraction * 0.5  # Half-Kelly for safety
        position_size = min(
            raw_size,
            self.config.max_position_size_usdc,
            remaining_budget,
        )

        # Minimum viable trade
        if position_size < 5.0:
            return 0.0

        return round(position_size, 2)

    # ── Pre-trade checks ────────────────────────────────────────────

    def can_trade(self, signal: Signal) -> tuple[bool, str]:
        """Check if we're allowed to take this trade."""
        # Already in this market?
        if signal.token_id in self.positions:
            return False, "Already have position in this token"

        # Check opposing position (don't hold YES and NO of same market)
        for pos in self.positions.values():
            if pos.condition_id == signal.market.condition_id:
                return False, "Already have position in this market (opposite side)"

        # Exposure check
        if self.total_invested >= self.config.max_total_exposure_usdc:
            return False, "Max total exposure reached"

        # Position count check
        if len(self.positions) >= self.config.max_positions:
            return False, "Max position count reached"

        return True, "OK"

    # ── Position tracking ───────────────────────────────────────────

    def record_entry(
        self,
        signal: Signal,
        fill_price: float,
        size: float,
        cost: float,
        order_id: str = "",
    ) -> Position:
        """Record a new position entry."""
        side = "YES" if signal.token_id == signal.market.token_yes else "NO"
        pos = Position(
            token_id=signal.token_id,
            condition_id=signal.market.condition_id,
            side=side,
            question=signal.market.question,
            entry_price=fill_price,
            size=size,
            cost_basis=cost,
            strategy=signal.strategy_name,
            order_id=order_id,
        )
        self.positions[signal.token_id] = pos
        self.total_invested += cost
        self.trade_count += 1
        logger.info(
            "ENTRY: %s %s $%.2f @ %.4f | %s | %s",
            side, signal.market.question[:40], cost, fill_price,
            signal.strategy_name, signal.reason,
        )
        return pos

    def record_exit(self, token_id: str, exit_price: float, proceeds: float) -> float:
        """Record a position exit and return realized P&L."""
        pos = self.positions.pop(token_id, None)
        if not pos:
            return 0.0

        pnl = proceeds - pos.cost_basis
        self.realized_pnl += pnl
        self.total_invested -= pos.cost_basis
        logger.info(
            "EXIT: %s %s PnL=$%.2f (%.1f%%) held %.1fh",
            pos.side, pos.question[:40], pnl,
            (pnl / pos.cost_basis * 100) if pos.cost_basis else 0,
            pos.age_hours,
        )
        return pnl

    # ── Exit signal checks ──────────────────────────────────────────

    def check_exits(self, get_price_fn) -> list[tuple[str, str]]:
        """
        Check all positions for stop-loss or take-profit triggers.
        Returns list of (token_id, reason) to exit.
        """
        exits = []
        for token_id, pos in list(self.positions.items()):
            try:
                current_price = get_price_fn(token_id)
            except Exception:
                continue

            pnl_pct = pos.pnl_pct(current_price)

            # Stop loss
            if pnl_pct <= -self.config.stop_loss_pct:
                exits.append((token_id, f"STOP LOSS triggered at {pnl_pct:.1f}%"))

            # Take profit
            elif pnl_pct >= self.config.take_profit_pct:
                exits.append((token_id, f"TAKE PROFIT triggered at {pnl_pct:.1f}%"))

            # Time-based exit: if position is old and flat, free up capital
            elif pos.age_hours > 48 and abs(pnl_pct) < 5:
                exits.append((token_id, f"STALE position ({pos.age_hours:.0f}h, {pnl_pct:+.1f}%)"))

        return exits

    # ── Portfolio summary ───────────────────────────────────────────

    def portfolio_summary(self, get_price_fn=None) -> dict:
        """Get current portfolio state."""
        unrealized_pnl = 0.0
        if get_price_fn:
            for token_id, pos in self.positions.items():
                try:
                    price = get_price_fn(token_id)
                    unrealized_pnl += pos.pnl(price)
                except Exception:
                    pass

        return {
            "open_positions": len(self.positions),
            "total_invested": self.total_invested,
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": unrealized_pnl,
            "total_pnl": self.realized_pnl + unrealized_pnl,
            "trade_count": self.trade_count,
            "positions": {
                tid: {
                    "question": p.question[:50],
                    "side": p.side,
                    "entry": p.entry_price,
                    "cost": p.cost_basis,
                    "age_h": round(p.age_hours, 1),
                    "strategy": p.strategy,
                }
                for tid, p in self.positions.items()
            },
        }
