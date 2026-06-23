# Polymarket Automated Trading Bot

Fully automated trading bot for [Polymarket](https://polymarket.com) prediction markets. Scans live markets, runs multiple alpha strategies, manages risk, and executes trades autonomously.

## Architecture

```
main.py                        # Entry point
polymarket_bot/
  config.py                    # Environment-based configuration
  client.py                    # Polymarket CLOB + Gamma API wrapper
  analyzer.py                  # Market scanner, enrichment, filtering
  risk_manager.py              # Position sizing (Kelly), stop-loss, take-profit
  executor.py                  # Order execution engine (limit + market orders)
  bot.py                       # Main orchestrator loop
  strategies/
    base.py                    # Signal/Strategy interfaces
    momentum.py                # Trend-following via moving average crossovers
    value.py                   # Mispricing detection (negative vig, wide spreads)
    volume_spike.py            # Unusual volume detection
    sentiment.py               # News headline sentiment scoring
    mean_reversion.py          # Z-score based reversion on overreactions
    orderbook_imbalance.py     # Resting bid/ask liquidity imbalance
    resolution_drift.py        # Stable favorites converging near resolution
  aggregation.py               # Regime-aware signal aggregation / conflict resolution
```

## How It Works

The bot runs a continuous loop:

1. **Scan** -- Fetches active markets from Polymarket's Gamma API
2. **Enrich** -- Builds snapshots with orderbook data, price history, liquidity
3. **Filter** -- Removes illiquid, low-volume, or near-resolved markets
4. **Analyze** -- Runs 7 strategies on every market to generate trading signals
5. **Aggregate** -- Classifies each market's regime (trend/range), suppresses
   signals that don't fit it, resolves directional conflicts, and keeps the best
   signal per market
6. **Size** -- Calculates position size via half-Kelly criterion
7. **Execute** -- Places limit or market orders via the CLOB API
8. **Monitor** -- Checks all positions for stop-loss / take-profit / staleness
9. **Repeat** -- Sleeps and loops

## Strategies

| Strategy | Edge | Signal |
|----------|------|--------|
| **Momentum** | Trend continuation | Short MA > Long MA with acceleration |
| **Value** | Mispricing | YES + NO < $1.00 (negative vig) or wide spreads |
| **Volume Spike** | Informed flow | 24h volume spike + directional price move |
| **Sentiment** | News alpha | Headline sentiment via NewsAPI keyword scoring |
| **Mean Reversion** | Overreaction | Z-score > 1.8 std devs with rapid recent move (skipped near resolution) |
| **Order Book Imbalance** | Microstructure | Resting bid vs ask liquidity imbalance |
| **Resolution Drift** | Time decay | Calm, moderate favorite converging as resolution nears |

Trend-following strategies (Momentum, Volume Spike, Sentiment) and the
counter-trend strategy (Mean Reversion) carry conflicting edges, so signals are
passed through a **regime-aware aggregator**: each market is classified as
trending or ranging, signals whose behaviour doesn't fit the regime are dropped,
and any remaining directional conflict on a market is resolved by backing the
stronger side (penalised for the disagreement) or standing aside when it's a
coin flip. Directional strategies also require the move to clear the round-trip
spread cost before signalling.

## Risk Management

- **Kelly-based sizing** -- Half-Kelly with hard caps per position
- **Stop loss** -- Auto-exit at configurable % drawdown (default 15%)
- **Take profit** -- Auto-exit at configurable % gain (default 40%)
- **Max exposure** -- Total portfolio cap (default $500)
- **Max positions** -- Concurrent position limit (default 10)
- **Stale exit** -- Closes flat positions after 48 hours
- **State persistence** -- Saves/restores positions across restarts

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env` with your Polymarket API credentials. You need:
- A Polygon wallet private key
- CLOB API credentials (key, secret, passphrase) -- derive these via the `py-clob-client` SDK

### 3. Run in dry-run mode (paper trading)

```bash
python main.py
```

The bot starts in **dry-run mode** by default (`DRY_RUN=true`). It will scan markets, generate signals, and simulate trades without placing real orders.

### 4. Go live

Set `DRY_RUN=false` in `.env` when ready. Start with small position sizes.

## Configuration

All settings are in `.env`:

| Variable | Default | Description |
|----------|---------|-------------|
| `POLYMARKET_API_KEY` | -- | CLOB API key |
| `POLYMARKET_API_SECRET` | -- | CLOB API secret |
| `POLYMARKET_API_PASSPHRASE` | -- | CLOB API passphrase |
| `POLYMARKET_PRIVATE_KEY` | -- | Polygon wallet private key |
| `MAX_POSITION_SIZE_USDC` | 100 | Max USDC per trade |
| `MAX_TOTAL_EXPOSURE_USDC` | 500 | Max total portfolio exposure |
| `MAX_POSITIONS` | 10 | Max concurrent positions |
| `STOP_LOSS_PCT` | 15 | Stop loss trigger (%) |
| `TAKE_PROFIT_PCT` | 40 | Take profit trigger (%) |
| `TRADE_LOOP_INTERVAL_SECONDS` | 60 | Seconds between cycles |
| `MIN_LIQUIDITY_USDC` | 1000 | Min market liquidity to trade |
| `MIN_VOLUME_USDC` | 5000 | Min market total volume |
| `DRY_RUN` | true | Paper trading mode |
| `NEWS_API_KEY` | -- | Optional NewsAPI key for sentiment strategy |

## Testing

The strategy, risk, and execution logic is covered by a fast, network-free
unit suite (fakes stand in for the Polymarket APIs).

```bash
pip install -r requirements-dev.txt
pytest
```

Tests run automatically on every pull request via GitHub Actions
(`.github/workflows/tests.yml`).

## Disclaimer

This bot is for educational and research purposes. Trading on prediction markets involves risk of loss. Use at your own risk. Ensure you comply with all applicable laws and Polymarket's terms of service in your jurisdiction.
