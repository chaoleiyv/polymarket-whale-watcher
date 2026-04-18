"""
Trade monitoring service - per-market parallel architecture.

Each market runs its own independent async task that:
1. Polls the internal API for new trades (incremental via start_ts)
2. Detects whale trades
3. Fetches trader ranking + history in parallel
4. Fires the whale callback (LLM report generation) without blocking other markets

Modeled after paper_trading/paper_trading.py's _market_loop pattern.
"""
import asyncio
import json
import logging
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Callable, Awaitable

import httpx

from src.config import get_settings
from src.models.market import Market, TrendingMarket
from src.models.trade import (
    TradeActivity, WhaleTrade, TraderRanking, TraderHistory,
    EventPosition, MarketTopTrader,
)
from src.services.anomaly_detector import AnomalyDetector

logger = logging.getLogger(__name__)

# Gamma API for fetching latest market prices
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

# Internal API for trade data (more stable than official data-api)
# URL and key loaded from settings (.env)

# File to persist processed transaction hashes
PROCESSED_TXNS_FILE = Path(__file__).parent.parent.parent / "data" / "processed_transactions.json"


class TradeMonitor:
    """
    Monitors Polymarket markets for large trades.

    Architecture: one asyncio.Task per market, fully parallel.
    """

    def __init__(
        self,
        on_whale_detected: Optional[Callable[[WhaleTrade], Awaitable[None]]] = None,
    ):
        self.settings = get_settings()

        # Official API (for trader ranking/history queries only)
        self.data_api_url = "https://data-api.polymarket.com"
        self.trades_endpoint = f"{self.data_api_url}/trades"
        self.leaderboard_endpoint = f"{self.data_api_url}/v1/leaderboard"
        self._client = httpx.AsyncClient(timeout=30.0)

        # Internal API client for trade data
        self._internal_api_url = self.settings.internal_api_url
        self._internal_client = httpx.AsyncClient(
            timeout=30.0,
            headers={
                "X-API-Key": self.settings.internal_api_key,
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
            },
        )

        # Per-market last-fetch timestamps for incremental polling
        self._market_last_ts: Dict[str, int] = {}

        # Global rate limiter for internal API (matches paper_trading: 5 QPS max)
        # NOTE: Lock created lazily in run() to avoid "attached to different loop" error
        self._api_lock: Optional[asyncio.Lock] = None
        self._api_sem: Optional[asyncio.Semaphore] = None  # concurrency limiter
        self._api_last_request: float = 0.0
        self._api_global_interval: float = 1.0  # min 1s between requests = 1 QPS

        # Cache for trader rankings to avoid repeated API calls
        self._trader_ranking_cache: Dict[str, TraderRanking] = {}

        # Markets being monitored: market_id -> Market
        self._monitored_markets: Dict[str, Market] = {}

        # Track processed transactions to avoid duplicates
        self._processed_txns: Set[str] = set()
        self._load_processed_txns()

        # Anomaly detector for multi-dimensional scoring
        self._anomaly_detector = AnomalyDetector()

        # Callback for whale detection
        self._on_whale_detected = on_whale_detected

        # Control flag and per-market tasks
        self._running = False
        self._market_tasks: Dict[str, asyncio.Task] = {}

        # Flag to track if initial scan is complete (ignore historical trades)
        self._initial_scan_complete = False

    # ================================================================
    # Persistence
    # ================================================================

    def _load_processed_txns(self):
        """Load processed transaction hashes from JSON file."""
        try:
            if PROCESSED_TXNS_FILE.exists():
                with open(PROCESSED_TXNS_FILE, "r") as f:
                    data = json.load(f)
                    self._processed_txns = set(data.get("transactions", []))
                    logger.info(f"Loaded {len(self._processed_txns)} processed transactions from file")
        except json.JSONDecodeError as e:
            logger.warning(f"Corrupted JSON file, backing up and starting fresh: {e}")
            if PROCESSED_TXNS_FILE.exists():
                backup_file = PROCESSED_TXNS_FILE.with_suffix('.json.bak')
                PROCESSED_TXNS_FILE.rename(backup_file)
                logger.info(f"Backed up corrupted file to {backup_file}")
            self._processed_txns = set()
        except Exception as e:
            logger.warning(f"Failed to load processed transactions: {e}")
            self._processed_txns = set()

    def _save_processed_txns(self):
        """Save processed transaction hashes to JSON file."""
        try:
            PROCESSED_TXNS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(PROCESSED_TXNS_FILE, "w") as f:
                json.dump({
                    "transactions": list(self._processed_txns),
                    "count": len(self._processed_txns),
                    "last_updated": datetime.now().isoformat()
                }, f, indent=2)
            logger.debug(f"Saved {len(self._processed_txns)} processed transactions to file")
        except Exception as e:
            logger.warning(f"Failed to save processed transactions: {e}")

    async def close(self):
        """Cleanup resources."""
        self._save_processed_txns()
        await self._client.aclose()
        await self._internal_client.aclose()

    # ================================================================
    # Market list management
    # ================================================================

    def set_monitored_markets(self, markets: List[TrendingMarket]):
        """Update the list of markets to monitor."""
        self._monitored_markets = {}
        for tm in markets:
            if tm.market.id:
                self._monitored_markets[tm.market.id] = tm.market
        logger.info(f"Now monitoring {len(self._monitored_markets)} markets")

    # ================================================================
    # Internal API: fetch trades
    # ================================================================

    _MAX_RETRIES = 3
    _RETRY_BACKOFF = [1, 2, 4]  # seconds between retries

    async def fetch_market_trades(self, market_id: str) -> List[TradeActivity]:
        """
        Fetch recent taker trades for a market using the /flows API.

        /flows returns one record per taker per transaction (already aggregated
        across maker fills), with accurate usd_amount and real execution price.
        Uses incremental polling via start_ts.
        Retries up to _MAX_RETRIES times on connection/timeout errors.
        """
        try:
            last_ts = self._market_last_ts.get(market_id)

            params: Dict[str, object] = {
                "market_id": market_id,
                "role": "taker",
                # First poll: only fetch recent 50 trades to record txn hashes
                # Subsequent polls: incremental via start_ts, small data
                "limit": 50 if last_ts is None else 500,
                "desc": True,
            }

            if last_ts is not None:
                params["start_ts"] = last_ts + 1

            # Semaphore limits concurrent requests; Lock enforces per-request interval
            sem = self._api_sem or asyncio.Semaphore(20)
            last_err: Optional[Exception] = None
            async with sem:
              for attempt in range(self._MAX_RETRIES):
                try:
                    # Global rate limit
                    async with self._api_lock:
                        now = _time.monotonic()
                        wait = self._api_global_interval - (now - self._api_last_request)
                        if wait > 0:
                            await asyncio.sleep(wait)
                        self._api_last_request = _time.monotonic()

                    response = await self._internal_client.get(
                        f"{self._internal_api_url}/flows", params=params,
                    )
                    response.raise_for_status()
                    break  # success
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (502, 503, 504) and attempt < self._MAX_RETRIES - 1:
                        delay = self._RETRY_BACKOFF[attempt]
                        logger.debug(
                            f"Internal API {e.response.status_code} for {market_id} "
                            f"(attempt {attempt + 1}/{self._MAX_RETRIES}), "
                            f"retrying in {delay}s"
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise  # don't retry other HTTP errors
                except httpx.HTTPError as e:
                    last_err = e
                    if attempt < self._MAX_RETRIES - 1:
                        delay = self._RETRY_BACKOFF[attempt]
                        logger.debug(
                            f"Internal API retry for {market_id} "
                            f"(attempt {attempt + 1}/{self._MAX_RETRIES}): "
                            f"{type(e).__name__}, retrying in {delay}s"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.warning(
                            f"Internal API connection error for {market_id} "
                            f"(attempt {attempt + 1}/{self._MAX_RETRIES}, giving up): "
                            f"{type(e).__name__}: {e}"
                        )
                        return []
              else:
                # All retries exhausted (shouldn't reach here, but just in case)
                return []

            data = response.json()
            if not data:
                return []

            activities = []
            max_ts = last_ts or 0

            for item in data:
                try:
                    raw_direction = item.get("direction", "")

                    # Only track BUY trades (new positions).
                    # SELL may just be exiting a position, not a directional signal.
                    if raw_direction != "BUY":
                        continue

                    token_amount = float(item.get("token_amount", 0) or 0)
                    raw_price = float(item.get("price", 0) or 0)
                    usdc_size = float(item.get("usd_amount", 0) or 0)

                    # No normalization — keep real price and outcome:
                    # - nonusdc_side=token1: BUY Yes token at raw_price
                    # - nonusdc_side=token2: BUY No token at raw_price
                    nonusdc_side = item.get("nonusdc_side", "token1")
                    outcome = "Yes" if nonusdc_side == "token1" else "No"

                    ts = int(item.get("timestamp", 0) or 0)

                    if ts > max_ts:
                        max_ts = ts

                    activity = TradeActivity(
                        transaction_hash=f"{item.get('transaction_hash', '')}-{item.get('log_index', '')}",
                        timestamp=ts,
                        condition_id=item.get("condition_id", market_id),
                        asset=item.get("condition_id", ""),
                        side="BUY",
                        size=token_amount,
                        usdc_size=usdc_size,
                        price=raw_price,
                        outcome=outcome,
                        outcome_index=0 if outcome == "Yes" else 1,
                        title="",
                        slug=None,
                        event_slug=None,
                        proxy_wallet=item.get("address"),
                        name=None,
                    )
                    activities.append(activity)
                except Exception as e:
                    logger.debug(f"Failed to parse /flows trade: {e}")
                    continue

            if max_ts > 0:
                self._market_last_ts[market_id] = max_ts

            return activities

        except httpx.HTTPStatusError as e:
            logger.warning(
                f"Flows API HTTP {e.response.status_code} for {market_id}: "
                f"{e.response.text[:200]}"
            )
            return []
        except Exception as e:
            logger.warning(f"Error fetching flows for {market_id}: {type(e).__name__}: {e}")
            return []

    # ================================================================
    # Official API: trader info (ranking + history)
    # ================================================================

    async def fetch_trader_ranking(self, wallet_address: str) -> Optional[TraderRanking]:
        """Fetch trader ranking from the leaderboard API."""
        if not wallet_address:
            return None

        if wallet_address in self._trader_ranking_cache:
            return self._trader_ranking_cache[wallet_address]

        try:
            params = {
                "user": wallet_address,
                "timePeriod": "ALL",
                "orderBy": "PNL",
            }
            response = await self._client.get(self.leaderboard_endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            if data and len(data) > 0:
                user_data = data[0]
                ranking = TraderRanking(
                    rank=user_data.get("rank"),
                    pnl=float(user_data.get("pnl", 0) or 0),
                    volume=float(user_data.get("vol", 0) or 0),
                    user_name=user_data.get("userName"),
                    profile_image=user_data.get("profileImage"),
                    verified=bool(user_data.get("verifiedBadge")),
                    time_period="ALL",
                )
                self._trader_ranking_cache[wallet_address] = ranking
                logger.debug(f"Fetched ranking for {wallet_address}: #{ranking.rank}")
                return ranking

            return None

        except httpx.HTTPError as e:
            logger.debug(f"HTTP error fetching ranking for {wallet_address}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching ranking for {wallet_address}: {e}")
            return None

    async def fetch_trader_history(self, wallet_address: str) -> Optional[TraderHistory]:
        """Fetch trader's recent trading history."""
        if not wallet_address:
            return None

        try:
            params = {
                "user": wallet_address,
                "limit": 100,
            }
            response = await self._client.get(self.trades_endpoint, params=params)
            response.raise_for_status()
            data = response.json()

            if not data:
                return None

            total_trades = len(data)
            total_volume = 0.0
            large_trades_count = 0
            recent_markets: Set[str] = set()
            recent_trades = []

            for trade in data:
                usdc_size = float(trade.get("usdcSize", 0) or 0)
                if usdc_size == 0:
                    size = float(trade.get("size", 0) or 0)
                    price = float(trade.get("price", 0) or 0)
                    usdc_size = size * price

                total_volume += usdc_size

                if usdc_size >= 5000:
                    large_trades_count += 1
                    recent_trades.append({
                        "side": trade.get("side", ""),
                        "usdc_size": usdc_size,
                        "price": float(trade.get("price", 0) or 0),
                        "title": trade.get("title", trade.get("marketTitle", "")),
                        "timestamp": trade.get("timestamp", 0),
                    })

                title = trade.get("title", trade.get("marketTitle", ""))
                if title:
                    recent_markets.add(title[:50])

            avg_trade_size = total_volume / total_trades if total_trades > 0 else 0
            recent_trades.sort(key=lambda x: x["usdc_size"], reverse=True)

            return TraderHistory(
                total_trades=total_trades,
                total_volume=total_volume,
                avg_trade_size=avg_trade_size,
                large_trades_count=large_trades_count,
                recent_markets=list(recent_markets)[:10],
                recent_trades=recent_trades[:10],
            )

        except httpx.HTTPError as e:
            logger.debug(f"HTTP error fetching history for {wallet_address}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching history for {wallet_address}: {e}")
            return None

    # ================================================================
    # Event positions & market top traders
    # ================================================================

    async def fetch_whale_event_positions(
        self,
        wallet_address: str,
        event_slug: str,
        current_condition_id: str,
    ) -> List[EventPosition]:
        """
        Fetch the whale's current positions across all markets in the same event.

        Uses the Polymarket data API positions endpoint directly:
        GET https://data-api.polymarket.com/positions?user=<wallet>
        Then filters by event_slug to find related holdings.
        """
        if not wallet_address or not event_slug:
            return []

        try:
            response = await self._client.get(
                f"{self.data_api_url}/positions",
                params={"user": wallet_address},
            )
            response.raise_for_status()
            all_positions = response.json()
            if not all_positions:
                return []

            # Filter positions belonging to the same event, excluding current market
            result = []
            for pos in all_positions:
                pos_event_slug = pos.get("eventSlug", "")
                pos_condition_id = pos.get("conditionId", "")

                if pos_event_slug != event_slug:
                    continue
                if pos_condition_id == current_condition_id:
                    continue

                size = float(pos.get("size", 0) or 0)
                if size == 0:
                    continue  # skip empty positions

                outcome = pos.get("outcome", "Yes")
                avg_price = float(pos.get("avgPrice", 0) or 0)
                cur_price = float(pos.get("curPrice", 0) or 0)
                current_value = float(pos.get("currentValue", 0) or 0)
                initial_value = float(pos.get("initialValue", 0) or 0)
                cash_pnl = float(pos.get("cashPnl", 0) or 0)
                title = pos.get("title", "")

                # Build human-readable summary
                if outcome == "Yes":
                    side_summary = f"Holding Yes {size:,.0f} tokens @ avg {avg_price:.2%}, current {cur_price:.2%}"
                else:
                    side_summary = f"Holding No {size:,.0f} tokens @ avg {avg_price:.2%}, current {cur_price:.2%}"

                result.append(EventPosition(
                    market_question=title,
                    condition_id=pos_condition_id,
                    outcome=outcome,
                    size=size,
                    avg_price=avg_price,
                    current_price=cur_price,
                    current_value=current_value,
                    initial_value=initial_value,
                    pnl=cash_pnl,
                    side_summary=side_summary,
                ))

            # Sort by position value descending
            result.sort(key=lambda x: x.current_value, reverse=True)
            logger.debug(
                f"Found {len(result)} event positions for {wallet_address} "
                f"in event '{event_slug}'"
            )
            return result

        except Exception as e:
            logger.warning(f"Error fetching whale event positions: {e}")
            return []

    async def fetch_market_top_traders(
        self, market_id: str, condition_id: str = "",
        outcome_prices: Optional[List[float]] = None, top_n: int = 5,
    ) -> tuple[List[MarketTopTrader], List[MarketTopTrader]]:
        """
        Fetch top holders (bulls and bears) for a market.

        Uses the official Polymarket data-api /holders endpoint which returns
        the top position holders for each outcome token, sorted by amount.

        Returns:
            (top_buyers, top_sellers) — each up to top_n entries.
            top_buyers = top Yes token holders (bullish).
            top_sellers = top No token holders (bearish).
        """
        if not condition_id:
            return [], []

        # outcome_prices: [yes_price, no_price]
        yes_price = outcome_prices[0] if outcome_prices and len(outcome_prices) > 0 else 0.5
        no_price = outcome_prices[1] if outcome_prices and len(outcome_prices) > 1 else 0.5

        try:
            response = await self._client.get(
                f"{self.data_api_url}/holders",
                params={"market": condition_id, "limit": top_n},
            )
            response.raise_for_status()
            data = response.json()
            if not data:
                return [], []

            top_buyers = []
            top_sellers = []

            for token_group in data:
                holders = token_group.get("holders", [])
                if not holders:
                    continue

                # outcomeIndex: 0 = Yes (bulls), 1 = No (bears)
                outcome_index = holders[0].get("outcomeIndex", 0)
                token_price = yes_price if outcome_index == 0 else no_price

                for h in holders[:top_n]:
                    wallet = h.get("proxyWallet", "")
                    name = h.get("name") or h.get("pseudonym") or None
                    amount = float(h.get("amount", 0) or 0)
                    # Convert token amount to USD value
                    usd_value = amount * token_price

                    trader = MarketTopTrader(
                        wallet=wallet,
                        name=name,
                        net_volume_usd=usd_value,
                        trade_count=0,
                    )

                    if outcome_index == 0:
                        top_buyers.append(trader)
                    else:
                        top_sellers.append(trader)

            # Fetch rankings for top traders in parallel
            ranking_tasks = []
            trader_refs = []
            for t in top_buyers + top_sellers:
                ranking_tasks.append(self.fetch_trader_ranking(t.wallet))
                trader_refs.append(t)

            if ranking_tasks:
                rankings = await asyncio.gather(*ranking_tasks, return_exceptions=True)
                for trader, ranking in zip(trader_refs, rankings):
                    if isinstance(ranking, TraderRanking) and ranking:
                        trader.rank = ranking.rank
                        trader.pnl = ranking.pnl
                        if ranking.user_name:
                            trader.name = ranking.user_name

            logger.debug(
                f"Market {market_id}: {len(top_buyers)} top Yes holders, "
                f"{len(top_sellers)} top No holders"
            )
            return top_buyers, top_sellers

        except Exception as e:
            logger.warning(f"Error fetching top holders for {market_id}: {e}")
            return [], []

    # ================================================================
    # Whale detection
    # ================================================================

    def _is_whale_trade(self, activity: TradeActivity, market: Optional[Market] = None) -> bool:
        """
        Check if a trade qualifies as a whale trade.

        Uses a dynamic size threshold based on market volume:
        - Large markets (24h vol > $1M): standard threshold (MIN_TRADE_SIZE_USD)
        - Small markets (24h vol < $100k): lowered to $1,000
        - In between: linearly interpolated
        """
        # Price filter: only BUY trades remain, price is the taker's buy price.
        # Low price = cheap bet with high upside, high price = expensive/certain.
        # Filter to [MIN_PRICE, MAX_PRICE] range (e.g. 0-0.7).
        if not (self.settings.min_price <= activity.price <= self.settings.max_price):
            return False

        # Dynamic threshold based on market total volume:
        # - Tiny markets ($10k-$100k vol):  $1,000  (niche, info asymmetry high)
        # - Medium markets ($100k-$5M vol): $5,000  (standard)
        # - Large markets ($5M+ vol):       $10,000 (macro, noise high)
        if market and market.volume > 0:
            vol = market.volume  # total volume, not 24hr
            if vol <= 10_000:
                threshold = 500
            elif vol <= 100_000:
                threshold = 1_000
            elif vol <= 5_000_000:
                threshold = 5_000
            else:
                threshold = 10_000
        else:
            threshold = 5_000

        return activity.usdc_size >= threshold

    async def _handle_whale(self, activity: TradeActivity, market_id: str, market: Market):
        """
        Handle a single whale trade:
        1. Fetch trader info (ranking + history) for anomaly scoring
        2. Compute multi-dimensional anomaly score as pre-filter
        3. If score passes threshold, fetch full enrichment data and fire LLM callback
        """
        try:
            # Phase 1: Quick fetch — only ranking + history (needed for anomaly scoring)
            trader_ranking, trader_history = await asyncio.gather(
                self.fetch_trader_ranking(activity.proxy_wallet),
                self.fetch_trader_history(activity.proxy_wallet),
            )

            # Phase 2: Multi-dimensional anomaly scoring (pre-filter before LLM)
            should_analyze, score, breakdown = self._anomaly_detector.should_analyze(
                activity, market=market, trader_history=trader_history,
                market_id=market_id,
            )

            rank_str = f"(Rank #{trader_ranking.rank})" if trader_ranking and trader_ranking.rank else "(Unranked)"
            breakdown_short = " | ".join(f"{k}={v:.2f}" for k, v in breakdown.items())

            if not should_analyze:
                logger.info(
                    f"⚪ Whale below threshold: ${activity.usdc_size:,.2f} "
                    f"BUY {activity.outcome} @ {activity.price:.4f} {rank_str} "
                    f"score={score:.2f} [{breakdown_short}] — skipped LLM"
                )
                return

            logger.info(
                f"🐋 Whale trade detected! ${activity.usdc_size:,.2f} "
                f"BUY {activity.outcome} @ {activity.price:.4f} {rank_str} "
                f"score={score:.2f} [{breakdown_short}] on '{market.question[:50]}...'"
            )

            # Phase 3: Full enrichment (only for trades that pass pre-filter)
            event_positions, (top_buyers, top_sellers) = await asyncio.gather(
                self.fetch_whale_event_positions(
                    activity.proxy_wallet,
                    activity.event_slug,
                    market.condition_id or "",
                ),
                self.fetch_market_top_traders(
                    market_id, condition_id=market.condition_id or "",
                    outcome_prices=market.outcome_prices,
                ),
            )

            whale_trade = WhaleTrade(
                id=f"{market_id}_{activity.transaction_hash}",
                trade=activity,
                market_id=market_id,
                market_question=market.question,
                market_description=market.description,
                market_outcomes=market.outcomes,
                market_outcome_prices=market.outcome_prices,
                trader_ranking=trader_ranking,
                trader_history=trader_history,
                whale_event_positions=event_positions,
                market_top_buyers=top_buyers,
                market_top_sellers=top_sellers,
            )

            # Fire callback (LLM report generation)
            if self._on_whale_detected:
                await self._on_whale_detected(whale_trade)

        except Exception as e:
            logger.error(f"Error handling whale trade in {market_id}: {e}")

    # ================================================================
    # Per-market independent loop
    # ================================================================

    async def _market_loop(self, market_id: str, initial_delay: float):
        """
        Independent polling loop for a single market.

        Each market runs this as its own asyncio.Task:
        1. Wait initial_delay (stagger startup to avoid request storm)
        2. First poll: record existing transactions (no alerts)
        3. Subsequent polls: detect whales, handle in parallel
        """
        if initial_delay > 0:
            await asyncio.sleep(initial_delay)

        market = self._monitored_markets.get(market_id)
        if not market:
            return

        poll_interval = self.settings.fetch_interval_seconds
        # If we already have a last_ts for this market, it means the loop was
        # restarted (e.g. after a market list refresh) — skip the silent
        # first-poll window to avoid missing trades.
        is_first_poll = market_id not in self._market_last_ts

        while self._running:
            try:
                # Check if market was removed during refresh
                market = self._monitored_markets.get(market_id)
                if not market:
                    logger.debug(f"Market {market_id} no longer monitored, stopping loop")
                    break

                activities = await self.fetch_market_trades(market_id)

                # Collect whale handling tasks for this poll cycle
                whale_tasks = []

                for activity in activities:
                    # Record every trade for cluster detection
                    self._anomaly_detector.record_trade(activity, market_id)

                    if activity.transaction_hash in self._processed_txns:
                        continue
                    self._processed_txns.add(activity.transaction_hash)

                    # First poll: only record, don't alert
                    if is_first_poll:
                        continue

                    if self._is_whale_trade(activity, market=market):
                        # Launch whale handling as a parallel task
                        whale_tasks.append(
                            asyncio.create_task(
                                self._handle_whale(activity, market_id, market)
                            )
                        )

                # Wait for all whale handlers in this cycle to complete
                if whale_tasks:
                    await asyncio.gather(*whale_tasks, return_exceptions=True)

                is_first_poll = False

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in market loop {market_id}: {e}")

            await asyncio.sleep(poll_interval)

    # ================================================================
    # Main run loop
    # ================================================================

    async def run(self):
        """
        Start the parallel monitoring loop.

        Architecture (modeled after paper_trading._poll_trades):
        - Each market gets its own asyncio.Task (_market_loop)
        - Startup is staggered to avoid request storms
        - Main loop handles: task lifecycle, persistence, new market spawning
        """
        self._running = True
        poll_interval = self.settings.fetch_interval_seconds

        # Create lock/semaphore inside event loop (avoids "attached to different loop" error)
        self._api_lock = asyncio.Lock()
        self._api_sem = asyncio.Semaphore(5)  # max 5 concurrent API requests

        logger.info(
            f"Starting parallel trade monitor "
            f"({len(self._monitored_markets)} markets, interval: {poll_interval}s)"
        )

        try:
            # Spawn per-market tasks with staggered start
            market_ids = list(self._monitored_markets.keys())
            n_markets = len(market_ids)
            stagger_window = max(poll_interval, n_markets * 1.0)  # ~1s per market

            for i, market_id in enumerate(market_ids):
                delay = (i / max(n_markets, 1)) * stagger_window
                task = asyncio.create_task(self._market_loop(market_id, initial_delay=delay))
                self._market_tasks[market_id] = task

            logger.info(f"Spawned {len(self._market_tasks)} parallel market tasks")

            # Main supervisory loop
            save_interval = 60  # save processed txns every 60 seconds
            last_save = asyncio.get_event_loop().time()

            while self._running:
                now = asyncio.get_event_loop().time()

                # Spawn tasks for newly added markets (from set_monitored_markets)
                for market_id in self._monitored_markets:
                    if market_id not in self._market_tasks or self._market_tasks[market_id].done():
                        task = asyncio.create_task(
                            self._market_loop(market_id, initial_delay=0)
                        )
                        self._market_tasks[market_id] = task
                        logger.info(f"Spawned new task for market {market_id}")

                # Clean up tasks for removed markets
                removed = [mid for mid in self._market_tasks if mid not in self._monitored_markets]
                for mid in removed:
                    self._market_tasks[mid].cancel()
                    del self._market_tasks[mid]

                # Periodic persistence
                if now - last_save >= save_interval:
                    self._save_processed_txns()
                    last_save = now

                await asyncio.sleep(5.0)

        finally:
            # Cancel all market tasks
            for task in self._market_tasks.values():
                task.cancel()
            await asyncio.gather(*self._market_tasks.values(), return_exceptions=True)
            self._market_tasks.clear()
            self._save_processed_txns()

    def stop(self):
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Trade monitor stopping...")

    def clear_processed_transactions(self):
        """Clear the processed transactions cache."""
        count = len(self._processed_txns)
        self._processed_txns.clear()
        logger.info(f"Cleared {count} processed transactions from cache")
