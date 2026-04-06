"""Unified web search with fallback: Tavily -> Serper -> DuckDuckGo."""
import logging
from typing import Optional

from src.services.tavily_search import TavilySearchService
from src.services.serper_search import SerperSearchService
from src.services.ddg_search import DDGSearchService

logger = logging.getLogger(__name__)

# Responses that indicate a search engine failed (not just "no results")
_FAILURE_KEYWORDS = ("API Error", "search failed", "unavailable", "exceeds your plan")


def _is_failure(result: str) -> bool:
    return any(kw in result for kw in _FAILURE_KEYWORDS)


class WebSearchService:
    """
    Unified web search with automatic fallback.

    Priority: Tavily (best quality) -> Serper -> DuckDuckGo (free).
    Falls back to the next engine when the current one errors.
    """

    def __init__(
        self,
        tavily_api_key: str = "",
        serper_api_key: str = "",
    ):
        self._engines = []

        tavily = TavilySearchService(api_key=tavily_api_key)
        if tavily.is_available():
            self._engines.append(("Tavily", tavily))

        serper = SerperSearchService(api_key=serper_api_key)
        if serper.is_available():
            self._engines.append(("Serper", serper))

        ddg = DDGSearchService()
        if ddg.is_available():
            self._engines.append(("DuckDuckGo", ddg))

        if self._engines:
            names = [name for name, _ in self._engines]
            logger.info(f"Web search engines: {' -> '.join(names)}")
        else:
            logger.warning("No web search engine available.")

    def is_available(self) -> bool:
        return len(self._engines) > 0

    def search(self, query: str, max_results: int = 5) -> str:
        if not self._engines:
            return "Web search unavailable: no search engine configured."

        for name, engine in self._engines:
            result = engine.search(query, max_results=max_results)
            if _is_failure(result):
                logger.warning(f"{name} search failed, trying next engine...")
                continue
            return result

        return f"All web search engines failed for: '{query}'"

    def search_for_market(self, market_question: str, max_results: int = 5) -> str:
        if not self._engines:
            return "Web search unavailable: no search engine configured."

        for name, engine in self._engines:
            result = engine.search_for_market(market_question, max_results=max_results)
            if _is_failure(result):
                logger.warning(f"{name} market search failed, trying next engine...")
                continue
            return result

        return f"No relevant web results found for: {market_question[:50]}..."
