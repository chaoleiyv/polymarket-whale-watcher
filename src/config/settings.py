"""Application settings and configuration."""
import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # LLM API (OpenAI-compatible proxy)
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    llm_base_url: str = Field(default="http://apicz.boyuerichdata.com/v1/", alias="LLM_BASE_URL")

    # Trade data API mode: "internal" (private API) or "official" (Polymarket data-api)
    trade_api_mode: str = Field(default="official", alias="TRADE_API_MODE")

    # Internal trade data API (only used when TRADE_API_MODE=internal)
    internal_api_url: str = Field(default="http://103.197.25.170:18088", alias="INTERNAL_API_URL")
    internal_api_key: str = Field(default="", alias="INTERNAL_API_KEY")

    # Twitter API (for social sentiment search)
    twitter_api_key: str = Field(default="", alias="TWITTER_API_KEY")

    # Tavily API (for web search, replaces Google Search)
    tavily_api_key: str = Field(default="", alias="TAVILY_API_KEY")

    # Serper API (web search fallback)
    serper_api_key: str = Field(default="", alias="SERPER_API_KEY")

    # FRED API (macroeconomic data)
    fred_api_key: str = Field(default="", alias="FRED_API_KEY")

    # Polygon.io API (stocks, forex, commodities)
    polygon_api_key: str = Field(default="", alias="POLYGON_API_KEY")

    # Congress.gov API (U.S. legislation)
    congress_api_key: str = Field(default="", alias="CONGRESS_API_KEY")

    # Etherscan API (on-chain data)
    etherscan_api_key: str = Field(default="", alias="ETHERSCAN_API_KEY")

    # Telegram API (crypto channel monitoring)
    telegram_api_id: str = Field(default="", alias="TELEGRAM_API_ID")
    telegram_api_hash: str = Field(default="", alias="TELEGRAM_API_HASH")
    telegram_session_string: str = Field(default="", alias="TELEGRAM_SESSION_STRING")
    telegram_channels: str = Field(default="", alias="TELEGRAM_CHANNELS")

    # Polygon Wallet
    polygon_wallet_private_key: str = Field(default="", alias="POLYGON_WALLET_PRIVATE_KEY")

    # MongoDB
    mongodb_uri: str = Field(default="mongodb://localhost:27017/whale_watcher", alias="MONGODB_URI")

    # SQLite database
    db_path: str = Field(default="data/signals.db", alias="DB_PATH")

    # Whale Detection Settings
    min_trade_size_usd: float = Field(default=1000.0, alias="MIN_TRADE_SIZE_USD")
    min_price: float = Field(default=0.2, alias="MIN_PRICE")
    max_price: float = Field(default=0.8, alias="MAX_PRICE")

    # Monitoring Settings
    fetch_interval_seconds: int = Field(default=15, alias="FETCH_INTERVAL_SECONDS")
    trending_markets_limit: int = Field(default=50, alias="TRENDING_MARKETS_LIMIT")

    # LLM Settings
    llm_model: str = Field(default="gemini-3-flash-preview", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")

    # Trade Execution
    enable_trade_execution: bool = Field(default=False, alias="ENABLE_TRADE_EXECUTION")

    # Email notification
    email_smtp_server: str = Field(default="smtp.qq.com", alias="EMAIL_SMTP_SERVER")
    email_smtp_port: int = Field(default=465, alias="EMAIL_SMTP_PORT")
    email_sender: str = Field(default="", alias="EMAIL_SENDER")
    email_password: str = Field(default="", alias="EMAIL_PASSWORD")
    email_recipient: str = Field(default="1253608463@qq.com,lyk@sii.edu.cn,1286874010@qq.com,tianhao.alex.huang@gmail.com", alias="EMAIL_RECIPIENT")
    email_enabled: bool = Field(default=False, alias="EMAIL_ENABLED")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    load_dotenv()
    return Settings()
