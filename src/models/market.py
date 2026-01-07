"""Market data models."""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class Market(BaseModel):
    """Polymarket market data model."""

    id: str
    question: str
    condition_id: Optional[str] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    end_date: Optional[str] = None
    outcomes: List[str] = Field(default_factory=list)
    outcome_prices: List[float] = Field(default_factory=list)
    clob_token_ids: List[str] = Field(default_factory=list)
    volume: float = 0.0
    volume_24hr: float = 0.0
    liquidity: float = 0.0
    active: bool = True
    closed: bool = False
    neg_risk: bool = False


class TrendingMarket(BaseModel):
    """Trending market with additional metrics."""

    market: Market
    volume_24hr: float = 0.0
    liquidity: float = 0.0
    rank: int = 0
    fetched_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def is_valid_for_monitoring(self) -> bool:
        """Check if market is valid for whale monitoring."""
        return (
            self.market.active
            and not self.market.closed
            and len(self.market.clob_token_ids) > 0
        )
