# Polymarket Whale Watcher

AI-powered whale trade detection and analysis bot for Polymarket prediction markets.

## Overview

This bot combines the market monitoring approach from `polymarket-copy-trading-bot` with the AI analysis capabilities of `PolyMarket-trading-AI-model` to:

1. **Fetch Trending Markets** - Gets the most active markets by 24-hour volume
2. **Monitor for Whale Trades** - Watches for large trades ($10,000+ USD)
3. **Filter Anomalies** - Only triggers on trades with prices between 0.2-0.8 (uncertain markets)
4. **AI Analysis** - Uses LLM to analyze the whale trade and market context
5. **Trade Execution** - Optionally executes trades based on AI recommendations

## Installation

```bash
# Clone or navigate to the project
cd polymarket-whale-watcher

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Required settings:
- `OPENAI_API_KEY` - Your OpenAI API key for LLM analysis
- `POLYGON_WALLET_PRIVATE_KEY` - Your wallet private key (for trade execution)

Optional settings:
- `MIN_TRADE_SIZE_USD` - Minimum trade size to trigger (default: 10000)
- `MIN_PRICE` / `MAX_PRICE` - Price range filter (default: 0.2-0.8)
- `ENABLE_TRADE_EXECUTION` - Set to `true` to enable actual trading (default: false)

## Usage

### Start the Whale Watcher

```bash
# Run the bot
python -m src.main run

# With debug logging
python -m src.main run --debug
```

### Check Trending Markets

```bash
python -m src.main check-markets --limit 20
```

### Check Wallet Balance

```bash
python -m src.main check-balance
```

### Test LLM Analysis

```bash
python -m src.main test-analyze <market_id>
```

## Architecture

```
src/
├── config/
│   └── settings.py          # Configuration management
├── models/
│   ├── market.py            # Market data models
│   ├── trade.py             # Trade/whale trade models
│   └── decision.py          # LLM decision models
├── services/
│   ├── market_fetcher.py    # Fetches trending markets
│   ├── trade_monitor.py     # Monitors markets for trades
│   ├── anomaly_detector.py  # Detects whale trades
│   ├── llm_analyzer.py      # AI analysis
│   └── trade_executor.py    # Trade execution
├── prompts/
│   └── whale_analyzer.py    # LLM prompts
├── utils/
│   └── logger.py            # Logging utilities
└── main.py                  # Entry point
```

## How It Works

### 1. Market Selection
- Fetches top markets sorted by 24-hour trading volume
- Filters for active, CLOB-enabled markets
- Refreshes market list every 5 minutes

### 2. Trade Monitoring
- Polls Polymarket Data API for trade activity
- Checks each monitored market every N seconds (configurable)
- Tracks processed transactions to avoid duplicates

### 3. Anomaly Detection
Triggers when:
- Trade size >= $10,000 USD
- Trade price between 0.2 and 0.8 (uncertain outcome)

### 4. LLM Analysis
When a whale trade is detected:
- Formats trade context (amount, direction, price, market info)
- Sends to GPT-4 with superforecaster methodology
- Extracts structured recommendation (BUY/SELL/HOLD)

### 5. Trade Execution (Optional)
If enabled and LLM recommends:
- Calculates position size (capped at 20% of balance)
- Executes market order via Polymarket CLOB
- Logs execution result

## Safety Features

- Trade execution disabled by default
- Maximum position size capped at 20%
- Minimum confidence threshold (60%) for trade execution
- Price range filter to avoid obvious outcomes
- Comprehensive logging for audit trail

## Disclaimer

This bot is for educational and research purposes. Trading on prediction markets involves significant risk. Never trade with funds you cannot afford to lose. Always verify the bot's recommendations independently.
