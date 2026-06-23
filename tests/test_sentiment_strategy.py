"""Tests for SentimentStrategy keyword scoring, price heuristic, and caching."""

from polymarket_bot.strategies.sentiment import SentimentStrategy

from .conftest import make_config, make_snapshot, price_series


def _strategy_no_news():
    cfg = make_config()
    cfg.news_api_key = ""
    return SentimentStrategy(cfg)


# ── keyword scoring ─────────────────────────────────────────────────────

def test_keyword_score_positive():
    assert SentimentStrategy._keyword_score("team wins the championship") > 0


def test_keyword_score_negative():
    assert SentimentStrategy._keyword_score("candidate loses and trails badly") < 0


def test_keyword_score_neutral():
    assert SentimentStrategy._keyword_score("a quiet uneventful day") == 0.0


# ── search term extraction ──────────────────────────────────────────────

def test_extract_search_terms_strips_stopwords():
    strat = _strategy_no_news()
    terms = strat._extract_search_terms("Will the Lakers win the title?")
    assert "will" not in terms.split()
    assert "the" not in terms.split()
    assert "lakers" in terms


# ── price-derived heuristic ─────────────────────────────────────────────

def test_question_score_positive_on_upward_move():
    strat = _strategy_no_news()
    rising = [0.40] * 5 + [0.60] * 5  # older avg 0.40, recent avg 0.60
    market = make_snapshot(price_history=price_series(rising))
    assert strat._score_from_question(market.question, market) > 0


def test_question_score_zero_with_short_history():
    strat = _strategy_no_news()
    market = make_snapshot(price_history=price_series([0.5] * 4))
    assert strat._score_from_question(market.question, market) == 0.0


def test_question_score_ignores_zero_corrupted_history():
    # History with missing "p" keys must not be read as 0.0 prices.
    strat = _strategy_no_news()
    history = [{"p": 0.50}, {"foo": 1}, {"p": 0.50}, {"p": 0.50}, {"p": 0.50},
               {"p": 0.50}, {"p": 0.50}, {"p": 0.50}, {"p": 0.50}, {"p": 0.50},
               {"p": 0.50}]
    market = make_snapshot(price_history=history)
    # Only 10 valid flat prices remain -> no move -> score 0, not a spurious
    # signal driven by a phantom 0.0 entry.
    assert strat._score_from_question(market.question, market) == 0.0


# ── caching behaviour ───────────────────────────────────────────────────

def test_price_heuristic_not_frozen_by_cache():
    # Without a news key, the score must track price action each call rather
    # than being cached from the first evaluation.
    strat = _strategy_no_news()
    up = make_snapshot(condition_id="c1", price_history=price_series([0.40] * 5 + [0.60] * 5))
    first = strat.evaluate(up)
    assert first is not None
    assert first.token_id == up.token_yes  # positive sentiment -> YES

    # Same market id, but now the price action has reversed downward.
    down = make_snapshot(condition_id="c1", price_history=price_series([0.60] * 5 + [0.40] * 5))
    second = strat.evaluate(down)
    assert second is not None
    assert second.token_id == down.token_no  # recomputed -> negative -> NO


def test_news_score_is_cached_within_ttl(monkeypatch):
    cfg = make_config()
    cfg.news_api_key = "fake-key"
    strat = SentimentStrategy(cfg)

    calls = {"n": 0}

    def fake_score_from_news(question):
        calls["n"] += 1
        return 0.8

    monkeypatch.setattr(strat, "_score_from_news", fake_score_from_news)

    market = make_snapshot(condition_id="c-news")
    strat.evaluate(market)
    strat.evaluate(market)
    # Second call within TTL should reuse the cached news score.
    assert calls["n"] == 1


def test_news_cache_expires_after_ttl(monkeypatch):
    cfg = make_config()
    cfg.news_api_key = "fake-key"
    strat = SentimentStrategy(cfg)
    strat.NEWS_CACHE_TTL_SECONDS = 0  # force immediate expiry

    calls = {"n": 0}
    monkeypatch.setattr(strat, "_score_from_news", lambda q: (calls.__setitem__("n", calls["n"] + 1), 0.8)[1])

    market = make_snapshot(condition_id="c-news")
    strat.evaluate(market)
    strat.evaluate(market)
    assert calls["n"] == 2  # expired each time -> refetched
