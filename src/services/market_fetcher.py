"""Market fetching service - fetches trending markets from Polymarket."""
import json
import logging
from typing import List, Optional

import httpx

from src.config import get_settings
from src.models.market import Market, TrendingMarket

logger = logging.getLogger(__name__)


class MarketFetcher:
    """Fetches and manages trending markets from Polymarket Gamma API."""

    # Short-term price prediction markets to filter out (no insider trading value)
    # Matches patterns like "Bitcoin Up or Down - March 27, 2:00AM-2:15AM ET"
    SHORT_TERM_PRICE_KEYWORDS = [
        "up or down",       # "Bitcoin Up or Down - March 27, 2:00AM"
        "higher or lower",  # price higher or lower
        "above or below",   # close above or below
        "opens up or down", # "S&P 500 Opens Up or Down"
        "green or red",     # daily candle color
    ]

    # Temperature/weather markets (no insider trading value)
    WEATHER_KEYWORDS = [
        "highest temperature",
        "lowest temperature",
        "temperature in",
        "weather",
        "rainfall",
        "°f on",
        "°c on",
    ]

    # Sports-related keywords to filter out
    SPORTS_KEYWORDS = [
        "nba", "nfl", "mlb", "nhl", "mls", "ufc", "wwe", "pga", "atp", "wta",
        "fifa", "uefa", "epl", "premier league", "la liga", "serie a", "bundesliga",
        "champions league", "world cup", "olympics", "olympic",
        "basketball", "football", "soccer", "baseball", "hockey", "tennis",
        "golf", "boxing", "mma", "wrestling", "cricket", "rugby", "f1", "formula 1",
        "nascar", "racing", "motorsport",
        "game", "match", "vs", "versus", "playoff", "playoffs", "finals",
        "championship", "tournament", "season", "super bowl", "world series",
        "score", "points", "goals", "touchdowns", "wins", "win against",
        "beat", "defeat",
        "mvp", "rookie", "all-star", "draft", "trade",
        "lakers", "celtics", "warriors", "bulls", "heat", "knicks",
        "yankees", "dodgers", "red sox", "cubs", "mets",
        "cowboys", "patriots", "chiefs", "eagles", "49ers",
        "manchester", "barcelona", "real madrid", "liverpool", "chelsea",
    ]

    def __init__(self):
        self.settings = get_settings()
        self.gamma_url = "https://gamma-api.polymarket.com"
        self.markets_endpoint = f"{self.gamma_url}/markets"
        self.events_endpoint = f"{self.gamma_url}/events"
        self._client = httpx.Client(timeout=30.0)

    def __del__(self):
        """Cleanup HTTP client."""
        if hasattr(self, "_client"):
            self._client.close()

    def _should_filter_market(self, market_data: dict) -> str:
        """
        Check if a market should be filtered out.

        Returns:
            Filter reason string if should be filtered, empty string if OK.
        """
        question = (market_data.get("question") or "").lower()
        description = (market_data.get("description") or "").lower()
        slug = (market_data.get("slug") or "").lower()
        text = f"{question} {description} {slug}"

        for keyword in self.SPORTS_KEYWORDS:
            if keyword in text:
                return "sports"

        for keyword in self.SHORT_TERM_PRICE_KEYWORDS:
            if keyword in text:
                return "short_term_price"

        for keyword in self.WEATHER_KEYWORDS:
            if keyword in text:
                return "weather"

        return ""

    def _is_sports_market(self, market_data: dict) -> bool:
        """Legacy compatibility."""
        return bool(self._should_filter_market(market_data))

    def _parse_market(self, data: dict) -> Optional[Market]:
        """Parse raw market data into Market model."""
        try:
            # Parse outcome prices (comes as stringified list)
            outcome_prices = data.get("outcomePrices", [])
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)
            outcome_prices = [float(p) for p in outcome_prices]

            # Parse clob token IDs
            clob_token_ids = data.get("clobTokenIds", [])
            if isinstance(clob_token_ids, str):
                clob_token_ids = json.loads(clob_token_ids)

            # Parse outcomes
            outcomes = data.get("outcomes", [])
            if isinstance(outcomes, str):
                outcomes = json.loads(outcomes)

            return Market(
                id=str(data.get("id", "")),
                question=data.get("question", ""),
                condition_id=data.get("conditionId"),
                slug=data.get("slug"),
                description=data.get("description"),
                end_date=data.get("endDate"),
                outcomes=outcomes,
                outcome_prices=outcome_prices,
                clob_token_ids=clob_token_ids,
                volume=float(data.get("volume", 0) or 0),
                volume_24hr=float(data.get("volume24hr", 0) or 0),
                liquidity=float(data.get("liquidity", 0) or 0),
                active=data.get("active", False),
                closed=data.get("closed", False),
                neg_risk=data.get("negRisk", False),
            )
        except Exception as e:
            logger.warning(f"Failed to parse market {data.get('id')}: {e}")
            return None

    def get_trending_markets(self, limit: Optional[int] = None) -> List[TrendingMarket]:
        """
        Fetch trending markets sorted by 24-hour volume.

        Filters out sports-related markets since they lack fundamental analysis value.

        Args:
            limit: Maximum number of non-sports markets to return (defaults to settings)

        Returns:
            List of TrendingMarket objects (excluding sports markets)
        """
        limit = limit or self.settings.trending_markets_limit
        trending_markets = []
        offset = 0
        batch_size = 100  # Fetch more to account for sports filtering
        max_iterations = 10  # Safety limit to prevent infinite loops

        try:
            iteration = 0
            while len(trending_markets) < limit and iteration < max_iterations:
                iteration += 1

                # Fetch active markets sorted by volume
                params = {
                    "active": True,
                    "closed": False,
                    "archived": False,
                    "limit": batch_size,
                    "offset": offset,
                    "order": "volume24hr",
                    "ascending": False,
                    "enableOrderBook": True,  # Only markets with CLOB enabled
                }

                response = self._client.get(self.markets_endpoint, params=params)
                response.raise_for_status()

                data = response.json()
                if not data:
                    break  # No more markets

                filtered_counts = {"sports": 0, "short_term_price": 0, "weather": 0}
                for market_data in data:
                    reason = self._should_filter_market(market_data)
                    if reason:
                        filtered_counts[reason] = filtered_counts.get(reason, 0) + 1
                        continue

                    market = self._parse_market(market_data)
                    if market:
                        trending_market = TrendingMarket(
                            market=market,
                            volume_24hr=market.volume_24hr,
                            liquidity=market.liquidity,
                            rank=len(trending_markets) + 1,
                        )
                        if trending_market.is_valid_for_monitoring:
                            trending_markets.append(trending_market)

                            if len(trending_markets) >= limit:
                                break

                total_filtered = sum(filtered_counts.values())
                if total_filtered:
                    parts = [f"{k}={v}" for k, v in filtered_counts.items() if v > 0]
                    logger.debug(
                        f"Batch {iteration}: fetched {len(data)}, "
                        f"filtered {total_filtered} ({', '.join(parts)}), "
                        f"kept: {len(trending_markets)}"
                    )
                else:
                    logger.debug(
                        f"Batch {iteration}: fetched {len(data)}, "
                        f"kept: {len(trending_markets)}"
                    )

                if len(data) < batch_size:
                    break  # No more markets available

                offset += batch_size

            logger.info(
                f"Fetched {len(trending_markets)} trending markets "
                f"(filtered: sports, short-term price, weather)"
            )
            return trending_markets

        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching trending markets: {e}")
            return trending_markets  # Return what we have so far
        except Exception as e:
            logger.error(f"Error fetching trending markets: {e}")
            return trending_markets

    # Keywords that identify token launch / crypto project markets
    TOKEN_LAUNCH_KEYWORDS = [
        "fdv", "market cap (fdv)", "launch a token", "tge",
        "listing", "airdrop", "public sale",
    ]

    def _is_token_launch_market(self, market_data: dict) -> bool:
        """Check if a market is related to token launches / crypto projects."""
        question = (market_data.get("question") or "").lower()
        return any(kw in question for kw in self.TOKEN_LAUNCH_KEYWORDS)

    def get_token_launch_markets(self, max_scan: int = 2000) -> List[TrendingMarket]:
        """
        Scan active markets for token launch / crypto project markets
        that may not be in the top trending list.

        Returns:
            List of TrendingMarket objects for token launch markets.
        """
        token_markets = []
        offset = 0
        batch_size = 100
        seen_ids = set()

        try:
            while offset < max_scan:
                params = {
                    "active": True, "closed": False, "archived": False,
                    "limit": batch_size, "offset": offset,
                    "order": "volume24hr", "ascending": False,
                    "enableOrderBook": True,
                }
                response = self._client.get(self.markets_endpoint, params=params)
                response.raise_for_status()
                data = response.json()
                if not data:
                    break

                for market_data in data:
                    if not self._is_token_launch_market(market_data):
                        continue

                    market = self._parse_market(market_data)
                    if market and market.id not in seen_ids:
                        seen_ids.add(market.id)
                        tm = TrendingMarket(
                            market=market,
                            volume_24hr=market.volume_24hr,
                            liquidity=market.liquidity,
                        )
                        if tm.is_valid_for_monitoring:
                            token_markets.append(tm)

                if len(data) < batch_size:
                    break
                offset += batch_size

            logger.info(f"Found {len(token_markets)} token launch markets")
            return token_markets

        except Exception as e:
            logger.error(f"Error fetching token launch markets: {e}")
            return token_markets

    def get_niche_markets(
        self,
        limit: int = 50,
        min_volume_24hr: float = 5_000,
        max_volume_24hr: float = 500_000,
        offset_start: int = 200,
        max_scan: int = 1500,
    ) -> List[TrendingMarket]:
        """
        Fetch niche markets (lower volume) that may have higher information
        asymmetry value. Scans markets ranked beyond the top trending list.

        Args:
            limit: Max number of niche markets to return
            min_volume_24hr: Minimum 24h volume (filter out dead markets)
            max_volume_24hr: Maximum 24h volume (filter out large/macro markets)
            offset_start: Start scanning from this rank
            max_scan: Stop scanning after this offset

        Returns:
            List of TrendingMarket objects for niche markets.
        """
        niche_markets = []
        offset = offset_start
        batch_size = 100

        try:
            while offset < max_scan and len(niche_markets) < limit:
                params = {
                    "active": True, "closed": False, "archived": False,
                    "limit": batch_size, "offset": offset,
                    "order": "volume24hr", "ascending": False,
                    "enableOrderBook": True,
                }
                response = self._client.get(self.markets_endpoint, params=params)
                response.raise_for_status()
                data = response.json()
                if not data:
                    break

                for market_data in data:
                    vol = float(market_data.get("volume24hr", 0) or 0)

                    # Volume filter: not too small (dead), not too large (macro)
                    if vol < min_volume_24hr or vol > max_volume_24hr:
                        continue

                    # Apply standard filters (sports, weather, short-term price)
                    if self._should_filter_market(market_data):
                        continue

                    market = self._parse_market(market_data)
                    if market:
                        tm = TrendingMarket(
                            market=market,
                            volume_24hr=market.volume_24hr,
                            liquidity=market.liquidity,
                        )
                        if tm.is_valid_for_monitoring:
                            niche_markets.append(tm)
                            if len(niche_markets) >= limit:
                                break

                if len(data) < batch_size:
                    break
                offset += batch_size

            logger.info(
                f"Found {len(niche_markets)} niche markets "
                f"(volume ${min_volume_24hr:,.0f}-${max_volume_24hr:,.0f})"
            )
            return niche_markets

        except Exception as e:
            logger.error(f"Error fetching niche markets: {e}")
            return niche_markets

    def get_market_by_id(self, market_id: str) -> Optional[Market]:
        """
        Fetch a single market by ID.

        Args:
            market_id: The market ID

        Returns:
            Market object or None
        """
        try:
            url = f"{self.markets_endpoint}/{market_id}"
            response = self._client.get(url)
            response.raise_for_status()

            data = response.json()
            return self._parse_market(data)

        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching market {market_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching market {market_id}: {e}")
            return None

    def get_market_by_condition_id(self, condition_id: str) -> Optional[Market]:
        """
        Fetch a market by condition ID.

        Args:
            condition_id: The condition ID

        Returns:
            Market object or None
        """
        try:
            params = {"conditionId": condition_id}
            response = self._client.get(self.markets_endpoint, params=params)
            response.raise_for_status()

            data = response.json()
            if data and len(data) > 0:
                return self._parse_market(data[0])
            return None

        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching market by condition {condition_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching market by condition {condition_id}: {e}")
            return None

    def get_all_current_markets(self, batch_size: int = 100) -> List[Market]:
        """
        Fetch all current active markets (paginated).

        Args:
            batch_size: Number of markets per request

        Returns:
            List of all active Market objects
        """
        all_markets = []
        offset = 0

        while True:
            try:
                params = {
                    "active": True,
                    "closed": False,
                    "archived": False,
                    "limit": batch_size,
                    "offset": offset,
                    "enableOrderBook": True,
                }

                response = self._client.get(self.markets_endpoint, params=params)
                response.raise_for_status()

                data = response.json()
                if not data:
                    break

                for market_data in data:
                    market = self._parse_market(market_data)
                    if market:
                        all_markets.append(market)

                if len(data) < batch_size:
                    break

                offset += batch_size

            except Exception as e:
                logger.error(f"Error fetching markets at offset {offset}: {e}")
                break

        logger.info(f"Fetched {len(all_markets)} total active markets")
        return all_markets
