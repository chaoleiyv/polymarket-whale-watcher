"""
Tool registry for LLM function calling.

Each tool is a callable that the LLM can invoke on demand.
Tools are registered with their OpenAI-compatible function schema
and an executor function that performs the actual work.
"""
import json
import logging
from dataclasses import dataclass
from typing import Callable, Dict, List

from src.services.twitter_search import TwitterSearchService
from src.services.web_search import WebSearchService
from src.services.coingecko import CoinGeckoService
from src.services.fred import FREDService
from src.services.polygon import PolygonService
from src.services.congress import CongressService
from src.services.defillama import DefiLlamaService
from src.services.etherscan import EtherscanService
from src.services.telegram_search import TelegramSearchService

logger = logging.getLogger(__name__)


@dataclass
class Tool:
    """A tool available to the LLM."""
    name: str
    description: str
    parameters: dict  # JSON Schema for parameters
    execute: Callable[..., str]  # (kwargs) -> result string


class ToolRegistry:
    """
    Registry of tools available for LLM function calling.

    Usage:
        registry = ToolRegistry(twitter_api_key="...", tavily_api_key="...")
        schemas = registry.openai_tool_schemas()    # pass to LLM
        result = registry.call("search_twitter", query="Bitcoin")  # execute
    """

    def __init__(
        self,
        twitter_api_key: str,
        tavily_api_key: str,
        fred_api_key: str = "",
        polygon_api_key: str = "",
        congress_api_key: str = "",
        etherscan_api_key: str = "",
        serper_api_key: str = "",
        telegram_api_id: str = "",
        telegram_api_hash: str = "",
        telegram_session_string: str = "",
        telegram_channels: str = "",
    ):
        self._tools: Dict[str, Tool] = {}

        # -- Twitter search --
        twitter = TwitterSearchService(api_key=twitter_api_key)
        if twitter.is_available():
            self._register(Tool(
                name="search_twitter",
                description=(
                    "Search Twitter/X for real-time social sentiment, KOL opinions, "
                    "and breaking news about a topic. Returns top and latest tweets "
                    "with engagement metrics. Best for: real-time sentiment, crypto "
                    "community reactions, political commentary, breaking news that "
                    "hasn't hit mainstream media yet."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g. 'Bitcoin ETF', 'Trump indictment')",
                        },
                    },
                    "required": ["query"],
                },
                execute=lambda query: twitter.search_for_market(query, limit=10),
            ))

        # -- Telegram search (crypto channels) --
        telegram = TelegramSearchService(
            api_id=telegram_api_id,
            api_hash=telegram_api_hash,
            session_string=telegram_session_string,
            channels=telegram_channels.split(",") if telegram_channels.strip() else None,
        )
        if telegram.is_available():
            self._register(Tool(
                name="search_telegram",
                description=(
                    "Search Telegram channels for recent messages about a topic. "
                    "Covers crypto (Whale Alert, WatcherGuru, CryptoVIPSignalTA) and "
                    "politics/geopolitics (Disclose.tv, DDGeopolitics, Intel Slava, "
                    "PoliticsForAll, Trump, TheScrollOfBenjamin). "
                    "Returns messages with view counts. "
                    "Best for: US politics, Trump news, approval ratings, elections, "
                    "geopolitics, military conflicts, Russia/Ukraine, Middle East, "
                    "macro economics, crypto news, whale transfers, and breaking news."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g. 'MegaETH launch', 'Solana outage')",
                        },
                    },
                    "required": ["query"],
                },
                execute=lambda query: telegram.search_for_market(query, limit=10),
            ))

        # -- Web search (Tavily -> Serper -> DuckDuckGo fallback) --
        web_search = WebSearchService(
            tavily_api_key=tavily_api_key,
            serper_api_key=serper_api_key,
        )
        if web_search.is_available():
            self._register(Tool(
                name="search_web",
                description=(
                    "Search the web for recent news articles, analysis, and factual "
                    "information about a topic. Returns article summaries with sources. "
                    "Best for: verifying events, finding official announcements, "
                    "regulatory news, earnings reports, court rulings, legislation status."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query for news and analysis",
                        },
                    },
                    "required": ["query"],
                },
                execute=lambda query: web_search.search_for_market(query, max_results=5),
            ))

        # -- CoinGecko crypto data --
        coingecko = CoinGeckoService()
        self._register(Tool(
            name="get_crypto_price",
            description=(
                "Get real-time cryptocurrency price, 24h/7d/30d change, market cap, "
                "volume, and ATH data. Accepts ticker symbols (BTC, ETH, SOL) or "
                "CoinGecko IDs. Best for: any market involving crypto price targets "
                "(e.g. 'Will BTC hit $100k'), crypto market conditions."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "coin": {
                        "type": "string",
                        "description": "Ticker symbol (BTC, ETH, SOL) or CoinGecko coin ID",
                    },
                },
                "required": ["coin"],
            },
            execute=lambda coin: coingecko.get_price(coin),
        ))

        self._register(Tool(
            name="get_crypto_market_overview",
            description=(
                "Get global crypto market overview: total market cap, 24h change, "
                "BTC/ETH dominance, trading volume. Best for: understanding overall "
                "crypto market sentiment and conditions."
            ),
            parameters={
                "type": "object",
                "properties": {},
            },
            execute=lambda: coingecko.get_market_overview(),
        ))

        # -- FRED macroeconomic data --
        fred = FREDService(api_key=fred_api_key)
        if fred.is_available():
            self._register(Tool(
                name="get_economic_data",
                description=(
                    "Get macroeconomic data from FRED (Federal Reserve). Supports "
                    "common names: fed_rate, cpi, inflation, unemployment, gdp, "
                    "oil_price, wti, brent, gold, vix, sp500, yield_curve, "
                    "jobless_claims, 10y_treasury, 2y_treasury, dollar_index. "
                    "Also accepts any FRED series ID (e.g. FEDFUNDS, UNRATE). "
                    "Returns recent data points with trend. Best for: Fed policy "
                    "markets, inflation bets, employment data, oil/commodity prices, "
                    "recession indicators."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Common name (fed_rate, cpi, unemployment, oil_price, "
                                "vix, gold, sp500) or FRED series ID"
                            ),
                        },
                    },
                    "required": ["query"],
                },
                execute=lambda query: fred.get_series(query),
            ))

        # -- Polygon.io financial data --
        polygon = PolygonService(api_key=polygon_api_key)
        if polygon.is_available():
            self._register(Tool(
                name="get_stock_price",
                description=(
                    "Get real-time stock/ETF price snapshot from Polygon.io. "
                    "Includes price, daily change, volume, day range. "
                    "Examples: AAPL, TSLA, GS, META, SPY, QQQ, GLD, USO. "
                    "Best for: markets involving specific company events "
                    "(IPOs, earnings, lawsuits), sector ETFs, gold/oil ETFs."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Ticker symbol (e.g. AAPL, TSLA, GS, SPY, GLD)",
                        },
                    },
                    "required": ["ticker"],
                },
                execute=lambda ticker: polygon.get_ticker_snapshot(ticker),
            ))

            self._register(Tool(
                name="get_stock_news",
                description=(
                    "Get recent news articles for a stock/company from Polygon.io. "
                    "Returns headlines, sources, and summaries. "
                    "Best for: company-specific events, earnings surprises, "
                    "M&A rumors, regulatory actions, CEO statements."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "ticker": {
                            "type": "string",
                            "description": "Stock ticker (e.g. AAPL, TSLA, GS)",
                        },
                    },
                    "required": ["ticker"],
                },
                execute=lambda ticker: polygon.get_market_news(ticker),
            ))

        # -- Congress.gov legislative data --
        congress_svc = CongressService(api_key=congress_api_key)
        if congress_svc.is_available():
            self._register(Tool(
                name="get_bill_status",
                description=(
                    "Get status of a specific U.S. Congressional bill. "
                    "Requires congress number (e.g. 119), bill type (hr, s, hjres, sjres), "
                    "and bill number. Returns sponsor, cosponsors, latest action, "
                    "committee referrals. Best for: markets about specific legislation "
                    "(TikTok ban, crypto regulation, immigration reform, tax bills)."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "congress": {
                            "type": "integer",
                            "description": "Congress number (119 for 2025-2026)",
                        },
                        "bill_type": {
                            "type": "string",
                            "description": "Bill type: hr (House), s (Senate), hjres, sjres",
                        },
                        "bill_number": {
                            "type": "integer",
                            "description": "Bill number",
                        },
                    },
                    "required": ["congress", "bill_type", "bill_number"],
                },
                execute=lambda congress, bill_type, bill_number: congress_svc.get_bill_status(
                    congress, bill_type, bill_number
                ),
            ))

            self._register(Tool(
                name="get_recent_legislation",
                description=(
                    "Get recently updated U.S. Congressional bills. "
                    "Returns latest bills with their current status and actions. "
                    "Best for: understanding current legislative activity, "
                    "political markets about government actions, policy changes."
                ),
                parameters={
                    "type": "object",
                    "properties": {},
                },
                execute=lambda: congress_svc.search_bills("", limit=5),
            ))

        # -- DeFiLlama (DeFi protocol data, no API key needed) --
        defillama = DefiLlamaService()
        self._register(Tool(
            name="get_protocol_tvl",
            description=(
                "Get DeFi protocol TVL (Total Value Locked), TVL changes (1h/24h/7d), "
                "and chain breakdown from DeFiLlama. Accepts protocol name or slug "
                "(e.g. 'aave', 'uniswap', 'lido', 'eigenlayer'). "
                "Best for: token launch FDV markets, DeFi protocol health, "
                "evaluating project fundamentals before/after token launch."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "description": "Protocol name or slug (e.g. 'aave', 'uniswap', 'megaeth')",
                    },
                },
                "required": ["protocol"],
            },
            execute=lambda protocol: defillama.get_protocol_tvl(protocol),
        ))

        self._register(Tool(
            name="get_token_unlocks",
            description=(
                "Get token unlock/vesting schedule for a DeFi protocol from DeFiLlama. "
                "Shows allocation categories and upcoming unlock events. "
                "Best for: understanding token supply dynamics, evaluating FDV markets, "
                "predicting sell pressure from upcoming unlocks."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "description": "Protocol name or slug (e.g. 'arbitrum', 'optimism', 'eigenlayer')",
                    },
                },
                "required": ["protocol"],
            },
            execute=lambda protocol: defillama.get_token_unlocks(protocol),
        ))

        self._register(Tool(
            name="get_protocol_revenue",
            description=(
                "Get DeFi protocol fees and revenue (24h/7d/30d/all-time) from DeFiLlama. "
                "Best for: evaluating protocol fundamentals, comparing revenue vs FDV, "
                "assessing if a token launch valuation is justified."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "protocol": {
                        "type": "string",
                        "description": "Protocol name or slug (e.g. 'aave', 'uniswap', 'gmx')",
                    },
                },
                "required": ["protocol"],
            },
            execute=lambda protocol: defillama.get_protocol_revenue(protocol),
        ))

        # -- Etherscan (on-chain data) --
        etherscan = EtherscanService(api_key=etherscan_api_key)
        if etherscan.is_available():
            self._register(Tool(
                name="get_wallet_transfers",
                description=(
                    "Get recent ERC-20 token transfers (USDC, USDT, WETH, DAI) for an "
                    "Ethereum wallet address from Etherscan. Shows direction (IN/OUT), "
                    "amount, counterparty, and flags large transfers (>$10k). "
                    "Best for: checking if a Polymarket whale recently received large "
                    "USDC deposits (funding for trades), tracking wallet activity."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": "Ethereum wallet address (0x...)",
                        },
                        "token": {
                            "type": "string",
                            "description": "Token to track: USDC, USDT, WETH, or DAI (default: USDC)",
                        },
                    },
                    "required": ["address"],
                },
                execute=lambda address, token="USDC": etherscan.get_wallet_token_transfers(address, token),
            ))

            self._register(Tool(
                name="get_contract_info",
                description=(
                    "Check if an Ethereum address is a smart contract, when it was created, "
                    "its name and verification status from Etherscan. "
                    "Best for: verifying if a crypto project has deployed contracts, "
                    "checking contract activity for token launch markets."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": "Ethereum contract address (0x...)",
                        },
                    },
                    "required": ["address"],
                },
                execute=lambda address: etherscan.get_contract_info(address),
            ))

    def _register(self, tool: Tool):
        self._tools[tool.name] = tool
        logger.info(f"Registered tool: {tool.name}")

    @property
    def available_tools(self) -> List[str]:
        return list(self._tools.keys())

    def openai_tool_schemas(self) -> List[dict]:
        """Return tool schemas in OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def call(self, name: str, **kwargs) -> str:
        """
        Execute a tool by name.

        Returns the tool's string result, or an error message if the tool
        is not found or execution fails.
        """
        tool = self._tools.get(name)
        if not tool:
            msg = f"Tool '{name}' not found. Available: {self.available_tools}"
            logger.error(msg)
            return msg

        try:
            result = tool.execute(**kwargs)
            logger.info(f"Tool {name} executed successfully ({len(result)} chars)")
            return result
        except Exception as e:
            msg = f"Tool '{name}' failed: {e}"
            logger.error(msg)
            return msg
