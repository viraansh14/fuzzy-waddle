"""Tests for momentum, mean reversion, and volume spike strategies."""

from datetime import datetime, timedelta, timezone

from polymarket_bot.strategies.momentum import MomentumStrategy
from polymarket_bot.strategies.mean_reversion import MeanReversionStrategy
from polymarket_bot.strategies.volume_spike import VolumeSpikeStrategy

from .conftest import make_snapshot, price_series


def _iso_in(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


# ── Momentum ────────────────────────────────────────────────────────────

def test_momentum_accelerating_aligned_with_uptrend():
    # Strong, steadily rising series: short MA well above long MA, last 3 rising.
    rising = [0.30 + 0.01 * i for i in range(30)]
    market = make_snapshot(price_history=price_series(rising))
    strat = MomentumStrategy(short_window=6, long_window=20, min_move_pct=5.0)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_yes  # bullish -> buy YES


def test_momentum_no_signal_when_flat():
    flat = [0.50] * 30
    market = make_snapshot(price_history=price_series(flat))
    strat = MomentumStrategy(short_window=6, long_window=20, min_move_pct=5.0)
    assert strat.evaluate(market) is None


def test_momentum_suppressed_when_spread_eats_edge():
    # Real momentum (~strong uptrend) but a huge spread makes the round-trip
    # cost exceed the move -> negative expectancy -> no signal.
    rising = [0.30 + 0.01 * i for i in range(30)]
    market = make_snapshot(price_history=price_series(rising), spread=0.40, mid=0.50)
    strat = MomentumStrategy(short_window=6, long_window=20, min_move_pct=5.0)
    assert strat.evaluate(market) is None


def test_momentum_bearish_buys_no():
    falling = [0.90 - 0.01 * i for i in range(30)]
    market = make_snapshot(price_history=price_series(falling))
    strat = MomentumStrategy(short_window=6, long_window=20, min_move_pct=5.0)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_no  # bearish -> buy NO


def test_momentum_acceleration_not_credited_for_counter_run():
    # Overall up momentum, but the last 3 ticks tick *down* (counter to trend).
    # The acceleration bonus must NOT be applied. We assert via confidence:
    # an identical series whose last 3 rise should score strictly higher.
    base = [0.30 + 0.01 * i for i in range(27)]
    counter = base + [0.62, 0.61, 0.60]  # dips at the end
    aligned = base + [0.60, 0.61, 0.62]  # rises at the end
    strat = MomentumStrategy(short_window=6, long_window=20, min_move_pct=5.0)
    sig_counter = strat.evaluate(make_snapshot(price_history=price_series(counter)))
    sig_aligned = strat.evaluate(make_snapshot(price_history=price_series(aligned)))
    assert sig_counter is not None and sig_aligned is not None
    assert sig_aligned.confidence > sig_counter.confidence


# ── Mean reversion ──────────────────────────────────────────────────────

def test_mean_reversion_overbought_spike_buys_no():
    # Flat history then a sharp spike up -> overbought -> expect BUY NO.
    series = [0.50] * 29 + [0.80]
    market = make_snapshot(price_history=price_series(series))
    strat = MeanReversionStrategy(z_threshold=1.8, lookback=30)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_no


def test_mean_reversion_oversold_spike_buys_yes():
    series = [0.50] * 29 + [0.20]
    market = make_snapshot(price_history=price_series(series))
    strat = MeanReversionStrategy(z_threshold=1.8, lookback=30)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_yes


def test_mean_reversion_needs_enough_history():
    series = [0.50] * 10
    market = make_snapshot(price_history=price_series(series))
    strat = MeanReversionStrategy(z_threshold=1.8, lookback=30)
    assert strat.evaluate(market) is None


def test_mean_reversion_no_signal_on_gradual_drift():
    # Gradual monotonic drift -> recency_ratio low -> skip (not a spike).
    series = [0.50 + 0.005 * i for i in range(30)]
    market = make_snapshot(price_history=price_series(series))
    strat = MeanReversionStrategy(z_threshold=1.8, lookback=30)
    assert strat.evaluate(market) is None


def test_mean_reversion_skips_near_resolution():
    # A reversion-worthy spike, but the market resolves in 2h -> the extreme is
    # likely justified, so the strategy must stand aside (docstring promise).
    series = [0.50] * 29 + [0.80]
    market = make_snapshot(price_history=price_series(series), end_date=_iso_in(2))
    strat = MeanReversionStrategy(z_threshold=1.8, lookback=30, min_hours_to_resolution=24.0)
    assert strat.evaluate(market) is None


def test_mean_reversion_fires_when_resolution_is_far():
    series = [0.50] * 29 + [0.80]
    market = make_snapshot(price_history=price_series(series), end_date=_iso_in(100))
    strat = MeanReversionStrategy(z_threshold=1.8, lookback=30, min_hours_to_resolution=24.0)
    assert strat.evaluate(market) is not None


def test_mean_reversion_skips_past_resolution():
    # End date already in the past (negative hours) -> still skip.
    series = [0.50] * 29 + [0.80]
    market = make_snapshot(price_history=price_series(series), end_date=_iso_in(-5))
    strat = MeanReversionStrategy(z_threshold=1.8, lookback=30, min_hours_to_resolution=24.0)
    assert strat.evaluate(market) is None


# ── Volume spike ────────────────────────────────────────────────────────

def test_volume_spike_short_history_uses_valid_baseline():
    # Exactly 6 prices: recent=last5, older window must be the single price
    # before them (prices[max(0,6-10):1] -> prices[0:1]), not an empty slice.
    series = [0.40, 0.50, 0.51, 0.52, 0.53, 0.55]
    market = make_snapshot(
        price_history=price_series(series),
        volume_24h=10_000.0,
        total_volume=20_000.0,  # spike_ratio = 0.5
    )
    strat = VolumeSpikeStrategy(volume_spike_ratio=0.10, min_24h_volume=5000)
    sig = strat.evaluate(market)
    # older_avg = 0.40, recent_avg = mean(0.50..0.55) -> clear positive change.
    assert sig is not None
    assert sig.token_id == market.token_yes


def test_volume_spike_requires_min_volume():
    series = [0.40, 0.50, 0.51, 0.52, 0.53, 0.55]
    market = make_snapshot(
        price_history=price_series(series),
        volume_24h=1000.0,  # below min
        total_volume=20_000.0,
    )
    strat = VolumeSpikeStrategy(volume_spike_ratio=0.10, min_24h_volume=5000)
    assert strat.evaluate(market) is None


def test_volume_spike_no_signal_without_price_move():
    series = [0.50] * 12
    market = make_snapshot(
        price_history=price_series(series),
        volume_24h=10_000.0,
        total_volume=20_000.0,
    )
    strat = VolumeSpikeStrategy(volume_spike_ratio=0.10, min_24h_volume=5000)
    assert strat.evaluate(market) is None


def test_volume_spike_downward_move_buys_no():
    series = [0.60, 0.50, 0.49, 0.48, 0.47, 0.45]
    market = make_snapshot(
        price_history=price_series(series),
        volume_24h=10_000.0,
        total_volume=20_000.0,
    )
    strat = VolumeSpikeStrategy(volume_spike_ratio=0.10, min_24h_volume=5000)
    sig = strat.evaluate(market)
    assert sig is not None
    assert sig.token_id == market.token_no
