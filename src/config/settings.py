"""Application settings and configuration."""
import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Gemini API
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")

    # Polygon Wallet
    polygon_wallet_private_key: str = Field(default="", alias="POLYGON_WALLET_PRIVATE_KEY")

    # MongoDB
    mongodb_uri: str = Field(default="mongodb://localhost:27017/whale_watcher", alias="MONGODB_URI")

    # Whale Detection Settings
    min_trade_size_usd: float = Field(default=1000.0, alias="MIN_TRADE_SIZE_USD")
    min_price: float = Field(default=0.2, alias="MIN_PRICE")
    max_price: float = Field(default=0.8, alias="MAX_PRICE")

    # Monitoring Settings
    fetch_interval_seconds: int = Field(default=5, alias="FETCH_INTERVAL_SECONDS")
    trending_markets_limit: int = Field(default=50, alias="TRENDING_MARKETS_LIMIT")

    # LLM Settings (Gemini)
    llm_model: str = Field(default="gemini-3-pro-preview", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")

    # Trade Execution
    enable_trade_execution: bool = Field(default=False, alias="ENABLE_TRADE_EXECUTION")

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
