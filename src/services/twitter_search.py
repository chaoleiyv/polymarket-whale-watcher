"""Twitter search service for whale trade verification."""
import os
import logging
from typing import Optional, Literal

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


# ---------- Retry-enabled HTTP GET ----------
_shared_session: Optional[requests.Session] = None


def robust_get(url: str, **kwargs) -> requests.Response:
    """GET request with automatic retry (3 retries, exponential backoff)."""
    global _shared_session
    if _shared_session is None:
        _shared_session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        _shared_session.mount("http://", adapter)
        _shared_session.mount("https://", adapter)
    kwargs.setdefault("timeout", 15)
    return _shared_session.get(url, **kwargs)

# Twitter module constants and helpers (extracted to avoid langchain @tool decorator issues)
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY", "NONE")
BASE_URL = "https://api.twitterapi.io/twitter"
SEARCH_ENDPOINT = f"{BASE_URL}/tweet/advanced_search"
USER_TWEETS_ENDPOINT = f"{BASE_URL}/user/last_tweets"


def _parse_tweet_text(tweet_data: dict) -> Optional[dict]:
    """Parse and format a single tweet."""
    try:
        # API returns author (not user), with userName (not username)
        author = tweet_data.get("author") or tweet_data.get("user") or {}
        user = author.get("userName") or author.get("username") or "unknown"
        text = tweet_data.get("text", "")
        likes = tweet_data.get("likeCount") or tweet_data.get("favorite_count") or 0
        retweets = tweet_data.get("retweetCount") or tweet_data.get("retweet_count") or 0
        created_at = tweet_data.get("createdAt") or tweet_data.get("created_at") or ""
        engagement = int(likes) + int(retweets)
        return {
            "user": user,
            "text": text,
            "engagement": engagement,
            "time": created_at,
        }
    except Exception:
        return None


def _format_tweets_report(title: str, parsed_tweets: list[dict], total_engagement: int) -> str:
    """Format tweets list as a report."""
    report = [f"--- {title} ---"]
    report.append(f"Total Engagement in Sample: {total_engagement} (Likes+RTs)")
    report.append("Top Discussions:")
    for idx, item in enumerate(parsed_tweets):
        text_preview = item["text"][:200]
        if len(item["text"]) > 200:
            text_preview += "..."
        report.append(f'{idx + 1}. @{item["user"]} (🔥{item["engagement"]}): "{text_preview}"')
    report.append("-------------------------------------------")
    return "\n".join(report)


class TwitterSearchService:
    """
    Twitter search service for whale trade verification.

    Uses Twitter API for social sentiment search.
    """

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Twitter search service.

        Args:
            api_key: Twitter API key. If not provided, reads from TWITTER_API_KEY env var.
        """
        self.api_key = api_key or os.getenv("TWITTER_API_KEY", "")
        if not self.api_key:
            logger.warning("TWITTER_API_KEY not set. Twitter search will be disabled.")

    def _get_headers(self) -> dict[str, str]:
        """Get request headers with API key."""
        return {"X-API-Key": self.api_key}

    def is_available(self) -> bool:
        """Check if Twitter search is available (API key is set)."""
        return bool(self.api_key and self.api_key.strip().upper() != "NONE")

    def search_tweets(
        self,
        query: str,
        search_mode: Literal["top", "latest"] = "top",
        limit: int = 10,
    ) -> str:
        """
        Search Twitter for tweets matching a query.

        Args:
            query: Search query (e.g., "Trump", "Bitcoin", "Fed rate")
            search_mode: "top" for most relevant, "latest" for most recent
            limit: Number of tweets to return (1-20)

        Returns:
            Formatted report of tweets with engagement metrics.
        """
        if not self.is_available():
            return "Twitter search unavailable: TWITTER_API_KEY not configured."

        limit = max(1, min(limit, 20))
        query_type = "Top" if search_mode == "top" else "Latest"

        params = {
            "query": query,
            "queryType": query_type,
            "limit": limit,
        }

        try:
            response = robust_get(
                SEARCH_ENDPOINT,
                params=params,
                headers=self._get_headers(),
                timeout=15,
            )

            if response.status_code != 200:
                logger.error(f"Twitter API error: {response.status_code} - {response.text[:200]}")
                return f"Twitter API Error: {response.status_code}"

            data = response.json()
            tweets_raw = data.get("tweets", [])

            if not tweets_raw:
                return f"No recent tweets found for '{query}'."

            # Parse tweets
            parsed_tweets = []
            total_engagement = 0

            for t in tweets_raw:
                p = _parse_tweet_text(t)
                if p:
                    parsed_tweets.append(p)
                    total_engagement += p["engagement"]

            mode_label = "Hot" if search_mode == "top" else "Latest"
            return _format_tweets_report(
                f"Twitter Search Results for '{query}' [{mode_label}]",
                parsed_tweets,
                total_engagement,
            )

        except Exception as e:
            logger.error(f"Twitter search failed: {e}")
            return f"Twitter search failed: {str(e)}"

    def search_for_market(
        self,
        market_question: str,
        limit: int = 10,
    ) -> str:
        """
        Search Twitter for information relevant to a prediction market.

        Combines both TOP (importance/engagement) and LATEST (timeliness) results
        to balance relevance and recency.

        Args:
            market_question: The market question to search for
            limit: Number of tweets per search mode (will search both top and latest)

        Returns:
            Combined search results from both search modes.
        """
        if not self.is_available():
            return "Twitter search unavailable: TWITTER_API_KEY not configured."

        results = []
        query = market_question[:100]  # Limit query length for API

        # 1. Search TOP tweets - high engagement, represents importance
        top_result = self.search_tweets(query, search_mode="top", limit=limit)
        if "No recent tweets" not in top_result and "Error" not in top_result:
            results.append("## Hot Tweets (High Engagement / Importance)\n" + top_result)

        # 2. Search LATEST tweets - real-time info, represents timeliness
        latest_result = self.search_tweets(query, search_mode="latest", limit=limit)
        if "No recent tweets" not in latest_result and "Error" not in latest_result:
            results.append("## Latest Tweets (Real-Time / Timeliness)\n" + latest_result)

        if not results:
            return f"No relevant tweets found for: {market_question[:50]}..."

        return "\n\n".join(results)


# Singleton instance
_twitter_service: Optional[TwitterSearchService] = None


def get_twitter_service() -> TwitterSearchService:
    """Get the singleton Twitter search service instance."""
    global _twitter_service
    if _twitter_service is None:
        _twitter_service = TwitterSearchService()
    return _twitter_service
