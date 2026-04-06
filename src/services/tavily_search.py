"""Tavily web search service - replaces Google Search for whale trade verification."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


def _format_search_results(query: str, results: list[dict]) -> str:
    """Format Tavily search results as a readable report."""
    report = [f"--- Web Search Results for '{query}' ---"]
    for idx, item in enumerate(results, 1):
        title = item.get("title", "No title")
        url = item.get("url", "")
        content = item.get("content", "")[:300]
        if len(item.get("content", "")) > 300:
            content += "..."
        report.append(f"{idx}. **{title}**")
        report.append(f"   Source: {url}")
        report.append(f"   {content}")
        report.append("")
    report.append("-------------------------------------------")
    return "\n".join(report)


class TavilySearchService:
    """
    Web search service using Tavily API for whale trade verification.

    Replaces Google Search grounding with explicit Tavily web search,
    passing results as context to the LLM.
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or ""
        if not self.api_key:
            logger.warning("TAVILY_API_KEY not set. Web search will be disabled.")

    def is_available(self) -> bool:
        """Check if Tavily search is available (API key is set)."""
        return bool(self.api_key and self.api_key.strip())

    def search(
        self,
        query: str,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> str:
        """
        Search the web using Tavily API.

        Args:
            query: Search query
            max_results: Number of results to return (1-10)
            search_depth: "basic" for fast search, "advanced" for deeper search

        Returns:
            Formatted search results report.
        """
        if not self.is_available():
            return "Web search unavailable: TAVILY_API_KEY not configured."

        max_results = max(1, min(max_results, 10))

        payload = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": search_depth,
            "max_results": max_results,
            "include_answer": True,
        }

        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(TAVILY_SEARCH_URL, json=payload)

            if response.status_code != 200:
                logger.error(f"Tavily API error: {response.status_code} - {response.text[:200]}")
                return f"Web search API Error: {response.status_code}"

            data = response.json()
            results = data.get("results", [])

            if not results:
                return f"No web search results found for '{query}'."

            report_parts = []

            # Include AI-generated answer summary if available
            answer = data.get("answer")
            if answer:
                report_parts.append(f"**Summary**: {answer}\n")

            report_parts.append(_format_search_results(query, results))
            return "\n".join(report_parts)

        except Exception as e:
            logger.error(f"Tavily search failed: {e}")
            return f"Web search failed: {str(e)}"

    def search_for_market(
        self,
        market_question: str,
        max_results: int = 5,
    ) -> str:
        """
        Search the web for information relevant to a prediction market.

        Performs a deeper search for market-relevant news.

        Args:
            market_question: The market question to search for
            max_results: Number of results per query

        Returns:
            Combined search results.
        """
        if not self.is_available():
            return "Web search unavailable: TAVILY_API_KEY not configured."

        results = []

        # Search with the market question directly
        query = market_question[:200]
        main_result = self.search(query, max_results=max_results, search_depth="advanced")

        # Propagate errors so the unified WebSearchService can fall back
        if "API Error" in main_result or "search failed" in main_result:
            return main_result

        if "No web search results" not in main_result and "Error" not in main_result:
            results.append("## 🔍 Web Search Results (News & Analysis)\n" + main_result)

        if not results:
            return f"No relevant web results found for: {market_question[:50]}..."

        return "\n\n".join(results)
