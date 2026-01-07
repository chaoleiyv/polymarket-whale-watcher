"""Trade data models."""
from datetime import datetime
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field


class TradeSide(str, Enum):
    """Trade side enum."""

    BUY = "BUY"
    SELL = "SELL"


class TradeActivity(BaseModel):
    """Raw trade activity from Polymarket API."""

    transaction_hash: str
    timestamp: int
    condition_id: str
    asset: str
    side: str
    size: float  # Token size
    usdc_size: float  # USD value
    price: float
    outcome: str
    outcome_index: int
    title: str
    slug: Optional[str] = None
    event_slug: Optional[str] = None
    proxy_wallet: Optional[str] = None
    name: Optional[str] = None


class TraderRanking(BaseModel):
    """Trader ranking information from leaderboard."""

    rank: Optional[int] = None  # Position on leaderboard (None if not ranked)
    pnl: Optional[float] = None  # Profit/Loss
    volume: Optional[float] = None  # Trading volume
    user_name: Optional[str] = None  # Display name
    profile_image: Optional[str] = None  # Avatar URL
    verified: bool = False  # Verified badge
    time_period: str = "ALL"  # Time period for ranking


class TraderHistory(BaseModel):
    """Trader's recent trading history summary."""

    total_trades: int = 0  # Total number of recent trades
    total_volume: float = 0.0  # Total trading volume in USDC
    avg_trade_size: float = 0.0  # Average trade size
    win_rate: Optional[float] = None  # Win rate if calculable
    recent_markets: list[str] = Field(default_factory=list)  # Recent markets traded
    large_trades_count: int = 0  # Number of trades >= $5000
    recent_trades: list[dict] = Field(default_factory=list)  # Recent trade details


class WhaleTrade(BaseModel):
    """Whale trade that meets detection criteria."""

    id: str = Field(default_factory=lambda: "")
    trade: TradeActivity
    market_id: str
    market_question: str
    market_description: Optional[str] = None
    market_outcomes: list[str] = Field(default_factory=list)
    market_outcome_prices: list[float] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    processed: bool = False
    llm_analyzed: bool = False

    # Trader ranking info
    trader_ranking: Optional[TraderRanking] = None
    # Trader history info
    trader_history: Optional[TraderHistory] = None

    @property
    def is_whale_trade(self) -> bool:
        """Check if this qualifies as a whale trade."""
        return self.trade.usdc_size >= 10000

    @property
    def is_valid_price_range(self) -> bool:
        """Check if trade price is in valid range (0.2-0.8)."""
        return 0.2 <= self.trade.price <= 0.8

    def to_llm_context(self) -> str:
        """Generate context string for LLM analysis."""
        # Format trader ranking info
        trader_info = ""
        if self.trader_ranking:
            rank_str = f"#{self.trader_ranking.rank}" if self.trader_ranking.rank else "未上榜"
            pnl_str = f"${self.trader_ranking.pnl:,.2f}" if self.trader_ranking.pnl else "N/A"
            vol_str = f"${self.trader_ranking.volume:,.2f}" if self.trader_ranking.volume else "N/A"
            verified_str = "✅ 已认证" if self.trader_ranking.verified else "未认证"
            trader_info = f"""
### 交易者排名信息 (盈利排行榜)
- **排名**: {rank_str} (时间范围: {self.trader_ranking.time_period})
- **累计盈亏 (PnL)**: {pnl_str}
- **交易量**: {vol_str}
- **用户名**: {self.trader_ranking.user_name or 'Anonymous'}
- **认证状态**: {verified_str}
"""
        else:
            trader_info = """
### 交易者排名信息
- 该交易者不在盈利排行榜上（可能是新用户或小额交易者）
"""

        # Format trader history info
        history_info = ""
        if self.trader_history:
            history_info = f"""
### 交易者历史交易记录
- **近期交易总数**: {self.trader_history.total_trades} 笔
- **近期交易总额**: ${self.trader_history.total_volume:,.2f} USDC
- **平均交易金额**: ${self.trader_history.avg_trade_size:,.2f} USDC
- **大额交易次数** (≥$5000): {self.trader_history.large_trades_count} 笔
- **活跃市场**: {', '.join(self.trader_history.recent_markets[:5]) if self.trader_history.recent_markets else 'N/A'}
"""
            # Add recent large trades details
            if self.trader_history.recent_trades:
                history_info += "\n**近期大额交易明细**:\n"
                for i, t in enumerate(self.trader_history.recent_trades[:5], 1):
                    history_info += f"  {i}. {t.get('side', 'N/A')} ${t.get('usdc_size', 0):,.2f} @ {t.get('price', 0):.4f} - {t.get('title', 'N/A')[:40]}...\n"
        else:
            history_info = """
### 交易者历史交易记录
- 无法获取该交易者的历史交易记录
"""

        return f"""
## 异常交易检测

### 交易信息
- 交易金额: ${self.trade.usdc_size:,.2f} USDC
- 交易方向: {self.trade.side}
- 交易价格: {self.trade.price:.4f}
- 交易结果: {self.trade.outcome}
- 交易时间: {datetime.fromtimestamp(self.trade.timestamp).strftime('%Y-%m-%d %H:%M:%S')}
- 交易者钱包: {self.trade.proxy_wallet or 'Unknown'}
{trader_info}{history_info}
### 市场信息
- 市场问题: {self.market_question}
- 市场描述: {self.market_description or 'N/A'}
- 可能结果: {', '.join(self.market_outcomes)}
- 当前价格: {', '.join([f'{o}: {p:.4f}' for o, p in zip(self.market_outcomes, self.market_outcome_prices)])}

### 分析要点
1. 这笔大额交易 (${self.trade.usdc_size:,.2f}) 表明交易者对 "{self.trade.outcome}" 结果有很强的信心
2. 交易价格 {self.trade.price:.4f} 说明市场尚未形成明确共识
3. 交易方向为 {self.trade.side}，可能暗示内部信息或深度分析结论
4. **交易者排名和历史交易是判断内幕交易可信度的重要参考** - 高排名、大额交易频繁的交易者通常有更好的信息来源或分析能力
"""
