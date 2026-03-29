"""Polymarket API client wrapper - handles CLOB + Gamma APIs."""

import logging
import time
from typing import Any

import requests
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    MarketOrderArgs,
    OrderArgs,
    OrderType,
)
from py_clob_client.order_builder.constants import BUY, SELL

from .config import Config

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Unified client for Polymarket CLOB and Gamma APIs."""

    CHAIN_ID = 137  # Polygon mainnet

    def __init__(self, config: Config):
        self.config = config
        self.creds = ApiCreds(
            api_key=config.api_key,
            api_secret=config.api_secret,
            api_passphrase=config.api_passphrase,
        )
        self.clob = ClobClient(
            config.clob_api_url,
            key=config.private_key,
            chain_id=self.CHAIN_ID,
            creds=self.creds,
        )
        self.gamma_url = config.gamma_api_url
        self._session = requests.Session()

    # ── Gamma API (market discovery) ────────────────────────────────

    def get_active_markets(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Fetch active markets from the Gamma API."""
        resp = self._session.get(
            f"{self.gamma_url}/markets",
            params={
                "limit": limit,
                "offset": offset,
                "active": True,
                "closed": False,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_market_by_id(self, condition_id: str) -> dict:
        resp = self._session.get(
            f"{self.gamma_url}/markets/{condition_id}",
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_events(self, limit: int = 50) -> list[dict]:
        """Fetch events (groups of markets) from Gamma."""
        resp = self._session.get(
            f"{self.gamma_url}/events",
            params={"limit": limit, "active": True, "closed": False},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # ── CLOB API (orderbook + trading) ──────────────────────────────

    def get_orderbook(self, token_id: str) -> dict:
        """Get the orderbook for a specific token (outcome)."""
        return self.clob.get_order_book(token_id)

    def get_midpoint(self, token_id: str) -> float:
        """Get the midpoint price for a token."""
        try:
            mid = self.clob.get_midpoint(token_id)
            return float(mid)
        except Exception:
            book = self.get_orderbook(token_id)
            best_bid = float(book["bids"][0]["price"]) if book.get("bids") else 0
            best_ask = float(book["asks"][0]["price"]) if book.get("asks") else 1
            return (best_bid + best_ask) / 2

    def get_spread(self, token_id: str) -> dict[str, float]:
        """Get bid/ask/spread for a token."""
        book = self.get_orderbook(token_id)
        best_bid = float(book["bids"][0]["price"]) if book.get("bids") else 0
        best_ask = float(book["asks"][0]["price"]) if book.get("asks") else 1
        return {
            "bid": best_bid,
            "ask": best_ask,
            "spread": best_ask - best_bid,
            "mid": (best_bid + best_ask) / 2,
        }

    def get_price_history(self, token_id: str, fidelity: int = 60) -> list[dict]:
        """Get price history via CLOB timeseries endpoint."""
        resp = self._session.get(
            f"{self.config.clob_api_url}/prices-history",
            params={"market": token_id, "interval": "max", "fidelity": fidelity},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("history", [])

    def get_open_orders(self) -> list[dict]:
        return self.clob.get_orders()

    def get_positions(self) -> list[dict]:
        """Get current positions (balance of outcome tokens)."""
        # The CLOB client doesn't have a direct positions endpoint;
        # we track positions locally. This queries open orders as a proxy.
        return self.clob.get_orders()

    def cancel_order(self, order_id: str) -> dict:
        return self.clob.cancel(order_id)

    def cancel_all_orders(self) -> None:
        self.clob.cancel_all()

    # ── Order placement ─────────────────────────────────────────────

    def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
    ) -> dict:
        """Place a limit order. side='BUY' or 'SELL'."""
        order_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            price=price,
            size=size,
            side=order_side,
            token_id=token_id,
        )
        signed = self.clob.create_order(order_args)
        resp = self.clob.post_order(signed, OrderType.GTC)
        logger.info(
            "Limit order placed: %s %s @ %.4f x %.2f | resp=%s",
            side, token_id[:12], price, size, resp,
        )
        return resp

    def place_market_order(
        self,
        token_id: str,
        side: str,
        amount: float,
    ) -> dict:
        """Place a market order for a given USDC amount."""
        order_side = BUY if side.upper() == "BUY" else SELL
        order_args = MarketOrderArgs(
            token_id=token_id,
            amount=amount,
            side=order_side,
        )
        signed = self.clob.create_market_order(order_args)
        resp = self.clob.post_order(signed, OrderType.FOK)
        logger.info(
            "Market order placed: %s %s $%.2f | resp=%s",
            side, token_id[:12], amount, resp,
        )
        return resp

    # ── Utilities ───────────────────────────────────────────────────

    def get_book_liquidity(self, token_id: str) -> dict[str, float]:
        """Calculate total liquidity on each side of the book."""
        book = self.get_orderbook(token_id)
        bid_liq = sum(
            float(o["price"]) * float(o["size"]) for o in book.get("bids", [])
        )
        ask_liq = sum(
            float(o["price"]) * float(o["size"]) for o in book.get("asks", [])
        )
        return {"bid_liquidity": bid_liq, "ask_liquidity": ask_liq, "total": bid_liq + ask_liq}

    def safe_request(self, func, *args, retries: int = 3, **kwargs) -> Any:
        """Retry wrapper for API calls."""
        for attempt in range(retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.warning("API call failed (attempt %d/%d): %s", attempt + 1, retries, e)
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
