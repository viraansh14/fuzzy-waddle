"""Sentiment strategy - uses news headlines to find alpha."""

import logging
import re
from typing import Optional

import requests

from .base import BaseStrategy, Signal
from ..analyzer import MarketSnapshot
from ..config import Config

logger = logging.getLogger(__name__)

# Keywords that signal positive/negative outcomes
POSITIVE_KEYWORDS = [
    "win", "wins", "winning", "victory", "leads", "leading", "surges",
    "passes", "approved", "confirms", "succeeds", "breakthrough", "soars",
    "gains", "rises", "jumps", "rallies", "agrees", "deal", "signs",
    "launches", "announces", "beats", "dominates", "landslide",
]
NEGATIVE_KEYWORDS = [
    "lose", "loses", "losing", "defeat", "trails", "trailing", "plunges",
    "fails", "rejected", "denies", "collapses", "drops", "falls", "tanks",
    "crashes", "declines", "withdraws", "cancels", "blocks", "bans",
    "opposes", "scandal", "crisis", "disaster",
]


class SentimentStrategy(BaseStrategy):
    """
    Analyzes news sentiment about market topics to find trades.

    Logic:
    - Extracts key terms from market question
    - Searches recent news for those terms
    - Scores sentiment of headlines (positive/negative keyword matching)
    - Strong positive sentiment → BUY YES
    - Strong negative sentiment → BUY NO
    - If no NewsAPI key, uses market description heuristics
    """

    name = "sentiment"

    def __init__(self, config: Config):
        self.news_api_key = config.news_api_key
        self._cache: dict[str, float] = {}

    def evaluate(self, market: MarketSnapshot) -> Optional[Signal]:
        question = market.question
        cache_key = market.condition_id

        if cache_key in self._cache:
            sentiment_score = self._cache[cache_key]
        else:
            if self.news_api_key:
                sentiment_score = self._score_from_news(question)
            else:
                sentiment_score = self._score_from_question(question, market)
            self._cache[cache_key] = sentiment_score

        if abs(sentiment_score) < 0.3:
            return None

        confidence = min(0.85, 0.5 + abs(sentiment_score) * 0.4)

        if sentiment_score > 0:
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_yes,
                confidence=confidence,
                strategy_name=self.name,
                reason=f"Positive sentiment ({sentiment_score:+.2f}) for: {question[:50]}",
            )
        else:
            return Signal(
                market=market,
                side="BUY",
                token_id=market.token_no,
                confidence=confidence,
                strategy_name=self.name,
                reason=f"Negative sentiment ({sentiment_score:+.2f}) for: {question[:50]}",
            )

    def _score_from_news(self, question: str) -> float:
        """Query NewsAPI and score headlines."""
        search_terms = self._extract_search_terms(question)
        if not search_terms:
            return 0.0

        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": search_terms,
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "apiKey": self.news_api_key,
                },
                timeout=15,
            )
            resp.raise_for_status()
            articles = resp.json().get("articles", [])
        except Exception as e:
            logger.warning("News API error: %s", e)
            return 0.0

        if not articles:
            return 0.0

        scores = []
        for article in articles:
            title = (article.get("title") or "").lower()
            desc = (article.get("description") or "").lower()
            text = f"{title} {desc}"
            scores.append(self._keyword_score(text))

        return sum(scores) / len(scores) if scores else 0.0

    def _score_from_question(self, question: str, market: MarketSnapshot) -> float:
        """Heuristic sentiment from the market question itself and price action."""
        score = 0.0

        # If price is moving strongly in one direction, that's a sentiment signal
        if market.price_history and len(market.price_history) >= 10:
            prices = [float(h.get("p", h.get("price", 0))) for h in market.price_history]
            recent = sum(prices[-5:]) / 5
            older = sum(prices[-10:-5]) / 5
            if older > 0:
                move = (recent - older) / older
                score += move * 3  # Amplify the signal

        return max(-1.0, min(1.0, score))

    def _extract_search_terms(self, question: str) -> str:
        """Pull searchable terms from a market question."""
        # Remove common question words
        clean = re.sub(
            r"\b(will|the|be|in|on|at|to|of|a|an|by|for|is|has|have|do|does|"
            r"before|after|during|this|that|which|who|what|when|where|how)\b",
            "",
            question.lower(),
        )
        clean = re.sub(r"[?!.,]", "", clean)
        words = [w for w in clean.split() if len(w) > 2]
        return " ".join(words[:5])

    @staticmethod
    def _keyword_score(text: str) -> float:
        """Score text by positive/negative keyword presence."""
        pos = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
        neg = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
        total = pos + neg
        if total == 0:
            return 0.0
        return (pos - neg) / total
