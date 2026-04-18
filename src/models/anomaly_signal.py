"""Anomaly signal models for storing historical anomalous trades."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.models.trade import TraderRanking, TraderHistory


class AnomalySignal(BaseModel):
    """
    Represents a stored anomaly signal for a market.

    This captures the raw trade and trader information for trades with medium
    or higher information asymmetry score. The information_asymmetry_score is stored
    for sorting/filtering purposes, but NOT shown to LLM - the model will
    re-analyze all signals (historical + current) together without bias.
    """

    # Unique identifier
    id: str = Field(default_factory=lambda: "")

    # Market identification
    market_id: str
    market_question: str
    market_slug: Optional[str] = None
    condition_id: Optional[str] = None

    # Trade information
    transaction_hash: str
    trade_timestamp: int  # Unix timestamp of the trade
    trade_side: str  # BUY or SELL
    trade_price: float
    trade_size_usd: float
    trade_outcome: str

    # Trader information
    trader_wallet: Optional[str] = None
    trader_ranking: Optional[TraderRanking] = None
    trader_history: Optional[TraderHistory] = None

    # Information asymmetry score (for sorting/filtering only, NOT shown to LLM)
    information_asymmetry_score: float = Field(default=0.0, ge=0.0, le=1.0)

    # LLM analysis results
    reasoning: str = ""
    insider_evidence: str = ""

    # Metadata
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    # Resolution tracking
    market_resolved: bool = False
    market_resolved_at: Optional[datetime] = None
    resolved_outcome: Optional[str] = None
    signal_correct: Optional[bool] = None
    theoretical_roi: Optional[float] = None

    def to_context_string(self) -> str:
        """
        Format this anomaly signal as a context string for LLM.

        Returns:
            Formatted string describing this historical anomaly signal.
        """
        trade_time = datetime.fromtimestamp(self.trade_timestamp).strftime('%Y-%m-%d %H:%M:%S')

        # Trader ranking info
        trader_rank_str = "Unranked"
        trader_pnl_str = "N/A"
        trader_vol_str = "N/A"
        if self.trader_ranking:
            if self.trader_ranking.rank:
                trader_rank_str = f"#{self.trader_ranking.rank}"
            if self.trader_ranking.pnl is not None:
                trader_pnl_str = f"${self.trader_ranking.pnl:,.2f}"
            if self.trader_ranking.volume is not None:
                trader_vol_str = f"${self.trader_ranking.volume:,.2f}"

        # Trader history info
        trader_history_str = ""
        if self.trader_history:
            trader_history_str = f"""
- Recent Trades: {self.trader_history.total_trades}
- Total Volume: ${self.trader_history.total_volume:,.2f}
- Large Trades: {self.trader_history.large_trades_count}"""

        return f"""**Trade Time**: {trade_time}
**Direction**: {self.trade_side}
**Trade Size**: ${self.trade_size_usd:,.2f} USDC
**Trade Price**: {self.trade_price:.4f}
**Outcome**: {self.trade_outcome}
**Trader Wallet**: {self.trader_wallet or 'Unknown'}
**Trader Rank**: {trader_rank_str} (PnL: {trader_pnl_str}, Volume: {trader_vol_str}){trader_history_str}"""
