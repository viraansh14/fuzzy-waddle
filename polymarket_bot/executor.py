"""Order execution engine - translates signals into actual trades."""

import logging
import time

from .client import PolymarketClient
from .config import Config
from .risk_manager import RiskManager
from .strategies.base import Signal

logger = logging.getLogger(__name__)


def _parse_filled_size(order_data: dict) -> float | None:
    """Return filled share count from a cancel-response or order-status dict.

    Returns None when no recognised fill field is present (caller must treat
    the fill count as unknown rather than zero).
    """
    for key in ("sizeMatched", "size_matched", "matchedAmount", "filled"):
        val = order_data.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


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
            current_price = self.client.get_midpoint(token_id)

            # For a SELL market order the CLOB API expects the number of shares
            # (tokens), not a USDC amount. Passing USDC proceeds would size the
            # order incorrectly.
            resp = self.client.place_market_order(
                token_id=token_id,
                side="SELL",
                amount=pos.size,
            )

            proceeds_est = pos.size * current_price
            self.risk.record_exit(token_id, current_price, proceeds_est)
            return True

        except Exception as e:
            logger.error("Exit failed for %s: %s", token_id, e)
            return False

    def cancel_stale_orders(self, min_age_seconds: float = 120) -> int:
        """
        Cancel GTC limit orders that are older than min_age_seconds and
        have not yet fully filled, then free the reserved capital.

        Only limit orders are eligible (they have a non-empty order_id that
        isn't the dry-run sentinel). Market orders are FOK and either fill
        immediately or are rejected, so they never leave open orders on the
        book. Skipping orders younger than min_age_seconds ensures that
        limits placed in the current cycle have at least one full sleep
        interval to fill before being cancelled.

        Partial fills are handled explicitly: if the API reports that some
        shares were matched before the cancel, the position is updated to
        reflect only those shares and the unfilled capital is released.  If
        fill information is unavailable the position is left untouched rather
        than silently dropping real tokens.
        """
        if self.config.dry_run:
            return 0

        now = time.time()
        candidates = [
            (token_id, pos)
            for token_id, pos in list(self.risk.positions.items())
            if pos.order_id
            and pos.order_id != "dry-run"
            and (now - pos.entry_time) > min_age_seconds
        ]

        cancelled = 0
        for token_id, pos in candidates:
            order_id = pos.order_id  # save before we might clear it
            try:
                cancel_resp = self.client.cancel_order(order_id)

                # The cancel response may include fill info directly; if not,
                # fall back to a dedicated order-status query.
                filled_size = _parse_filled_size(cancel_resp)
                if filled_size is None:
                    try:
                        order_status = self.client.get_order(order_id)
                        filled_size = _parse_filled_size(order_status)
                    except Exception:
                        pass

                if filled_size is None:
                    # Fill count is unknown — safer to leave the position in
                    # place than to silently drop real tokens from the wallet.
                    logger.warning(
                        "Cannot determine fill for cancelled order %s; "
                        "position kept to avoid data loss for %s",
                        order_id, pos.question[:40],
                    )
                    continue

                if filled_size > 0:
                    # Partial fill: shrink the position to the filled shares
                    # and release the capital reserved for the unfilled portion.
                    filled_cost = pos.entry_price * filled_size
                    unfilled_cost = pos.cost_basis - filled_cost
                    pos.size = filled_size
                    pos.cost_basis = filled_cost
                    pos.order_id = ""  # prevent re-evaluation next cycle
                    self.risk.total_invested -= unfilled_cost
                    logger.info(
                        "Partial fill on stale order %s: %.4f shares kept, "
                        "$%.2f unreserved for %s",
                        order_id, filled_size, unfilled_cost, pos.question[:40],
                    )
                else:
                    # Zero fill: order never executed; release all reserved capital.
                    self.risk.record_exit(token_id, pos.entry_price, pos.cost_basis)
                    logger.info(
                        "Cancelled stale limit order %s for %s, phantom position removed",
                        order_id, pos.question[:40],
                    )
                cancelled += 1
            except Exception as e:
                logger.warning("Failed to cancel stale order %s: %s", order_id, e)
        return cancelled
