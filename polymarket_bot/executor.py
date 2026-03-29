"""Order execution engine - translates signals into actual trades."""

import logging

from .client import PolymarketClient
from .config import Config
from .risk_manager import RiskManager
from .strategies.base import Signal

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """Executes trades from signals, handling order placement and fills."""

    def __init__(self, client: PolymarketClient, risk_manager: RiskManager, config: Config):
        self.client = client
        self.risk = risk_manager
        self.config = config

    def execute_signal(self, signal: Signal) -> bool:
        """
        Execute a trading signal end-to-end:
        1. Check risk limits
        2. Calculate position size
        3. Place order
        4. Record position

        Returns True if trade was placed.
        """
        # Pre-trade risk check
        can_trade, reason = self.risk.can_trade(signal)
        if not can_trade:
            logger.debug("Trade blocked: %s | %s", reason, signal)
            return False

        # Position sizing
        size_usdc = self.risk.calculate_position_size(signal)
        if size_usdc <= 0:
            logger.debug("Position size zero for %s", signal)
            return False

        # Calculate share quantity from USDC amount
        price = signal.target_price or signal.market.mid
        if price <= 0 or price >= 1:
            logger.warning("Invalid price %.4f for %s", price, signal.market.question[:40])
            return False

        shares = size_usdc / price

        logger.info(
            "EXECUTING: %s %s %.1f shares @ %.4f ($%.2f) | %s",
            signal.side, signal.market.question[:40],
            shares, price, size_usdc, signal.reason,
        )

        if self.config.dry_run:
            logger.info("[DRY RUN] Would place order — skipping actual execution")
            # Still record the position for paper trading
            self.risk.record_entry(
                signal=signal,
                fill_price=price,
                size=shares,
                cost=size_usdc,
                order_id="dry-run",
            )
            return True

        # Place the order
        try:
            if signal.target_price:
                # Use limit order at target price
                resp = self.client.place_limit_order(
                    token_id=signal.token_id,
                    side=signal.side,
                    price=signal.target_price,
                    size=shares,
                )
            else:
                # Use market order for immediate fills
                resp = self.client.place_market_order(
                    token_id=signal.token_id,
                    side=signal.side,
                    amount=size_usdc,
                )

            order_id = resp.get("orderID", resp.get("id", ""))

            # Record the position
            self.risk.record_entry(
                signal=signal,
                fill_price=price,
                size=shares,
                cost=size_usdc,
                order_id=order_id,
            )
            return True

        except Exception as e:
            logger.error("Order execution failed: %s | Signal: %s", e, signal)
            return False

    def execute_exit(self, token_id: str, reason: str) -> bool:
        """Exit a position by selling the tokens."""
        pos = self.risk.positions.get(token_id)
        if not pos:
            logger.warning("No position found for %s", token_id)
            return False

        logger.info("EXITING: %s %s | Reason: %s", pos.side, pos.question[:40], reason)

        if self.config.dry_run:
            try:
                current_price = self.client.get_midpoint(token_id)
            except Exception:
                current_price = pos.entry_price
            proceeds = pos.size * current_price
            self.risk.record_exit(token_id, current_price, proceeds)
            logger.info("[DRY RUN] Exit recorded at %.4f, proceeds=$%.2f", current_price, proceeds)
            return True

        try:
            # Sell all shares via market order
            current_price = self.client.get_midpoint(token_id)
            proceeds_est = pos.size * current_price

            resp = self.client.place_market_order(
                token_id=token_id,
                side="SELL",
                amount=proceeds_est,
            )

            self.risk.record_exit(token_id, current_price, proceeds_est)
            return True

        except Exception as e:
            logger.error("Exit failed for %s: %s", token_id, e)
            return False

    def cancel_stale_orders(self) -> int:
        """Cancel any open orders that haven't filled."""
        if self.config.dry_run:
            return 0
        try:
            self.client.cancel_all_orders()
            logger.info("Cancelled all open orders")
            return 1
        except Exception as e:
            logger.warning("Failed to cancel orders: %s", e)
            return 0
