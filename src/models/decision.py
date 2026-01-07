"""LLM decision models."""
from datetime import datetime
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field


class TradeAction(str, Enum):
    """Recommended trade action."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"  # Do not trade


class TraderCredibility(str, Enum):
    """Trader credibility level based on leaderboard ranking."""

    HIGH = "HIGH"  # Top 100
    MEDIUM = "MEDIUM"  # 100-500
    LOW = "LOW"  # 500+
    UNKNOWN = "UNKNOWN"  # Not on leaderboard


class TradeRecommendation(BaseModel):
    """Trade recommendation from LLM."""

    action: TradeAction
    outcome: str  # Which outcome to trade
    confidence: float = Field(ge=0.0, le=1.0)  # 0-1 confidence score
    suggested_price: Optional[float] = None
    suggested_size_percent: float = Field(default=0.1, ge=0.0, le=1.0)  # % of balance
    reasoning: str = ""

    # Insider trading assessment fields
    insider_trading_likelihood: float = Field(default=0.0, ge=0.0, le=1.0)  # 0-1 likelihood
    trader_credibility: TraderCredibility = TraderCredibility.UNKNOWN
    insider_evidence: str = ""  # Evidence supporting insider trading assessment


class LLMDecision(BaseModel):
    """Complete LLM decision for a whale trade."""

    whale_trade_id: str
    market_id: str
    analysis: str  # Full LLM analysis text
    recommendation: TradeRecommendation
    created_at: datetime = Field(default_factory=datetime.utcnow)
    executed: bool = False
    execution_result: Optional[str] = None

    @property
    def should_trade(self) -> bool:
        """Check if we should execute this trade."""
        return (
            self.recommendation.action != TradeAction.HOLD
            and self.recommendation.confidence >= 0.6
        )
