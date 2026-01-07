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

    # Sports-related keywords to filter out (case-insensitive)
    SPORTS_KEYWORDS = [
        # General sports terms
        "nba", "nfl", "mlb", "nhl", "mls", "ufc", "wwe", "pga", "atp", "wta",
        "fifa", "uefa", "epl", "premier league", "la liga", "serie a", "bundesliga",
        "champions league", "world cup", "olympics", "olympic",
        # Sports names
        "basketball", "football", "soccer", "baseball", "hockey", "tennis",
        "golf", "boxing", "mma", "wrestling", "cricket", "rugby", "f1", "formula 1",
        "nascar", "racing", "motorsport",
        # Team/game terms
        "game", "match", "vs", "versus", "playoff", "playoffs", "finals",
        "championship", "tournament", "season", "super bowl", "world series",
        # Player/team actions
        "score", "points", "goals", "touchdowns", "wins", "win against",
        "beat", "defeat",
        # Specific sports betting terms
        "mvp", "rookie", "all-star", "draft", "trade",
        # Common sports team cities/names patterns
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

    def _is_sports_market(self, market_data: dict) -> bool:
        """
        Check if a market is sports-related.

        Args:
            market_data: Raw market data from API

        Returns:
            True if the market is sports-related
        """
        # Check question and description
        question = (market_data.get("question") or "").lower()
        description = (market_data.get("description") or "").lower()
        slug = (market_data.get("slug") or "").lower()

        text_to_check = f"{question} {description} {slug}"

        for keyword in self.SPORTS_KEYWORDS:
            if keyword in text_to_check:
                return True

        return False

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

                sports_count = 0
                for market_data in data:
                    # Skip sports markets
                    if self._is_sports_market(market_data):
                        sports_count += 1
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

                logger.debug(
                    f"Batch {iteration}: fetched {len(data)}, "
                    f"filtered {sports_count} sports markets, "
                    f"total non-sports: {len(trending_markets)}"
                )

                if len(data) < batch_size:
                    break  # No more markets available

                offset += batch_size

            logger.info(
                f"Fetched {len(trending_markets)} trending markets (sports markets filtered out)"
            )
            return trending_markets

        except httpx.HTTPError as e:
            logger.error(f"HTTP error fetching trending markets: {e}")
            return trending_markets  # Return what we have so far
        except Exception as e:
            logger.error(f"Error fetching trending markets: {e}")
            return trending_markets

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
