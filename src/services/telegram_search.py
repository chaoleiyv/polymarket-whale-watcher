"""Telegram search service for crypto and geopolitical channel monitoring."""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List

try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Default public channels: crypto + geopolitics/politics
# Verified 2026-03-31: all channels return text messages with meaningful content
DEFAULT_CHANNELS = [
    # Crypto / macro
    "CryptoVIPSignalTA",    # crypto signals + macro news
    "whale_alert_io",       # on-chain whale transfers
    "WatcherGuru",          # crypto/macro breaking news
    # Geopolitics & politics
    "DDGeopolitics",        # geopolitical analysis (views ~20k)
    "disclosetv",           # US politics, breaking news (views ~50-70k)
    "realDonaldTrump",      # Trump's own posts
    "intelslava",           # Russia/Ukraine, geopolitics (views ~50k)
    "TheScrollOfBenjamin",  # Middle East geopolitics
    "WarMonitors",          # conflict breaking news (views ~14k, active)
]


class TelegramSearchService:
    """
    Searches crypto-relevant public Telegram channels for messages.

    Uses Telethon (MTProto API) to search public channels by keyword.
    Requires a one-time auth to generate a session string.
    """

    def __init__(
        self,
        api_id: str = "",
        api_hash: str = "",
        session_string: str = "",
        channels: Optional[List[str]] = None,
    ):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_string = session_string
        self.channels = channels or DEFAULT_CHANNELS
        self._client = None

    def is_available(self) -> bool:
        """Check if Telegram search is available."""
        if not (self.api_id and self.api_hash and self.session_string):
            return False
        try:
            import telethon  # noqa: F401
            return True
        except ImportError:
            logger.warning("telethon not installed. Telegram search disabled.")
            return False

    def _get_client(self):
        """Get or create the Telethon client."""
        if self._client is None:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
            self._client = TelegramClient(
                StringSession(self.session_string),
                int(self.api_id),
                self.api_hash,
            )
        return self._client

    async def _search_channel(
        self,
        channel: str,
        query: str,
        limit: int = 5,
    ) -> List[dict]:
        """Search a single channel for messages matching a query."""
        from telethon.errors import (
            FloodWaitError,
            ChannelPrivateError,
            UsernameNotOccupiedError,
            UsernameInvalidError,
        )

        results = []
        try:
            client = self._get_client()
            async for message in client.iter_messages(
                channel,
                search=query,
                limit=limit,
            ):
                if not message.text:
                    continue
                results.append({
                    "channel": channel,
                    "text": message.text,
                    "date": message.date.strftime("%Y-%m-%d %H:%M UTC") if message.date else "",
                    "views": message.views or 0,
                    "forwards": message.forwards or 0,
                })
        except FloodWaitError as e:
            logger.warning(f"Telegram flood wait: {e.seconds}s for channel {channel}")
        except (ChannelPrivateError, UsernameNotOccupiedError, UsernameInvalidError):
            logger.debug(f"Channel {channel} not accessible, skipping")
        except Exception as e:
            logger.error(f"Error searching Telegram channel {channel}: {e}")
        return results

    async def _search_all_channels(self, query: str, limit_per_channel: int = 5) -> List[dict]:
        """Search all configured channels."""
        client = self._get_client()
        async with client:
            all_results = []
            for channel in self.channels:
                results = await self._search_channel(channel, query, limit_per_channel)
                all_results.extend(results)
            # Sort by views descending
            all_results.sort(key=lambda x: x["views"], reverse=True)
            return all_results

    def _format_report(self, query: str, messages: List[dict]) -> str:
        """Format search results as a report string."""
        if not messages:
            return f"No Telegram messages found for '{query}'."

        total_views = sum(m["views"] for m in messages)
        lines = [
            f"--- Telegram Search Results for '{query}' ---",
            f"Total Views in Sample: {total_views:,}",
            f"Channels Searched: {', '.join(self.channels)}",
            "Messages:",
        ]

        for idx, msg in enumerate(messages):
            text_preview = msg["text"][:200]
            if len(msg["text"]) > 200:
                text_preview += "..."
            lines.append(
                f'{idx + 1}. [{msg["channel"]}] ({msg["date"]}, '
                f'👁 {msg["views"]:,}): "{text_preview}"'
            )

        lines.append("-------------------------------------------")
        return "\n".join(lines)

    def search_for_market(self, query: str, limit: int = 10) -> str:
        """
        Search Telegram channels for messages relevant to a market.

        Args:
            query: Search query
            limit: Max total messages to return

        Returns:
            Formatted report string
        """
        if not self.is_available():
            return "Telegram search unavailable: credentials not configured or telethon not installed."

        try:
            # Calculate per-channel limit
            limit_per_channel = max(3, limit // len(self.channels))

            # Run async search (nest_asyncio allows nested run_until_complete)
            loop = asyncio.get_event_loop()
            messages = loop.run_until_complete(
                self._search_all_channels(query, limit_per_channel)
            )

            # Trim to total limit
            messages = messages[:limit]
            return self._format_report(query, messages)

        except Exception as e:
            logger.error(f"Telegram search failed: {e}")
            return f"Telegram search failed: {str(e)}"
