# Polymarket Whale Watcher

AI-powered whale trade surveillance and analysis system for Polymarket prediction markets. Combines real-time monitoring, multi-dimensional anomaly detection, LLM-driven investigation with 14 autonomous tools, and signal accuracy tracking.

## Demo

<details>
<summary><b>Terminal Output</b> — Real-time whale detection and LLM analysis</summary>

```
╭──────────────────────────────────────────────────────────╮
│              🐋 Polymarket Whale Watcher                 │
│                                                          │
│  Markets Monitored:  50                                  │
│  Polling Interval:   15s                                 │
│  Min Trade Size:     $1,000                              │
│  Price Range:        0 - 0.7                             │
╰──────────────────────────────────────────────────────────╯

[14:22:51] 🐋 WHALE DETECTED on "Will MegaETH launch a token by June 30, 2026?"
           BUY Yes @ 0.4200 | $92,336 USDC | Wallet: 0x7a3b...f91e
           Anomaly Score: 0.78/1.00

[14:22:53] 🤖 LLM Analysis started (model: gemini-3-flash-preview)
           → Tool call: search_web("MegaETH token launch date 2026")
           → Tool call: search_twitter("MegaETH $METH token TGE")
           → Tool call: get_protocol_tvl("megaeth")
           → Tool call: get_contract_info("0x4f9b...2a1c")
           → Tool call: search_telegram("MegaETH launch")

[14:23:07] ✅ Analysis complete
           Information Asymmetry Score: 0.72 (HIGH)
           Recommendation: BUY Yes | Confidence: 0.75
           Report saved: reports/20260415/...

[15:00:00] 📊 Resolution check: 3 markets resolved
           → "EdgeX FDV above 400M" resolved YES — Signal CORRECT (ROI: +142%)
           → "Will Trump talk to Rutte" resolved NO — Signal INCORRECT
           → "Over 9M committed to P2P" resolved YES — Signal CORRECT (ROI: +67%)
```

See full example: [docs/examples/sample_terminal.txt](docs/examples/sample_terminal.txt)
</details>

<details>
<summary><b>Analysis Report</b> — LLM investigation with tool-use</summary>

Each whale trade generates a detailed markdown report:

- **Trade details** — amount, direction, price, trader wallet
- **Trader profile** — rank, PnL, history, recent trades
- **LLM investigation** — 5 autonomous tool calls (web, Twitter, Telegram, on-chain, DeFi)
- **Information asymmetry assessment** — score, evidence, reasoning

Example findings:
> *"New ERC-20 contract deployed by MegaETH deployer wallet 6 hours before trade — not yet publicly announced. KOL tweets about insider knowledge preceded the trade by ~3 hours."*
>
> **Information Asymmetry Score: 0.72** | Trader Credibility: HIGH

See full example: [docs/examples/sample_report.md](docs/examples/sample_report.md)
</details>

<details>
<summary><b>Daily Briefing</b> — Automated intelligence summary</summary>

Daily briefings include:
- High-confidence signals with analysis
- Price volatility alerts
- Historical signal performance (win rate, ROI by confidence tier)

Example stats:
| Metric | Value |
|--------|-------|
| Win Rate | **63.5%** |
| Avg ROI | **+28.3%** |
| Signals with IAS >= 60% | 3 today |

See full example: [docs/examples/sample_briefing.md](docs/examples/sample_briefing.md)
</details>

## Features

- **Real-Time Monitoring** — Parallel per-market polling of 50+ trending markets
- **Multi-Dimensional Anomaly Detection** — Scores trades on size, price uncertainty, time-of-day, trader deviation, and cluster signals
- **Trader Profiling** — Leaderboard ranking, trading history, recent behavior analysis
- **LLM Analysis with Tool-Use** — 14 autonomous tools (Twitter, web search, Telegram, crypto prices, stocks, economic data, Congress bills, DeFi metrics, on-chain analysis)
- **Signal Accuracy Tracking** — Automatic market resolution checking, win rate stats by confidence tier
- **Daily Intelligence Briefing** — Automated 10:00 AM daily summary with high-confidence signals
- **Email Alerts** — Real-time notifications for high-IAS signals (>= 60%)
- **Web Dashboard** — FastAPI-based signal performance dashboard
- **Leading Signal Research** — "Price leads news" dataset collection

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Start the whale watcher
python -m src.main run

# With debug logging
python -m src.main run --debug
```

## Configuration

Copy `.env.example` to `.env` and configure:

**Required:**
- `GEMINI_API_KEY` — Gemini API key for LLM analysis
- `INTERNAL_API_URL` / `INTERNAL_API_KEY` — Trade data API (currently using internal API, can be replaced with [Polymarket CLOB API](https://docs.polymarket.com/))

**Optional:**
- `TAVILY_API_KEY` — Web search (primary)
- `TWITTER_API_KEY` — Twitter sentiment search
- `POLYGON_API_KEY` — Stock/ETF data
- `FRED_API_KEY` — Economic indicators
- `ETHERSCAN_API_KEY` — On-chain data
- `EMAIL_*` — Email alert settings
- `MIN_TRADE_SIZE_USD` — Minimum trade size (default: 1000)
- `MIN_PRICE` / `MAX_PRICE` — Price range filter (default: 0-0.7)

## Commands

```bash
# Start monitoring
python -m src.main run [--debug]

# Check trending markets
python -m src.main check-markets --limit 20

# Test LLM analysis on a specific market
python -m src.main test-analyze <market_id>

# Generate daily briefing
python -m src.main briefing --today
python -m src.main briefing --date 2026-04-17

# Migrate legacy JSON signals to SQLite
python -m src.main migrate

# Start web dashboard
python -m src.main dashboard --port 8000
```

## Architecture

```
Polymarket API        Internal Trade API       Gamma API
       |                      |                     |
       v                      v                     v
 MarketFetcher          TradeMonitor          PriceMonitor
       |                      |                     |
       v                      v                     v
 TrendingMarkets      AnomalyDetector      VolatilityAnalyzer
                              |
                              v
                    LLMAnalyzer (14 tools)
                     |               |
                     v               v
              AnomalySignal    Reports/Alerts
                     |
                     v
              ResolutionTracker → StatsEngine → Dashboard
```

### Project Structure

```
src/
├── config/settings.py              # Environment configuration
├── models/
│   ├── market.py                   # Market, TrendingMarket
│   ├── trade.py                    # TradeActivity, WhaleTrade, TraderRanking
│   ├── decision.py                 # TradeRecommendation, LLMDecision
│   ├── anomaly_signal.py           # AnomalySignal (stored signal)
│   └── leading_signal.py           # LeadingSignal (price leads news)
├── services/
│   ├── market_fetcher.py           # Polymarket API, market filtering
│   ├── trade_monitor.py            # Per-market parallel monitoring
│   ├── price_monitor.py            # Volatility detection
│   ├── anomaly_detector.py         # Multi-dimensional anomaly scoring
│   ├── llm_analyzer.py             # LLM with tool-use (14 tools, 5 rounds max)
│   ├── volatility_analyzer.py      # Leading signal detection
│   ├── trader_profiler.py          # Trader profile generation
│   ├── tools.py                    # Tool registry
│   ├── daily_briefing.py           # Daily summary generation
│   ├── resolution_tracker.py       # Market resolution checking
│   ├── stats_engine.py             # Performance statistics
│   ├── anomaly_history.py          # Signal storage (SQLite)
│   ├── coingecko.py                # Crypto prices
│   ├── fred.py                     # Economic indicators (FRED)
│   ├── polygon.py                  # Stock prices & news
│   ├── congress.py                 # US legislation
│   ├── defillama.py                # DeFi TVL, revenue, token unlocks
│   ├── etherscan.py                # On-chain wallet analysis
│   ├── twitter_search.py           # Twitter API
│   ├── telegram_search.py          # Telegram channels
│   └── web_search.py               # Unified search (Tavily → Serper → DDG)
├── db/database.py                  # SQLite signal storage
├── prompts/
│   ├── whale_analyzer.py           # LLM system prompt & tool schemas
│   └── volatility_analyzer.py      # Volatility analysis prompt
├── dashboard.py                    # FastAPI web dashboard
└── main.py                         # Entry point (WhaleWatcher orchestrator)

data/                               # SQLite database + processed transactions
reports/                            # Analysis reports (by date)
daily_briefings/                    # Daily intelligence summaries
leading_signals/                    # "Price leads news" research dataset
price_volatility/                   # Volatility alert records
```

## How It Works

### 1. Market Selection
- Fetches top trending markets by 24h volume from Polymarket Gamma API
- Filters out sports, weather, and short-term price markets
- Refreshes market list every 15 minutes

### 2. Trade Monitoring
- Runs parallel async tasks per monitored market
- Polls internal API incrementally (new trades since last check)
- Rate-limited at 5 QPS to respect API limits
- Deduplicates by transaction hash

### 3. Anomaly Detection
Multi-dimensional scoring on 5 axes:
- **Size** — Trade size relative to market 24h volume
- **Price uncertainty** — Closer to 0.5 = more interesting
- **Time-of-day** — ET hour-based suspicion weights
- **Trader deviation** — Trade size vs trader's historical average
- **Cluster signal** — Same-direction trades within 5-minute window

### 4. LLM Investigation
When a whale trade triggers:
1. Builds rich context: trade details + trader profile + market data + historical signals
2. LLM autonomously uses tools to investigate (up to 5 rounds):
   - Search Twitter/Telegram for insider chatter
   - Check crypto prices, DeFi metrics, on-chain activity
   - Look up stock movements, economic data, Congress bills
   - Web search for breaking news
3. Produces structured recommendation: action, confidence, information asymmetry score (0-1)
4. Generates markdown report saved to `reports/`

### 5. Signal Tracking
- Resolution tracker checks every 30 minutes for resolved markets
- Validates signal correctness against actual outcomes
- Computes theoretical ROI for each signal
- Stats engine aggregates win rates by confidence tier

## Safety

- Trade execution disabled by default (`ENABLE_TRADE_EXECUTION=false`)
- Position size capped at 20% of balance if enabled
- Minimum 60% confidence threshold for execution
- Price range filter avoids obvious outcomes (0-0.7)
- All decisions logged for audit trail
- Rate limiting on all external APIs

## Disclaimer

This system is for research and educational purposes. Prediction market trading involves significant risk. Never trade with funds you cannot afford to lose. Always verify recommendations independently.
