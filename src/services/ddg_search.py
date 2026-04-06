"""DuckDuckGo web search service — free, no API key required."""
import logging

logger = logging.getLogger(__name__)


class DDGSearchService:
    """Web search service using duckduckgo-search (no API key needed)."""

    def __init__(self):
        self._available: bool | None = None

    def is_available(self) -> bool:
        if self._available is None:
            try:
                from duckduckgo_search import DDGS  # noqa: F401
                self._available = True
            except ImportError:
                logger.warning(
                    "duckduckgo-search not installed. "
                    "Install with: pip install duckduckgo-search"
                )
                self._available = False
        return self._available

    def search(self, query: str, max_results: int = 5) -> str:
        if not self.is_available():
            return "Web search unavailable: duckduckgo-search package not installed."

        try:
            from duckduckgo_search import DDGS

            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))

            if not results:
                return f"No web search results found for '{query}'."

            report = [f"--- Web Search Results for '{query}' ---"]
            for idx, item in enumerate(results, 1):
                title = item.get("title", "No title")
                url = item.get("href", "")
                body = item.get("body", "")[:300]
                if len(item.get("body", "")) > 300:
                    body += "..."
                report.append(f"{idx}. **{title}**")
                report.append(f"   Source: {url}")
                report.append(f"   {body}")
                report.append("")
            report.append("-------------------------------------------")
            return "\n".join(report)

        except Exception as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            return f"Web search failed: {str(e)}"

    def search_for_market(self, market_question: str, max_results: int = 5) -> str:
        query = market_question[:200]
        result = self.search(query, max_results=max_results)
        if "No web search results" not in result and "Error" not in result and "unavailable" not in result:
            return "## 🔍 Web Search Results (News & Analysis)\n" + result
        return f"No relevant web results found for: {market_question[:50]}..."
