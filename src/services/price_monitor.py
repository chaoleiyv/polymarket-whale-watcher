"""Price monitoring service - monitors ALL active market prices for volatility."""
import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Gamma API for fetching market prices
GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

# Storage directory for price volatility alerts
VOLATILITY_DIR = Path(__file__).parent.parent.parent / "price_volatility"


@dataclass
class PricePoint:
    """A single price observation."""
    timestamp: int
    yes_price: float


@dataclass
class VolatilityAlert:
    """A price volatility alert."""
    market_id: str
    market_question: str
    start_timestamp: int
    end_timestamp: int
    start_price: float
    end_price: float
    price_change: float
    price_change_percent: float
    direction: str  # "UP" or "DOWN"
    window_seconds: int
    detected_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "market_question": self.market_question,
            "start_timestamp": self.start_timestamp,
            "end_timestamp": self.end_timestamp,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "price_change": self.price_change,
            "price_change_percent": self.price_change_percent,
            "direction": self.direction,
            "window_seconds": self.window_seconds,
            "detected_at": self.detected_at,
        }


class PriceMonitor:
    """
    Monitors ALL active market prices for short-term volatility.

    Tracks Yes prices for all active markets and alerts when
    price changes exceed threshold within the time window.
    """

    # Default configuration
    DEFAULT_WINDOW_SECONDS = 300  # 5 minutes
    DEFAULT_THRESHOLD = 0.10  # 10%
    DEFAULT_MAX_HISTORY_SECONDS = 3600  # Keep 1 hour of history
    DEFAULT_POLL_INTERVAL = 30  # Poll all markets every 30 seconds

    def __init__(
        self,
        window_seconds: int = DEFAULT_WINDOW_SECONDS,
        threshold: float = DEFAULT_THRESHOLD,
        max_history_seconds: int = DEFAULT_MAX_HISTORY_SECONDS,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
        on_volatility_detected: Optional[Callable[["VolatilityAlert"], Awaitable[None]]] = None,
    ):
        """
        Initialize the price monitor.

        Args:
            window_seconds: Time window for volatility detection (default 5 minutes)
            threshold: Price change threshold to trigger alert (default 10%)
            max_history_seconds: How long to keep price history (default 1 hour)
            poll_interval: Interval for polling all markets (default 30 seconds)
            on_volatility_detected: Async callback when volatility is detected
        """
        self.window_seconds = window_seconds
        self.threshold = threshold
        self.max_history_seconds = max_history_seconds
        self.poll_interval = poll_interval

        # Callback for volatility detection
        self._on_volatility_detected = on_volatility_detected

        # Price history per market: market_id -> deque of PricePoints
        self._price_history: Dict[str, deque] = {}

        # Market info cache: market_id -> question
        self._market_info: Dict[str, str] = {}

        # Track recent alerts to avoid duplicates (market_id -> last_alert_timestamp)
        self._recent_alerts: Dict[str, int] = {}

        # Minimum interval between alerts for same market (seconds)
        self._alert_cooldown = 3600  # 1 hour (match window_seconds)

        # HTTP client for API calls
        self._client: Optional[httpx.AsyncClient] = None

        # Control flag
        self._running = False

        # Ensure storage directory exists
        VOLATILITY_DIR.mkdir(parents=True, exist_ok=True)

    def record_price(self, market_id: str, market_question: str, yes_price: float) -> Optional[VolatilityAlert]:
        """
        Record a price observation and check for volatility.

        Args:
            market_id: The market ID
            market_question: The market question text
            yes_price: Current Yes price (0-1)

        Returns:
            VolatilityAlert if threshold exceeded, None otherwise
        """
        now = int(datetime.utcnow().timestamp())

        # Initialize history for new markets
        if market_id not in self._price_history:
            self._price_history[market_id] = deque()

        history = self._price_history[market_id]

        # Add new price point
        history.append(PricePoint(timestamp=now, yes_price=yes_price))

        # Clean up old entries
        cutoff = now - self.max_history_seconds
        while history and history[0].timestamp < cutoff:
            history.popleft()

        # Check for volatility
        alert = self._check_volatility(market_id, market_question, now)

        if alert:
            # Check cooldown
            last_alert = self._recent_alerts.get(market_id, 0)
            if now - last_alert < self._alert_cooldown:
                logger.debug(f"Alert suppressed for {market_id} (cooldown)")
                return None

            # Record alert
            self._recent_alerts[market_id] = now
            self._store_alert(alert)

            # Log warning
            logger.warning(
                f"🚨 PRICE VOLATILITY: {market_question[:50]}... "
                f"{alert.direction} {abs(alert.price_change_percent):.1%} "
                f"({alert.start_price:.2%} → {alert.end_price:.2%}) "
                f"in {alert.window_seconds // 60}min"
            )

            return alert

        return None

    def _check_volatility(
        self, market_id: str, market_question: str, current_time: int
    ) -> Optional[VolatilityAlert]:
        """
        Check if price volatility exceeds threshold within the time window.

        Args:
            market_id: The market ID
            market_question: The market question text
            current_time: Current timestamp

        Returns:
            VolatilityAlert if threshold exceeded, None otherwise
        """
        history = self._price_history.get(market_id)
        if not history or len(history) < 2:
            return None

        current_price = history[-1].yes_price
        window_start = current_time - self.window_seconds

        # Find the oldest price within the window
        oldest_in_window = None
        for point in history:
            if point.timestamp >= window_start:
                oldest_in_window = point
                break

        if oldest_in_window is None:
            return None

        # Calculate price change
        price_change = current_price - oldest_in_window.yes_price
        price_change_abs = abs(price_change)

        if price_change_abs < self.threshold:
            return None

        # Create alert
        return VolatilityAlert(
            market_id=market_id,
            market_question=market_question,
            start_timestamp=oldest_in_window.timestamp,
            end_timestamp=current_time,
            start_price=oldest_in_window.yes_price,
            end_price=current_price,
            price_change=price_change,
            price_change_percent=price_change,
            direction="UP" if price_change > 0 else "DOWN",
            window_seconds=current_time - oldest_in_window.timestamp,
        )

    def _store_alert(self, alert: VolatilityAlert) -> None:
        """
        Store a volatility alert to file.

        Args:
            alert: The alert to store
        """
        alerts_file = VOLATILITY_DIR / "volatility_alerts.json"

        # Load existing alerts
        existing_alerts = []
        if alerts_file.exists():
            try:
                with open(alerts_file, 'r', encoding='utf-8') as f:
                    existing_alerts = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load existing alerts: {e}")

        # Add new alert
        existing_alerts.append(alert.to_dict())

        # Save back
        try:
            with open(alerts_file, 'w', encoding='utf-8') as f:
                json.dump(existing_alerts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to store volatility alert: {e}")

    def get_price_history(self, market_id: str) -> List[dict]:
        """
        Get price history for a market.

        Args:
            market_id: The market ID

        Returns:
            List of price points as dicts
        """
        history = self._price_history.get(market_id, deque())
        return [{"timestamp": p.timestamp, "yes_price": p.yes_price} for p in history]

    def get_all_alerts(self) -> List[dict]:
        """
        Get all stored volatility alerts.

        Returns:
            List of alerts as dicts
        """
        alerts_file = VOLATILITY_DIR / "volatility_alerts.json"

        if not alerts_file.exists():
            return []

        try:
            with open(alerts_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load alerts: {e}")
            return []

    def clear_history(self, market_id: Optional[str] = None) -> None:
        """
        Clear price history.

        Args:
            market_id: If provided, clear only this market's history. Otherwise clear all.
        """
        if market_id:
            if market_id in self._price_history:
                del self._price_history[market_id]
                logger.info(f"Cleared price history for {market_id}")
        else:
            self._price_history.clear()
            logger.info("Cleared all price history")

    async def _fetch_all_active_markets(self) -> List[dict]:
        """
        Fetch all active markets from Gamma API.

        Returns:
            List of market data dicts with id, question, and outcomePrices
        """
        all_markets = []
        offset = 0
        batch_size = 100

        try:
            while True:
                params = {
                    "active": True,
                    "closed": False,
                    "archived": False,
                    "limit": batch_size,
                    "offset": offset,
                    "enableOrderBook": True,
                }

                response = await self._client.get(GAMMA_API_URL, params=params)
                response.raise_for_status()

                data = response.json()
                if not data:
                    break

                for market in data:
                    market_id = str(market.get("id", ""))
                    question = market.get("question", "")
                    outcome_prices = market.get("outcomePrices", [])

                    if isinstance(outcome_prices, str):
                        outcome_prices = json.loads(outcome_prices)

                    if market_id and outcome_prices:
                        all_markets.append({
                            "id": market_id,
                            "question": question,
                            "yes_price": float(outcome_prices[0]) if outcome_prices else None,
                        })
                        # Cache market info
                        self._market_info[market_id] = question

                if len(data) < batch_size:
                    break

                offset += batch_size

        except Exception as e:
            logger.error(f"Error fetching active markets: {e}")

        return all_markets

    async def _poll_all_prices(self) -> List[VolatilityAlert]:
        """
        Poll prices for all active markets and check for volatility.

        Returns:
            List of volatility alerts triggered
        """
        alerts = []

        markets = await self._fetch_all_active_markets()
        logger.debug(f"Polling prices for {len(markets)} active markets")

        for market in markets:
            market_id = market["id"]
            question = market["question"]
            yes_price = market.get("yes_price")

            if yes_price is not None:
                alert = self.record_price(market_id, question, yes_price)
                if alert:
                    alerts.append(alert)

        return alerts

    async def run(self) -> None:
        """
        Start the price monitoring loop.

        Continuously polls all active markets at the configured interval.
        """
        self._running = True
        self._client = httpx.AsyncClient(timeout=60.0)

        logger.info(
            f"Starting price monitor (interval: {self.poll_interval}s, "
            f"window: {self.window_seconds}s, threshold: {self.threshold:.0%})"
        )

        try:
            while self._running:
                try:
                    alerts = await self._poll_all_prices()
                    if alerts:
                        logger.info(f"Detected {len(alerts)} volatility alerts")

                        # Call callback for each alert
                        if self._on_volatility_detected:
                            for alert in alerts:
                                try:
                                    await self._on_volatility_detected(alert)
                                except Exception as e:
                                    logger.error(f"Error in volatility callback: {e}")

                except Exception as e:
                    logger.error(f"Error in price monitoring loop: {e}")

                await asyncio.sleep(self.poll_interval)
        finally:
            await self._client.aclose()
            self._client = None

    def stop(self) -> None:
        """Stop the price monitoring loop."""
        self._running = False
        logger.info("Price monitor stopping...")

    def get_monitored_market_count(self) -> int:
        """Get the number of markets currently being monitored."""
        return len(self._price_history)
