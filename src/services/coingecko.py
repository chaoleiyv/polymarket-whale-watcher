"""CoinGecko API service for cryptocurrency market data."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

COINGECKO_API = "https://api.coingecko.com/api/v3"

# Common coin ID mapping (Polymarket markets often use ticker symbols)
TICKER_TO_ID = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "MATIC": "matic-network",
    "LINK": "chainlink",
    "UNI": "uniswap",
    "SHIB": "shiba-inu",
    "LTC": "litecoin",
    "BNB": "binancecoin",
    "NEAR": "near",
    "ARB": "arbitrum",
    "OP": "optimism",
    "APT": "aptos",
    "SUI": "sui",
    "PEPE": "pepe",
}


def _resolve_coin_id(query: str) -> str:
    """Resolve a ticker or name to a CoinGecko coin ID."""
    q = query.strip().upper()
    if q in TICKER_TO_ID:
        return TICKER_TO_ID[q]
    # Try lowercase as-is (CoinGecko IDs are lowercase)
    return query.strip().lower()


class CoinGeckoService:
    """
    CoinGecko API client for crypto market data.

    Free tier: 30 calls/min, no API key required.
    """

    def __init__(self):
        self._client = httpx.Client(timeout=15.0)

    def get_price(self, coin: str) -> str:
        """
        Get current price, 24h change, market cap, and volume for a cryptocurrency.

        Args:
            coin: Ticker symbol (BTC, ETH, SOL) or CoinGecko ID (bitcoin, ethereum)

        Returns:
            Formatted price report string.
        """
        coin_id = _resolve_coin_id(coin)

        try:
            resp = self._client.get(
                f"{COINGECKO_API}/coins/{coin_id}",
                params={
                    "localization": "false",
                    "tickers": "false",
                    "community_data": "false",
                    "developer_data": "false",
                    "sparkline": "false",
                },
            )

            if resp.status_code == 404:
                return f"Coin '{coin}' (id: {coin_id}) not found on CoinGecko."
            resp.raise_for_status()
            data = resp.json()

            market = data.get("market_data", {})
            name = data.get("name", coin_id)
            symbol = data.get("symbol", "").upper()

            price = market.get("current_price", {}).get("usd")
            change_24h = market.get("price_change_percentage_24h")
            change_7d = market.get("price_change_percentage_7d")
            change_30d = market.get("price_change_percentage_30d")
            high_24h = market.get("high_24h", {}).get("usd")
            low_24h = market.get("low_24h", {}).get("usd")
            market_cap = market.get("market_cap", {}).get("usd")
            volume_24h = market.get("total_volume", {}).get("usd")
            ath = market.get("ath", {}).get("usd")
            ath_change = market.get("ath_change_percentage", {}).get("usd")

            lines = [
                f"--- {name} ({symbol}) Market Data ---",
                f"Price: ${price:,.2f}" if price else "Price: N/A",
            ]

            if high_24h and low_24h:
                lines.append(f"24h Range: ${low_24h:,.2f} - ${high_24h:,.2f}")

            if change_24h is not None:
                lines.append(f"24h Change: {change_24h:+.2f}%")
            if change_7d is not None:
                lines.append(f"7d Change: {change_7d:+.2f}%")
            if change_30d is not None:
                lines.append(f"30d Change: {change_30d:+.2f}%")

            if market_cap:
                lines.append(f"Market Cap: ${market_cap:,.0f}")
            if volume_24h:
                lines.append(f"24h Volume: ${volume_24h:,.0f}")

            if ath and ath_change is not None:
                lines.append(f"ATH: ${ath:,.2f} ({ath_change:+.1f}% from ATH)")

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"CoinGecko API error for '{coin}': {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"CoinGecko query failed for '{coin}': {e}"
            logger.error(msg)
            return msg

    def get_market_overview(self) -> str:
        """
        Get global crypto market overview: total market cap, BTC dominance, etc.

        Returns:
            Formatted global market overview string.
        """
        try:
            resp = self._client.get(f"{COINGECKO_API}/global")
            resp.raise_for_status()
            data = resp.json().get("data", {})

            total_cap = data.get("total_market_cap", {}).get("usd", 0)
            total_vol = data.get("total_volume", {}).get("usd", 0)
            btc_dom = data.get("market_cap_percentage", {}).get("btc", 0)
            eth_dom = data.get("market_cap_percentage", {}).get("eth", 0)
            change_24h = data.get("market_cap_change_percentage_24h_usd", 0)
            active_coins = data.get("active_cryptocurrencies", 0)

            lines = [
                "--- Global Crypto Market Overview ---",
                f"Total Market Cap: ${total_cap:,.0f}",
                f"24h Change: {change_24h:+.2f}%",
                f"24h Volume: ${total_vol:,.0f}",
                f"BTC Dominance: {btc_dom:.1f}%",
                f"ETH Dominance: {eth_dom:.1f}%",
                f"Active Coins: {active_coins:,}",
                "---",
            ]
            return "\n".join(lines)

        except Exception as e:
            msg = f"CoinGecko global query failed: {e}"
            logger.error(msg)
            return msg
