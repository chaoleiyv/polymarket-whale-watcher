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


class EventPosition(BaseModel):
    """Whale's position in a related market under the same event."""

    market_question: str
    condition_id: str = ""
    outcome: str = ""  # "Yes" or "No"
    size: float = 0.0  # token size held
    avg_price: float = 0.0  # average entry price
    current_price: float = 0.0  # current market price
    current_value: float = 0.0  # current position value in USD
    initial_value: float = 0.0  # cost basis
    pnl: float = 0.0  # realized + unrealized PnL
    side_summary: str = ""  # human readable summary


class MarketTopTrader(BaseModel):
    """Top trader on a market (by net volume)."""

    wallet: str
    name: Optional[str] = None
    rank: Optional[int] = None
    pnl: Optional[float] = None
    net_volume_usd: float = 0.0  # positive = net buyer of Yes, negative = net seller
    trade_count: int = 0


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
    # Whale's positions across the same event
    whale_event_positions: list[EventPosition] = Field(default_factory=list)
    # Top traders on this market (bulls and bears)
    market_top_buyers: list[MarketTopTrader] = Field(default_factory=list)
    market_top_sellers: list[MarketTopTrader] = Field(default_factory=list)

    @property
    def is_whale_trade(self) -> bool:
        """Check if this qualifies as a whale trade."""
        return self.trade.usdc_size >= 10000

    @property
    def is_valid_price_range(self) -> bool:
        """Check if trade price is in valid range (0.2-0.8)."""
        return 0.2 <= self.trade.price <= 0.8

    def format_event_positions(self) -> str:
        """Format whale's event positions for LLM context."""
        if self.whale_event_positions:
            info = "### Whale's Positions in Other Markets Under the Same Event\n"
            info += "(Used to identify hedging or correlated bets)\n\n"
            for pos in self.whale_event_positions:
                pnl_str = f"PnL ${pos.pnl:+,.0f}" if pos.pnl else ""
                info += (
                    f"- **{pos.market_question[:60]}{'...' if len(pos.market_question) > 60 else ''}**\n"
                    f"  {pos.side_summary} | "
                    f"Current Value ${pos.current_value:,.0f} | Cost Basis ${pos.initial_value:,.0f} | "
                    f"{pnl_str}\n"
                )
            return info
        return "### Whale's Positions in Other Markets Under the Same Event\n- No other related positions (single-market event or no cross-market trades)\n"

    def format_top_traders(self) -> str:
        """Format market top holders for LLM context."""
        info = "### Top 5 Bulls and Bears on This Market\n"
        info += "(Reflects the stance and credentials of major participants)\n"

        if self.market_top_buyers:
            info += "\n**Bulls (Holding Yes Token)**:\n"
            for i, t in enumerate(self.market_top_buyers, 1):
                rank_str = f"Rank #{t.rank}" if t.rank else "Unranked"
                pnl_str = f"PnL ${t.pnl:,.0f}" if t.pnl is not None else ""
                name_str = t.name or t.wallet[:10] + "..."
                info += (
                    f"  {i}. **{name_str}** ({rank_str}{', ' + pnl_str if pnl_str else ''}) "
                    f"— Position Value ${t.net_volume_usd:,.0f}\n"
                )
        else:
            info += "\n**Bulls**: No significant positions\n"

        if self.market_top_sellers:
            info += "\n**Bears (Holding No Token)**:\n"
            for i, t in enumerate(self.market_top_sellers, 1):
                rank_str = f"Rank #{t.rank}" if t.rank else "Unranked"
                pnl_str = f"PnL ${t.pnl:,.0f}" if t.pnl is not None else ""
                name_str = t.name or t.wallet[:10] + "..."
                info += (
                    f"  {i}. **{name_str}** ({rank_str}{', ' + pnl_str if pnl_str else ''}) "
                    f"— Position Value ${t.net_volume_usd:,.0f}\n"
                )
        else:
            info += "\n**Bears**: No significant positions\n"

        return info

    def to_llm_context(self) -> str:
        """Generate context string for LLM analysis."""
        # Format trader ranking info
        trader_info = ""
        if self.trader_ranking:
            rank_str = f"#{self.trader_ranking.rank}" if self.trader_ranking.rank else "Unranked"
            pnl_str = f"${self.trader_ranking.pnl:,.2f}" if self.trader_ranking.pnl else "N/A"
            vol_str = f"${self.trader_ranking.volume:,.2f}" if self.trader_ranking.volume else "N/A"
            verified_str = "Verified" if self.trader_ranking.verified else "Unverified"
            trader_info = f"""
### Trader Ranking (PnL Leaderboard)
- **Rank**: {rank_str} (Period: {self.trader_ranking.time_period})
- **Cumulative PnL**: {pnl_str}
- **Volume**: {vol_str}
- **Username**: {self.trader_ranking.user_name or 'Anonymous'}
- **Verification**: {verified_str}
"""
        else:
            trader_info = """
### Trader Ranking
- This trader is not on the PnL leaderboard (possibly a new user or small trader)
"""

        # Format trader history info
        history_info = ""
        if self.trader_history:
            history_info = f"""
### Trader History
- **Recent Trades**: {self.trader_history.total_trades}
- **Recent Volume**: ${self.trader_history.total_volume:,.2f} USDC
- **Avg Trade Size**: ${self.trader_history.avg_trade_size:,.2f} USDC
- **Large Trades** (>=$5000): {self.trader_history.large_trades_count}
- **Active Markets**: {', '.join(self.trader_history.recent_markets[:5]) if self.trader_history.recent_markets else 'N/A'}
"""
            # Add recent large trades details
            if self.trader_history.recent_trades:
                history_info += "\n**Recent Large Trade Details**:\n"
                for i, t in enumerate(self.trader_history.recent_trades[:5], 1):
                    history_info += f"  {i}. {t.get('side', 'N/A')} ${t.get('usdc_size', 0):,.2f} @ {t.get('price', 0):.4f} - {t.get('title', 'N/A')[:40]}...\n"
        else:
            history_info = """
### Trader History
- Unable to retrieve this trader's trading history
"""

        return f"""
## Anomalous Trade Detection

### Trade Information
- Direction: BUY {self.trade.outcome} Token ({'Bullish — expects the event to occur' if self.trade.outcome == 'Yes' else 'Bearish — expects the event will not occur'})
- Trade Size: ${self.trade.usdc_size:,.2f} USDC
- Entry Price: {self.trade.price:.4f} (Odds ~{1/self.trade.price:.1f}x)
- Trade Time: {datetime.fromtimestamp(self.trade.timestamp).strftime('%Y-%m-%d %H:%M:%S')}
- Trader Wallet: {self.trade.proxy_wallet or 'Unknown'}
{trader_info}{history_info}
{self.format_event_positions()}

{self.format_top_traders()}

### Market Information
- Market Question: {self.market_question}
- Market Description: {self.market_description or 'N/A'}
- Possible Outcomes: {', '.join(self.market_outcomes)}
- Current Prices: {', '.join([f'{o}: {p:.4f}' for o, p in zip(self.market_outcomes, self.market_outcome_prices)])}

### Key Analysis Points
1. This large trade (${self.trade.usdc_size:,.2f}) is **BUY {self.trade.outcome} Token**, {'indicating the trader is Bullish and expects the event to occur' if self.trade.outcome == 'Yes' else 'indicating the trader is Bearish and expects the event will not occur'}
2. Entry price {self.trade.price:.4f}, odds ~{1/self.trade.price:.1f}x
3. **Trader ranking and history are key references for assessing information asymmetry credibility**
4. **Review the whale's other positions under the same event** — opposite positions may indicate a hedging strategy
5. **Check the top bulls and bears on this market** — which side has the elite traders
"""
