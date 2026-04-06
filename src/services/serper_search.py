"""Serper.dev web search service."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

SERPER_SEARCH_URL = "https://google.serper.dev/search"


class SerperSearchService:
    """Web search service using Serper.dev API (Google Search results)."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or ""
        if not self.api_key:
            logger.debug("SERPER_API_KEY not set. Serper search will be disabled.")

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def search(self, query: str, max_results: int = 5) -> str:
        if not self.is_available():
            return "Web search unavailable: SERPER_API_KEY not configured."

        headers = {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {"q": query, "num": max_results}

        try:
            with httpx.Client(timeout=20) as client:
                response = client.post(SERPER_SEARCH_URL, json=payload, headers=headers)

            if response.status_code != 200:
                logger.error(f"Serper API error: {response.status_code} - {response.text[:200]}")
                return f"Web search API Error: {response.status_code}"

            data = response.json()

            # Format results
            report = [f"--- Web Search Results for '{query}' ---"]

            # Knowledge graph answer
            kg = data.get("knowledgeGraph")
            if kg:
                title = kg.get("title", "")
                desc = kg.get("description", "")
                if title and desc:
                    report.append(f"**Summary**: {title} — {desc}\n")

            # Answer box
            answer_box = data.get("answerBox")
            if answer_box:
                answer = answer_box.get("answer") or answer_box.get("snippet", "")
                if answer:
                    report.append(f"**Summary**: {answer}\n")

            # Organic results
            organic = data.get("organic", [])
            if not organic:
                return f"No web search results found for '{query}'."

            for idx, item in enumerate(organic[:max_results], 1):
                title = item.get("title", "No title")
                url = item.get("link", "")
                snippet = item.get("snippet", "")
                report.append(f"{idx}. **{title}**")
                report.append(f"   Source: {url}")
                report.append(f"   {snippet}")
                report.append("")

            report.append("-------------------------------------------")
            return "\n".join(report)

        except Exception as e:
            logger.error(f"Serper search failed: {e}")
            return f"Web search failed: {str(e)}"

    def search_for_market(self, market_question: str, max_results: int = 5) -> str:
        if not self.is_available():
            return "Web search unavailable: SERPER_API_KEY not configured."

        query = market_question[:200]
        result = self.search(query, max_results=max_results)

        # Propagate errors so the unified WebSearchService can fall back
        if "API Error" in result or "search failed" in result:
            return result

        if "No web search results" not in result and "Error" not in result:
            return "## 🔍 Web Search Results (News & Analysis)\n" + result
        return f"No relevant web results found for: {market_question[:50]}..."
