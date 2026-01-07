"""Trade monitoring service - monitors markets for whale trades."""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Callable, Awaitable

import httpx

from src.config import get_settings
from src.models.market import Market, TrendingMarket
from src.models.trade import TradeActivity, WhaleTrade, TraderRanking, TraderHistory

logger = logging.getLogger(__name__)

# File to persist processed transaction hashes
PROCESSED_TXNS_FILE = Path(__file__).parent.parent.parent / "data" / "processed_transactions.json"


class TradeMonitor:
    """
    Monitors Polymarket markets for large trades.

    Similar to copy-trading-bot's tradeMonitor, but monitors markets instead of users.
    """

    def __init__(
        self,
        on_whale_detected: Optional[Callable[[WhaleTrade], Awaitable[None]]] = None,
    ):
        """
        Initialize trade monitor.

        Args:
            on_whale_detected: Async callback when whale trade is detected
        """
        self.settings = get_settings()
        self.data_api_url = "https://data-api.polymarket.com"
        self.trades_endpoint = f"{self.data_api_url}/trades"
        self.leaderboard_endpoint = f"{self.data_api_url}/v1/leaderboard"
        self._client = httpx.AsyncClient(timeout=30.0)

        # Cache for trader rankings to avoid repeated API calls
        self._trader_ranking_cache: Dict[str, TraderRanking] = {}

        # Markets being monitored: condition_id -> Market
        self._monitored_markets: Dict[str, Market] = {}

        # Track processed transactions to avoid duplicates
        self._processed_txns: Set[str] = set()

        # Load previously processed transactions from file
        self._load_processed_txns()

        # Callback for whale detection
        self._on_whale_detected = on_whale_detected

        # Control flag
        self._running = False

        # Flag to track if initial scan is complete (ignore historical trades)
        self._initial_scan_complete = False

    def _load_processed_txns(self):
        """Load processed transaction hashes from JSON file."""
        try:
            if PROCESSED_TXNS_FILE.exists():
                with open(PROCESSED_TXNS_FILE, "r") as f:
                    data = json.load(f)
                    self._processed_txns = set(data.get("transactions", []))
                    logger.info(f"Loaded {len(self._processed_txns)} processed transactions from file")
        except Exception as e:
            logger.warning(f"Failed to load processed transactions: {e}")
            self._processed_txns = set()

    def _save_processed_txns(self):
        """Save processed transaction hashes to JSON file."""
        try:
            # Ensure directory exists
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
        # Save processed transactions before closing
        self._save_processed_txns()
        await self._client.aclose()

    def set_monitored_markets(self, markets: List[TrendingMarket]):
        """
        Update the list of markets to monitor.

        Args:
            markets: List of trending markets to monitor
        """
        self._monitored_markets = {}
        for tm in markets:
            if tm.market.condition_id:
                self._monitored_markets[tm.market.condition_id] = tm.market

        logger.info(f"Now monitoring {len(self._monitored_markets)} markets")

    async def fetch_market_trades(self, condition_id: str) -> List[TradeActivity]:
        """
        Fetch recent trades for a market using the /trades endpoint.

        This endpoint allows querying by market without requiring a user address.

        Args:
            condition_id: The market condition ID

        Returns:
            List of trade activities
        """
        try:
            # Use /trades endpoint which supports market-based queries
            # Docs: https://docs.polymarket.com/api-reference/core/get-trades-for-a-user-or-markets
            params = {
                "market": condition_id,
                "limit": 500,
            }

            response = await self._client.get(self.trades_endpoint, params=params)
            response.raise_for_status()

            data = response.json()
            activities = []

            for item in data:
                try:
                    # Calculate USDC size from price and size
                    size = float(item.get("size", 0) or 0)
                    price = float(item.get("price", 0) or 0)
                    usdc_size = float(item.get("usdcSize", 0) or 0)

                    # If usdcSize not provided, calculate it
                    if usdc_size == 0 and size > 0 and price > 0:
                        usdc_size = size * price

                    activity = TradeActivity(
                        transaction_hash=item.get("transactionHash", item.get("id", "")),
                        timestamp=item.get("timestamp", 0),
                        condition_id=item.get("conditionId", condition_id),
                        asset=item.get("asset", item.get("tokenId", "")),
                        side=item.get("side", ""),
                        size=size,
                        usdc_size=usdc_size,
                        price=price,
                        outcome=item.get("outcome", ""),
                        outcome_index=int(item.get("outcomeIndex", 0) or 0),
                        title=item.get("title", item.get("marketTitle", "")),
                        slug=item.get("slug", item.get("marketSlug")),
                        event_slug=item.get("eventSlug"),
                        proxy_wallet=item.get("proxyWallet", item.get("maker", item.get("taker"))),
                        name=item.get("name"),
                    )
                    activities.append(activity)
                except Exception as e:
                    logger.debug(f"Failed to parse trade: {e}")
                    continue

            return activities

        except httpx.HTTPError as e:
            logger.warning(f"HTTP error fetching trades for {condition_id}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Error fetching trades for {condition_id}: {e}")
            return []

    async def fetch_trader_ranking(self, wallet_address: str) -> Optional[TraderRanking]:
        """
        Fetch trader ranking from the leaderboard API.

        Args:
            wallet_address: The trader's wallet address

        Returns:
            TraderRanking or None if not found/error
        """
        if not wallet_address:
            return None

        # Check cache first
        if wallet_address in self._trader_ranking_cache:
            return self._trader_ranking_cache[wallet_address]

        try:
            # Query leaderboard for this specific user (ALL time period for overall ranking)
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
                # Cache the result
                self._trader_ranking_cache[wallet_address] = ranking
                logger.debug(f"Fetched ranking for {wallet_address}: #{ranking.rank}")
                return ranking

            # User not on leaderboard
            return None

        except httpx.HTTPError as e:
            logger.debug(f"HTTP error fetching ranking for {wallet_address}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching ranking for {wallet_address}: {e}")
            return None

    async def fetch_trader_history(self, wallet_address: str) -> Optional[TraderHistory]:
        """
        Fetch trader's recent trading history.

        Args:
            wallet_address: The trader's wallet address

        Returns:
            TraderHistory or None if not found/error
        """
        if not wallet_address:
            return None

        try:
            # Fetch recent trades for this user
            params = {
                "user": wallet_address,
                "limit": 100,  # Get last 100 trades
            }

            response = await self._client.get(self.trades_endpoint, params=params)
            response.raise_for_status()

            data = response.json()

            if not data:
                return None

            # Calculate statistics
            total_trades = len(data)
            total_volume = 0.0
            large_trades_count = 0
            recent_markets = set()
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

            # Sort recent trades by size (largest first)
            recent_trades.sort(key=lambda x: x["usdc_size"], reverse=True)

            history = TraderHistory(
                total_trades=total_trades,
                total_volume=total_volume,
                avg_trade_size=avg_trade_size,
                large_trades_count=large_trades_count,
                recent_markets=list(recent_markets)[:10],
                recent_trades=recent_trades[:10],
            )

            logger.debug(f"Fetched history for {wallet_address}: {total_trades} trades, ${total_volume:,.2f} volume")
            return history

        except httpx.HTTPError as e:
            logger.debug(f"HTTP error fetching history for {wallet_address}: {e}")
            return None
        except Exception as e:
            logger.debug(f"Error fetching history for {wallet_address}: {e}")
            return None

    def _is_whale_trade(self, activity: TradeActivity) -> bool:
        """
        Check if a trade qualifies as a whale trade.

        Args:
            activity: The trade activity to check

        Returns:
            True if this is a whale trade
        """
        return (
            activity.usdc_size >= self.settings.min_trade_size_usd
            and self.settings.min_price <= activity.price <= self.settings.max_price
        )

    async def _check_market(self, condition_id: str, market: Market) -> List[WhaleTrade]:
        """
        Check a single market for whale trades.

        Args:
            condition_id: Market condition ID
            market: Market object

        Returns:
            List of detected whale trades
        """
        whale_trades = []

        activities = await self.fetch_market_trades(condition_id)

        for activity in activities:
            # Skip if already processed
            if activity.transaction_hash in self._processed_txns:
                continue

            # Mark as processed
            self._processed_txns.add(activity.transaction_hash)

            # Skip during initial scan (only record historical transactions)
            if not self._initial_scan_complete:
                continue

            # Check if it's a whale trade
            if self._is_whale_trade(activity):
                # Fetch trader ranking and history concurrently
                trader_ranking, trader_history = await asyncio.gather(
                    self.fetch_trader_ranking(activity.proxy_wallet),
                    self.fetch_trader_history(activity.proxy_wallet),
                )

                whale_trade = WhaleTrade(
                    id=f"{condition_id}_{activity.transaction_hash}",
                    trade=activity,
                    market_id=market.id,
                    market_question=market.question,
                    market_description=market.description,
                    market_outcomes=market.outcomes,
                    market_outcome_prices=market.outcome_prices,
                    trader_ranking=trader_ranking,
                    trader_history=trader_history,
                )

                whale_trades.append(whale_trade)

                # Log with ranking info
                rank_str = f"(排名 #{trader_ranking.rank})" if trader_ranking and trader_ranking.rank else "(未上榜)"
                logger.info(
                    f"🐋 Whale trade detected! ${activity.usdc_size:,.2f} "
                    f"{activity.side} @ {activity.price:.4f} {rank_str} on '{market.question[:50]}...'"
                )

        return whale_trades

    async def check_all_markets(self) -> List[WhaleTrade]:
        """
        Check all monitored markets for whale trades.

        Returns:
            List of all detected whale trades
        """
        all_whale_trades = []

        # Check markets concurrently in batches
        batch_size = 10
        items = list(self._monitored_markets.items())

        for i in range(0, len(items), batch_size):
            batch = items[i : i + batch_size]
            tasks = [
                self._check_market(condition_id, market)
                for condition_id, market in batch
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Error checking market: {result}")
                elif result:
                    all_whale_trades.extend(result)

        return all_whale_trades

    async def run(self):
        """
        Start the monitoring loop.

        Continuously monitors markets at the configured interval.
        First scan records existing transactions without triggering alerts.
        """
        self._running = True
        logger.info(
            f"Starting trade monitor (interval: {self.settings.fetch_interval_seconds}s)"
        )

        # Initial scan - record existing transactions without alerting
        logger.info("Performing initial scan to record existing transactions...")
        await self.check_all_markets()
        self._initial_scan_complete = True
        self._save_processed_txns()  # Save after initial scan
        logger.info(f"Initial scan complete. Recorded {len(self._processed_txns)} existing transactions. Now monitoring for NEW trades only.")

        save_counter = 0
        while self._running:
            try:
                whale_trades = await self.check_all_markets()

                # Call callback for each whale trade
                if self._on_whale_detected:
                    for whale_trade in whale_trades:
                        try:
                            await self._on_whale_detected(whale_trade)
                        except Exception as e:
                            logger.error(f"Error in whale callback: {e}")

                # Save processed transactions periodically (every 12 cycles = ~1 minute)
                save_counter += 1
                if save_counter >= 12:
                    self._save_processed_txns()
                    save_counter = 0

            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")

            # Wait for next interval
            await asyncio.sleep(self.settings.fetch_interval_seconds)

    def stop(self):
        """Stop the monitoring loop."""
        self._running = False
        logger.info("Trade monitor stopping...")

    def clear_processed_transactions(self):
        """Clear the processed transactions cache."""
        count = len(self._processed_txns)
        self._processed_txns.clear()
        logger.info(f"Cleared {count} processed transactions from cache")
