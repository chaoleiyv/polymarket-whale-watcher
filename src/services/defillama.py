"""DeFiLlama API service for DeFi protocol data (TVL, revenue, token unlocks)."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFILLAMA_API = "https://api.llama.fi"


def _fmt_usd(value) -> str:
    """Format a dollar value with appropriate suffix."""
    if value is None:
        return "N/A"
    if isinstance(value, list):
        return "N/A"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return "N/A"
    abs_val = abs(value)
    if abs_val >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if abs_val >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    if abs_val >= 1_000:
        return f"${value / 1_000:,.2f}K"
    return f"${value:,.2f}"


class DefiLlamaService:
    """
    DeFiLlama API client for DeFi protocol analytics.

    Free API, no key required. Rate limits are generous.
    """

    def __init__(self):
        self._client = httpx.Client(timeout=20.0)
        self._protocols_cache: Optional[list] = None

    @staticmethod
    def is_available() -> bool:
        """Always available - no API key needed."""
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_protocols_list(self) -> list:
        """Fetch and cache the full protocols list for slug lookups."""
        if self._protocols_cache is not None:
            return self._protocols_cache
        try:
            resp = self._client.get(f"{DEFILLAMA_API}/protocols")
            resp.raise_for_status()
            self._protocols_cache = resp.json()
            return self._protocols_cache
        except Exception as e:
            logger.error(f"Failed to fetch DeFiLlama protocols list: {e}")
            return []

    def _resolve_slug(self, query: str) -> Optional[str]:
        """
        Fuzzy-match a user query to a DeFiLlama protocol slug.

        Tries exact slug match, then name match, then substring match.
        """
        q = query.strip().lower()
        protocols = self._fetch_protocols_list()

        # 1) Exact slug match
        for p in protocols:
            if p.get("slug", "").lower() == q:
                return p["slug"]

        # 2) Exact name match (case-insensitive)
        for p in protocols:
            if p.get("name", "").lower() == q:
                return p["slug"]

        # 3) Substring match on slug or name – prefer shortest match (most specific)
        candidates = []
        for p in protocols:
            slug = p.get("slug", "").lower()
            name = p.get("name", "").lower()
            if q in slug or q in name:
                candidates.append(p)

        if candidates:
            # Sort by TVL descending so the most prominent protocol wins ties
            candidates.sort(key=lambda p: p.get("tvl") or 0, reverse=True)
            return candidates[0]["slug"]

        return None

    # ------------------------------------------------------------------
    # Public methods – all return formatted strings
    # ------------------------------------------------------------------

    def get_protocol_tvl(self, protocol: str) -> str:
        """
        Get protocol TVL, TVL changes, and chain breakdown.

        Args:
            protocol: Protocol name or slug (e.g., "aave", "Lido", "uniswap").

        Returns:
            Formatted TVL report string.
        """
        slug = self._resolve_slug(protocol)
        if slug is None:
            return f"Protocol '{protocol}' not found on DeFiLlama."

        try:
            resp = self._client.get(f"{DEFILLAMA_API}/protocol/{slug}")
            if resp.status_code == 404:
                return f"Protocol '{protocol}' (slug: {slug}) not found on DeFiLlama."
            resp.raise_for_status()
            data = resp.json()

            name = data.get("name", slug)
            symbol = data.get("symbol", "")
            category = data.get("category", "N/A")
            # tvl field is a historical list; get current TVL from last entry or currentChainTvls
            tvl_data = data.get("tvl")
            if isinstance(tvl_data, list) and tvl_data:
                tvl = tvl_data[-1].get("totalLiquidityUSD", 0)
            elif isinstance(tvl_data, (int, float)):
                tvl = tvl_data
            else:
                tvl = None
            chain_tvls = data.get("chainTvls", {})

            # TVL changes
            change_1h = data.get("change_1h")
            change_1d = data.get("change_1d")
            change_7d = data.get("change_7d")

            lines = [
                f"--- {name} ({symbol}) TVL Report ---",
                f"Category: {category}",
                f"Total TVL: {_fmt_usd(tvl)}",
            ]

            if change_1h is not None:
                lines.append(f"1h Change: {change_1h:+.2f}%")
            if change_1d is not None:
                lines.append(f"24h Change: {change_1d:+.2f}%")
            if change_7d is not None:
                lines.append(f"7d Change: {change_7d:+.2f}%")

            # Chain breakdown – show top chains by TVL
            if chain_tvls:
                # chainTvls has sub-objects; the latest TVL per chain is the last entry
                chain_summary = {}
                for chain_name, chain_data in chain_tvls.items():
                    # Skip aggregated keys like "staking", "borrowed", "pool2"
                    if "-" in chain_name or chain_name in ("staking", "borrowed", "pool2", "vesting"):
                        continue
                    if isinstance(chain_data, dict):
                        tvl_history = chain_data.get("tvl", [])
                        if tvl_history:
                            chain_summary[chain_name] = tvl_history[-1].get("totalLiquidityUSD", 0)
                    elif isinstance(chain_data, (int, float)):
                        chain_summary[chain_name] = chain_data

                if chain_summary:
                    sorted_chains = sorted(chain_summary.items(), key=lambda x: x[1], reverse=True)
                    lines.append("Chain Breakdown:")
                    for chain_name, chain_tvl in sorted_chains[:10]:
                        lines.append(f"  {chain_name}: {_fmt_usd(chain_tvl)}")

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"DeFiLlama API error for '{protocol}': {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"DeFiLlama TVL query failed for '{protocol}': {e}"
            logger.error(msg)
            return msg

    def get_token_unlocks(self, protocol: str) -> str:
        """
        Get token unlock/vesting schedule for a protocol.

        Args:
            protocol: Protocol name or slug.

        Returns:
            Formatted token unlock schedule string.
        """
        slug = self._resolve_slug(protocol)
        if slug is None:
            return f"Protocol '{protocol}' not found on DeFiLlama."

        try:
            resp = self._client.get(f"{DEFILLAMA_API}/api/emission/{slug}")
            if resp.status_code == 404:
                return f"No token unlock data for '{protocol}' on DeFiLlama."
            resp.raise_for_status()
            data = resp.json()

            name = data.get("name", slug)
            token_price = data.get("tokenPrice", {})
            categories = data.get("categories", {})
            events = data.get("events", [])

            lines = [f"--- {name} Token Unlock Schedule ---"]

            # Token price info
            if isinstance(token_price, dict):
                price = token_price.get("price")
                symbol = token_price.get("symbol", "")
                if price:
                    lines.append(f"Token: {symbol.upper()} @ ${price:,.4f}")

            # Emission categories
            if categories:
                lines.append("Allocation Categories:")
                for cat_name, cat_data in categories.items():
                    if isinstance(cat_data, dict):
                        pct = cat_data.get("percentage")
                        if pct is not None:
                            lines.append(f"  {cat_name}: {pct:.1f}%")
                    else:
                        lines.append(f"  {cat_name}")

            # Upcoming events
            if events:
                lines.append("Upcoming Unlock Events:")
                shown = 0
                for event in events[:10]:
                    desc = event.get("description", "Unlock")
                    date = event.get("date", "TBD")
                    amount = event.get("noOfTokens")
                    if amount:
                        lines.append(f"  {date}: {desc} ({amount:,.0f} tokens)")
                    else:
                        lines.append(f"  {date}: {desc}")
                    shown += 1
                if len(events) > 10:
                    lines.append(f"  ... and {len(events) - 10} more events")

            if len(lines) == 1:
                lines.append("No detailed unlock data available.")

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"DeFiLlama API error for '{protocol}' unlocks: {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"DeFiLlama unlock query failed for '{protocol}': {e}"
            logger.error(msg)
            return msg

    def get_protocol_revenue(self, protocol: str) -> str:
        """
        Get protocol fees and revenue data.

        Args:
            protocol: Protocol name or slug.

        Returns:
            Formatted fees/revenue report string.
        """
        slug = self._resolve_slug(protocol)
        if slug is None:
            return f"Protocol '{protocol}' not found on DeFiLlama."

        try:
            resp = self._client.get(f"{DEFILLAMA_API}/summary/fees/{slug}")
            if resp.status_code == 404:
                return f"No fee/revenue data for '{protocol}' on DeFiLlama."
            resp.raise_for_status()
            data = resp.json()

            name = data.get("name", slug)
            category = data.get("category", "N/A")

            total_24h = data.get("total24h")
            total_7d = data.get("total7d")
            total_30d = data.get("total30d")
            total_all_time = data.get("totalAllTime")
            revenue_24h = data.get("revenue24h")
            revenue_7d = data.get("revenue7d")
            revenue_30d = data.get("revenue30d")

            lines = [
                f"--- {name} Fees & Revenue ---",
                f"Category: {category}",
            ]

            # Fees
            lines.append("Fees:")
            if total_24h is not None:
                lines.append(f"  24h Fees: {_fmt_usd(total_24h)}")
            if total_7d is not None:
                lines.append(f"  7d Fees: {_fmt_usd(total_7d)}")
            if total_30d is not None:
                lines.append(f"  30d Fees: {_fmt_usd(total_30d)}")
            if total_all_time is not None:
                lines.append(f"  All-Time Fees: {_fmt_usd(total_all_time)}")

            # Revenue (protocol revenue, subset of fees)
            has_revenue = any(v is not None for v in [revenue_24h, revenue_7d, revenue_30d])
            if has_revenue:
                lines.append("Revenue (protocol share):")
                if revenue_24h is not None:
                    lines.append(f"  24h Revenue: {_fmt_usd(revenue_24h)}")
                if revenue_7d is not None:
                    lines.append(f"  7d Revenue: {_fmt_usd(revenue_7d)}")
                if revenue_30d is not None:
                    lines.append(f"  30d Revenue: {_fmt_usd(revenue_30d)}")

            # Chain breakdown if available
            chain_data = data.get("totalDataChartBreakdown")
            if not chain_data and data.get("chains"):
                lines.append(f"Available on chains: {', '.join(data['chains'][:15])}")

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"DeFiLlama API error for '{protocol}' revenue: {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"DeFiLlama revenue query failed for '{protocol}': {e}"
            logger.error(msg)
            return msg
