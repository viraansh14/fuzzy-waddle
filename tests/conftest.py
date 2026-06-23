"""Shared fixtures and lightweight fakes for the test suite.

The bot's real client talks to the Polymarket CLOB/Gamma APIs over the
network. None of the unit tests below should touch the network, so we build
``MarketSnapshot`` objects directly and use a ``FakeClient`` that records the
calls made against it.
"""

from polymarket_bot.analyzer import MarketSnapshot
from polymarket_bot.config import Config


def make_config(**overrides) -> Config:
    """Build a Config with sane test defaults, overridable per-test."""
    cfg = Config()
    cfg.dry_run = False
    cfg.max_position_size_usdc = 100.0
    cfg.max_total_exposure_usdc = 500.0
    cfg.max_positions = 10
    cfg.stop_loss_pct = 15.0
    cfg.take_profit_pct = 40.0
    cfg.trade_loop_interval = 60
    cfg.min_liquidity = 1000.0
    cfg.min_volume = 5000.0
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_snapshot(
    *,
    condition_id: str = "cond-1",
    question: str = "Will it rain?",
    token_yes: str = "tok-yes",
    token_no: str = "tok-no",
    yes_price: float = 0.5,
    no_price: float = 0.5,
    volume_24h: float = 10_000.0,
    total_volume: float = 100_000.0,
    liquidity: float = 5_000.0,
    spread: float = 0.02,
    bid: float = 0.49,
    ask: float = 0.51,
    mid: float = 0.50,
    price_history=None,
    end_date=None,
    bid_liquidity: float = 0.0,
    ask_liquidity: float = 0.0,
) -> MarketSnapshot:
    """Construct a MarketSnapshot with overridable fields for strategy tests."""
    return MarketSnapshot(
        condition_id=condition_id,
        question=question,
        slug="will-it-rain",
        token_yes=token_yes,
        token_no=token_no,
        outcome_prices={"Yes": yes_price, "No": no_price},
        volume_24h=volume_24h,
        total_volume=total_volume,
        liquidity=liquidity,
        spread=spread,
        bid=bid,
        ask=ask,
        mid=mid,
        price_history=price_history if price_history is not None else [],
        end_date=end_date,
        bid_liquidity=bid_liquidity,
        ask_liquidity=ask_liquidity,
    )


def price_series(values) -> list[dict]:
    """Turn a list of floats into the {"p": value} history format."""
    return [{"p": v} for v in values]


class FakeClient:
    """Minimal stand-in for PolymarketClient used by executor tests.

    Only the methods the executor actually calls are implemented. Each call is
    recorded so tests can assert on the interaction, and per-method behaviour is
    configurable via the constructor.
    """

    def __init__(
        self,
        *,
        midpoint: float = 0.5,
        cancel_response=None,
        order_status=None,
        cancel_raises: bool = False,
        get_order_raises: bool = False,
    ):
        self._midpoint = midpoint
        self._cancel_response = cancel_response if cancel_response is not None else {}
        self._order_status = order_status
        self._cancel_raises = cancel_raises
        self._get_order_raises = get_order_raises
        self.cancelled_orders: list[str] = []
        self.get_order_calls: list[str] = []
        self.market_orders: list[dict] = []

    def get_midpoint(self, token_id: str) -> float:
        return self._midpoint

    def cancel_order(self, order_id: str) -> dict:
        if self._cancel_raises:
            raise RuntimeError("cancel failed")
        self.cancelled_orders.append(order_id)
        return self._cancel_response

    def get_order(self, order_id: str) -> dict:
        self.get_order_calls.append(order_id)
        if self._get_order_raises:
            raise RuntimeError("get_order failed")
        if self._order_status is None:
            raise RuntimeError("no order status configured")
        return self._order_status

    def place_market_order(self, token_id: str, side: str, amount: float) -> dict:
        self.market_orders.append({"token_id": token_id, "side": side, "amount": amount})
        return {"orderID": "filled-order"}
