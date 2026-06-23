"""Market analyzer - scores and ranks markets for trading opportunities."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .client import PolymarketClient
from .config import Config

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """Enriched snapshot of a market with all data needed for strategy evaluation."""

    condition_id: str
    question: str
    slug: str
    token_yes: str
    token_no: str
    outcome_prices: dict[str, float]  # {"Yes": 0.65, "No": 0.35}
    volume_24h: float
    total_volume: float
    liquidity: float
    spread: float
    bid: float
    ask: float
    mid: float
    price_history: list[dict]
    end_date: Optional[str] = None
    description: str = ""
    tags: list[str] = field(default_factory=list)
    # Resting order liquidity on each side of the YES book (USDC notional).
    bid_liquidity: float = 0.0
    ask_liquidity: float = 0.0

    @property
    def implied_probability(self) -> float:
        return self.outcome_prices.get("Yes", self.mid)

    @property
    def is_liquid(self) -> bool:
        return self.liquidity > 500 and self.spread < 0.10

    @property
    def hours_to_resolution(self) -> Optional[float]:
        """Hours until the market resolves, or None if the end date is unknown
        or unparseable. Negative if the end date is already in the past."""
        if not self.end_date:
            return None
        raw = self.end_date.strip()
        # Python 3.10's fromisoformat does not accept a trailing 'Z'.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return delta.total_seconds() / 3600


class MarketAnalyzer:
    """Fetches, filters, and enriches markets for strategy consumption."""

    def __init__(self, client: PolymarketClient, config: Config):
        self.client = client
        self.config = config

    def scan_markets(self, limit: int = 100) -> list[MarketSnapshot]:
        """Fetch active markets and build enriched snapshots."""
        raw_markets = self.client.safe_request(
            self.client.get_active_markets, limit=limit
        )
        snapshots = []
        for m in raw_markets:
            try:
                snap = self._build_snapshot(m)
                if snap and self._passes_filters(snap):
                    snapshots.append(snap)
            except Exception as e:
                logger.debug("Skipping market %s: %s", m.get("question", "?")[:40], e)
        logger.info("Scanned %d markets, %d passed filters", len(raw_markets), len(snapshots))
        return snapshots

    def _build_snapshot(self, raw: dict) -> Optional[MarketSnapshot]:
        """Build a MarketSnapshot from raw Gamma API data."""
        tokens = raw.get("clobTokenIds") or raw.get("clob_token_ids", [])
        if not tokens or len(tokens) < 2:
            return None

        token_yes = tokens[0]
        token_no = tokens[1]

        # Get prices from raw data first, fall back to orderbook
        outcome_prices_raw = raw.get("outcomePrices") or raw.get("outcome_prices")
        if outcome_prices_raw:
            if isinstance(outcome_prices_raw, str):
                import json
                prices_list = json.loads(outcome_prices_raw)
            else:
                prices_list = outcome_prices_raw
            outcome_prices = {
                "Yes": float(prices_list[0]),
                "No": float(prices_list[1]),
            }
        else:
            outcome_prices = {"Yes": 0.5, "No": 0.5}

        # Get orderbook data for the YES token
        try:
            spread_data = self.client.get_spread(token_yes)
        except Exception:
            spread_data = {
                "bid": outcome_prices["Yes"] - 0.02,
                "ask": outcome_prices["Yes"] + 0.02,
                "spread": 0.04,
                "mid": outcome_prices["Yes"],
            }

        # Get price history
        try:
            history = self.client.get_price_history(token_yes, fidelity=60)
        except Exception:
            history = []

        # Get liquidity info (total plus per-side resting liquidity)
        try:
            liq_data = self.client.get_book_liquidity(token_yes)
            liquidity = liq_data["total"]
            bid_liquidity = liq_data.get("bid_liquidity", 0.0)
            ask_liquidity = liq_data.get("ask_liquidity", 0.0)
        except Exception:
            liquidity = 0
            bid_liquidity = 0.0
            ask_liquidity = 0.0

        volume_24h = float(raw.get("volume24hr", 0) or raw.get("volume_24hr", 0) or 0)
        total_volume = float(raw.get("volumeNum", 0) or raw.get("volume_num", 0) or 0)

        return MarketSnapshot(
            condition_id=raw.get("conditionId") or raw.get("condition_id", ""),
            question=raw.get("question", ""),
            slug=raw.get("slug", ""),
            token_yes=token_yes,
            token_no=token_no,
            outcome_prices=outcome_prices,
            volume_24h=volume_24h,
            total_volume=total_volume,
            liquidity=liquidity,
            spread=spread_data["spread"],
            bid=spread_data["bid"],
            ask=spread_data["ask"],
            mid=spread_data["mid"],
            price_history=history,
            end_date=raw.get("endDate") or raw.get("end_date_iso"),
            description=raw.get("description", ""),
            tags=[t.get("label", "") for t in raw.get("tags", [])],
            bid_liquidity=bid_liquidity,
            ask_liquidity=ask_liquidity,
        )

    def _passes_filters(self, snap: MarketSnapshot) -> bool:
        """Filter out illiquid or unsuitable markets."""
        if snap.liquidity < self.config.min_liquidity:
            return False
        if snap.total_volume < self.config.min_volume:
            return False
        if snap.spread > 0.15:
            return False
        # Skip markets too close to resolution (> 95% or < 5%)
        if snap.mid > 0.95 or snap.mid < 0.05:
            return False
        return True
