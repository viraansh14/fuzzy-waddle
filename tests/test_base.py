"""Tests for shared strategy helpers (extract_prices)."""

import math

from polymarket_bot.strategies.base import extract_prices


def test_extract_prices_keeps_valid_positive_prices():
    history = [{"p": 0.4}, {"p": 0.5}, {"p": 0.6}]
    assert extract_prices(history) == [0.4, 0.5, 0.6]


def test_extract_prices_excludes_zero():
    # A missing/zero price must not be treated as a real 0.0 data point.
    history = [{"p": 0.4}, {"p": 0}, {"p": 0.6}]
    assert extract_prices(history) == [0.4, 0.6]


def test_extract_prices_excludes_nan():
    history = [{"p": 0.4}, {"p": float("nan")}, {"p": 0.6}]
    result = extract_prices(history)
    assert result == [0.4, 0.6]
    assert all(not math.isnan(p) for p in result)


def test_extract_prices_handles_missing_keys():
    # Entries without "p" or "price" default to 0 and are dropped.
    history = [{"p": 0.4}, {"foo": 1}, {"price": 0.6}]
    assert extract_prices(history) == [0.4, 0.6]


def test_extract_prices_supports_price_key_alias():
    history = [{"price": 0.3}, {"price": 0.7}]
    assert extract_prices(history) == [0.3, 0.7]


def test_extract_prices_skips_unparseable_values():
    history = [{"p": "abc"}, {"p": 0.5}, {"p": None}]
    assert extract_prices(history) == [0.5]


def test_extract_prices_empty():
    assert extract_prices([]) == []
