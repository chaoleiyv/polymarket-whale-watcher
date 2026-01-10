"""Anomaly signal models for storing historical anomalous trades."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from src.models.trade import TraderRanking, TraderHistory


class AnomalySignal(BaseModel):
    """
    Represents a stored anomaly signal for a market.

    This captures the raw trade and trader information for trades with medium
    or higher insider trading likelihood. The insider_trading_likelihood is stored
    for sorting/filtering purposes, but NOT shown to LLM - the model will
    re-analyze all signals (historical + current) together without bias.
    """

    # Unique identifier
    id: str = Field(default_factory=lambda: "")

    # Market identification
    market_id: str
    market_question: str
    market_slug: Optional[str] = None

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

    # Insider trading likelihood (for sorting/filtering only, NOT shown to LLM)
    insider_trading_likelihood: float = Field(default=0.0, ge=0.0, le=1.0)

    # Metadata
    detected_at: datetime = Field(default_factory=datetime.utcnow)

    def to_context_string(self) -> str:
        """
        Format this anomaly signal as a context string for LLM.

        Returns:
            Formatted string describing this historical anomaly signal.
        """
        trade_time = datetime.fromtimestamp(self.trade_timestamp).strftime('%Y-%m-%d %H:%M:%S')

        # Trader ranking info
        trader_rank_str = "未上榜"
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
- 近期交易数: {self.trader_history.total_trades} 笔
- 交易总额: ${self.trader_history.total_volume:,.2f}
- 大额交易数: {self.trader_history.large_trades_count} 笔"""

        return f"""**交易时间**: {trade_time}
**交易方向**: {self.trade_side}
**交易金额**: ${self.trade_size_usd:,.2f} USDC
**交易价格**: {self.trade_price:.4f}
**交易结果**: {self.trade_outcome}
**交易者钱包**: {self.trader_wallet or 'Unknown'}
**交易者排名**: {trader_rank_str} (PnL: {trader_pnl_str}, 交易量: {trader_vol_str}){trader_history_str}"""
