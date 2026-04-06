"""Polygon.io API service for stocks, forex, and commodities market data."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

POLYGON_API = "https://api.polygon.io"


class PolygonService:
    """
    Polygon.io API client for financial market data.

    Covers: stocks, options, forex, crypto, indices, commodities futures.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.Client(timeout=15.0)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make authenticated GET request."""
        params = params or {}
        params["apiKey"] = self.api_key
        resp = self._client.get(f"{POLYGON_API}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_ticker_snapshot(self, ticker: str) -> str:
        """
        Get previous day close + recent daily bars for a ticker.

        Args:
            ticker: Ticker symbol (AAPL, TSLA, GS, SPY, QQQ, GLD, USO)

        Returns:
            Formatted price and market data report.
        """
        t = ticker.strip().upper()

        try:
            # Previous close (free tier)
            prev_data = self._get(f"/v2/aggs/ticker/{t}/prev")
            results = prev_data.get("results", [])

            if not results:
                return f"No data found for '{ticker}' on Polygon.io."

            bar = results[0]
            close = bar.get("c", 0)
            open_p = bar.get("o", 0)
            high = bar.get("h", 0)
            low = bar.get("l", 0)
            volume = bar.get("v", 0)
            vwap = bar.get("vw", 0)

            change = close - open_p if open_p else 0
            change_pct = (change / open_p * 100) if open_p else 0

            lines = [
                f"--- {t} Last Trading Day (Polygon.io) ---",
                f"Close: ${close:,.2f}",
                f"Open: ${open_p:,.2f}",
                f"High: ${high:,.2f}",
                f"Low: ${low:,.2f}",
                f"Change: {change:+.2f} ({change_pct:+.2f}%)",
            ]
            if vwap:
                lines.append(f"VWAP: ${vwap:,.2f}")
            if volume:
                lines.append(f"Volume: {volume:,.0f}")

            # Also try to get 5-day bars for trend
            try:
                from datetime import date, timedelta
                end = date.today()
                start = end - timedelta(days=10)
                range_data = self._get(
                    f"/v2/aggs/ticker/{t}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                    params={"adjusted": "true", "sort": "asc", "limit": 10},
                )
                bars = range_data.get("results", [])
                if len(bars) >= 2:
                    first_close = bars[0].get("c", 0)
                    last_close = bars[-1].get("c", 0)
                    if first_close:
                        week_change = ((last_close - first_close) / first_close) * 100
                        lines.append(f"~{len(bars)}-day Change: {week_change:+.2f}%")
            except Exception:
                pass  # trend data is optional

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Ticker '{ticker}' not found on Polygon.io."
            return f"Polygon API error for '{ticker}': HTTP {e.response.status_code}"
        except Exception as e:
            msg = f"Polygon query failed for '{ticker}': {e}"
            logger.error(msg)
            return msg

    def get_market_news(self, ticker: str, limit: int = 5) -> str:
        """
        Get recent news articles for a ticker.

        Args:
            ticker: Stock/crypto ticker (e.g. AAPL, TSLA, GS)
            limit: Number of articles (1-10)

        Returns:
            Formatted news report.
        """
        t = ticker.strip().upper()
        limit = max(1, min(limit, 10))

        try:
            data = self._get("/v2/reference/news", params={
                "ticker": t,
                "limit": limit,
                "order": "desc",
                "sort": "published_utc",
            })

            results = data.get("results", [])
            if not results:
                return f"No recent news found for '{ticker}'."

            lines = [f"--- {t} Recent News (Polygon.io) ---"]
            for i, article in enumerate(results, 1):
                title = article.get("title", "No title")
                published = article.get("published_utc", "")[:19]
                source = article.get("publisher", {}).get("name", "Unknown")
                desc = article.get("description", "")[:200]
                if len(article.get("description", "")) > 200:
                    desc += "..."

                lines.append(f"{i}. **{title}**")
                lines.append(f"   Source: {source} | {published}")
                if desc:
                    lines.append(f"   {desc}")
                lines.append("")

            lines.append("---")
            return "\n".join(lines)

        except Exception as e:
            msg = f"Polygon news query failed for '{ticker}': {e}"
            logger.error(msg)
            return msg
