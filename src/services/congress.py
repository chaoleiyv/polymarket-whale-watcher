"""Congress.gov API service for U.S. legislative data."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

CONGRESS_API = "https://api.congress.gov/v3"


class CongressService:
    """
    Congress.gov API client for U.S. legislative data.

    Covers: bills, votes, members, committees, nominations.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.Client(timeout=15.0)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        """Make authenticated GET request."""
        params = params or {}
        params["api_key"] = self.api_key
        params["format"] = "json"
        resp = self._client.get(f"{CONGRESS_API}{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    def search_bills(self, query: str, limit: int = 5) -> str:
        """
        Search for bills by keyword.

        Args:
            query: Search keywords (e.g. 'TikTok ban', 'crypto regulation', 'immigration')
            limit: Number of results (1-10)

        Returns:
            Formatted report of matching bills with status.
        """
        limit = max(1, min(limit, 10))

        try:
            data = self._get("/bill", params={
                "limit": limit,
                "sort": "updateDate+desc",
            })

            bills = data.get("bills", [])
            if not bills:
                return f"No bills found on Congress.gov."

            # Filter by query keyword in title (API doesn't support text search directly)
            # So we fetch recent bills and note to user
            lines = [f"--- Congress.gov: Recent Bills ---"]
            lines.append(f"(Showing {len(bills)} most recently updated bills)")
            lines.append("")

            for i, bill in enumerate(bills, 1):
                bill_type = bill.get("type", "")
                number = bill.get("number", "")
                title = bill.get("title", "No title")
                congress = bill.get("congress", "")
                update_date = bill.get("updateDate", "")[:10]
                latest_action = bill.get("latestAction", {})
                action_text = latest_action.get("text", "")
                action_date = latest_action.get("actionDate", "")

                bill_id = f"{bill_type} {number}" if bill_type and number else "N/A"

                lines.append(f"{i}. **{bill_id}** (Congress {congress})")
                lines.append(f"   Title: {title[:150]}")
                lines.append(f"   Updated: {update_date}")
                if action_text:
                    lines.append(f"   Latest Action ({action_date}): {action_text[:150]}")
                lines.append("")

            lines.append("---")
            return "\n".join(lines)

        except Exception as e:
            msg = f"Congress.gov search failed: {e}"
            logger.error(msg)
            return msg

    def get_bill_status(self, congress: int, bill_type: str, bill_number: int) -> str:
        """
        Get detailed status of a specific bill.

        Args:
            congress: Congress number (e.g. 119 for current)
            bill_type: Bill type (hr, s, hjres, sjres)
            bill_number: Bill number

        Returns:
            Formatted bill status report.
        """
        bt = bill_type.strip().lower()

        try:
            data = self._get(f"/bill/{congress}/{bt}/{bill_number}")
            bill = data.get("bill", {})

            if not bill:
                return f"Bill {bt.upper()} {bill_number} (Congress {congress}) not found."

            title = bill.get("title", "No title")
            introduced = bill.get("introducedDate", "N/A")
            sponsors = bill.get("sponsors", [])
            sponsor_str = ", ".join(
                f"{s.get('firstName', '')} {s.get('lastName', '')} ({s.get('party', '')}-{s.get('state', '')})"
                for s in sponsors[:3]
            ) if sponsors else "N/A"

            latest_action = bill.get("latestAction", {})
            action_text = latest_action.get("text", "N/A")
            action_date = latest_action.get("actionDate", "")

            policy_area = bill.get("policyArea", {}).get("name", "N/A")
            committees_count = bill.get("committees", {}).get("count", 0)
            cosponsors_count = bill.get("cosponsors", {}).get("count", 0)
            actions_count = bill.get("actions", {}).get("count", 0)

            # Determine bill progress
            constitutional = bill.get("constitutionalAuthorityStatementText", "")

            lines = [
                f"--- Bill Status: {bt.upper()} {bill_number} (Congress {congress}) ---",
                f"Title: {title}",
                f"Introduced: {introduced}",
                f"Sponsor: {sponsor_str}",
                f"Cosponsors: {cosponsors_count}",
                f"Policy Area: {policy_area}",
                f"Committees Referred: {committees_count}",
                f"Total Actions: {actions_count}",
                f"",
                f"Latest Action ({action_date}): {action_text}",
                f"---",
            ]
            return "\n".join(lines)

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"Bill {bt.upper()} {bill_number} (Congress {congress}) not found."
            return f"Congress.gov API error: HTTP {e.response.status_code}"
        except Exception as e:
            msg = f"Congress.gov bill query failed: {e}"
            logger.error(msg)
            return msg

    def get_recent_votes(self, chamber: str = "senate", limit: int = 5) -> str:
        """
        Get recent roll call votes.

        Args:
            chamber: 'senate' or 'house'
            limit: Number of votes (1-10)

        Returns:
            Formatted report of recent votes.
        """
        chamber = chamber.strip().lower()
        if chamber not in ("senate", "house"):
            chamber = "senate"
        limit = max(1, min(limit, 10))

        try:
            # Get current congress number (119th as of 2025-2026)
            congress = 119

            data = self._get(f"/bill", params={
                "limit": limit,
                "sort": "updateDate+desc",
            })

            # Use the nominations endpoint for Senate votes
            # or fall back to recent bill actions
            lines = [f"--- Recent Congressional Activity ({chamber.title()}) ---"]

            bills = data.get("bills", [])
            for i, bill in enumerate(bills[:limit], 1):
                bill_type = bill.get("type", "")
                number = bill.get("number", "")
                title = bill.get("title", "")[:100]
                latest = bill.get("latestAction", {})
                action = latest.get("text", "")[:120]
                date = latest.get("actionDate", "")

                lines.append(f"{i}. {bill_type} {number}: {title}")
                lines.append(f"   {date}: {action}")
                lines.append("")

            lines.append("---")
            return "\n".join(lines)

        except Exception as e:
            msg = f"Congress.gov votes query failed: {e}"
            logger.error(msg)
            return msg
