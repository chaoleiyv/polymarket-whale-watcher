"""Anomaly detection service - filters and validates whale trades."""
import logging
from typing import List, Optional
from datetime import datetime, timedelta

from src.config import get_settings
from src.models.trade import WhaleTrade, TradeActivity

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Detects anomalous (whale) trades based on configurable criteria.

    Criteria:
    - Trade size >= MIN_TRADE_SIZE_USD (default: $10,000)
    - Trade price between MIN_PRICE and MAX_PRICE (default: 0.2-0.8)
    """

    def __init__(self):
        self.settings = get_settings()

    def is_anomalous_trade(self, activity: TradeActivity) -> bool:
        """
        Check if a trade is anomalous based on size and price.

        Args:
            activity: The trade activity to check

        Returns:
            True if the trade is anomalous
        """
        # Check trade size
        if activity.usdc_size < self.settings.min_trade_size_usd:
            return False

        # Check price range (0.2-0.8 means not too certain either way)
        if not (self.settings.min_price <= activity.price <= self.settings.max_price):
            return False

        return True

    def get_anomaly_score(self, activity: TradeActivity) -> float:
        """
        Calculate an anomaly score for a trade.

        Higher score = more interesting anomaly.

        Args:
            activity: The trade activity to score

        Returns:
            Anomaly score between 0 and 1
        """
        if not self.is_anomalous_trade(activity):
            return 0.0

        score = 0.0

        # Size component (bigger trades = higher score)
        # $10k = 0.3, $50k = 0.5, $100k+ = 0.6
        size_score = min(0.6, 0.3 + (activity.usdc_size - 10000) / 200000)
        score += size_score

        # Price component (closer to 0.5 = more uncertain = higher score)
        # Price at 0.5 = 0.4, price at 0.2 or 0.8 = 0.2
        price_distance_from_50 = abs(activity.price - 0.5)
        price_score = 0.4 * (1 - price_distance_from_50 / 0.3)
        score += max(0, price_score)

        return min(1.0, score)

    def filter_whale_trades(
        self,
        trades: List[WhaleTrade],
        min_score: float = 0.5,
    ) -> List[WhaleTrade]:
        """
        Filter whale trades by anomaly score.

        Args:
            trades: List of whale trades to filter
            min_score: Minimum anomaly score to include

        Returns:
            Filtered list of whale trades
        """
        filtered = []
        for trade in trades:
            score = self.get_anomaly_score(trade.trade)
            if score >= min_score:
                filtered.append(trade)
                logger.debug(
                    f"Trade passed filter: ${trade.trade.usdc_size:,.2f} "
                    f"@ {trade.trade.price:.4f} (score: {score:.2f})"
                )

        return filtered

    def analyze_trade_context(self, whale_trade: WhaleTrade) -> dict:
        """
        Analyze the context of a whale trade for LLM input.

        Args:
            whale_trade: The whale trade to analyze

        Returns:
            Dictionary with analysis context
        """
        trade = whale_trade.trade

        # Determine trade direction interpretation
        if trade.side == "BUY":
            direction_meaning = f"The trader is betting FOR '{trade.outcome}' occurring"
        else:
            direction_meaning = f"The trader is betting AGAINST '{trade.outcome}' occurring"

        # Calculate implied probability from price
        implied_prob = trade.price if trade.side == "BUY" else (1 - trade.price)

        # Assess market state from outcome prices
        market_state = "uncertain"
        if whale_trade.market_outcome_prices:
            max_price = max(whale_trade.market_outcome_prices)
            if max_price > 0.7:
                market_state = "leaning towards one outcome"
            elif max_price < 0.6:
                market_state = "highly uncertain"

        # Calculate conviction level based on size
        conviction = "moderate"
        if trade.usdc_size >= 50000:
            conviction = "very high"
        elif trade.usdc_size >= 25000:
            conviction = "high"

        return {
            "trade_size_usd": trade.usdc_size,
            "trade_side": trade.side,
            "trade_price": trade.price,
            "trade_outcome": trade.outcome,
            "direction_meaning": direction_meaning,
            "implied_probability": implied_prob,
            "market_state": market_state,
            "conviction_level": conviction,
            "anomaly_score": self.get_anomaly_score(trade),
            "market_question": whale_trade.market_question,
            "market_outcomes": whale_trade.market_outcomes,
            "current_prices": whale_trade.market_outcome_prices,
        }

    def format_for_llm(self, whale_trade: WhaleTrade) -> str:
        """
        Format whale trade data for LLM analysis.

        Args:
            whale_trade: The whale trade to format

        Returns:
            Formatted string for LLM input
        """
        context = self.analyze_trade_context(whale_trade)
        trade = whale_trade.trade

        # Build outcome prices string
        prices_str = ""
        for i, (outcome, price) in enumerate(
            zip(context["market_outcomes"], context["current_prices"])
        ):
            prices_str += f"  - {outcome}: {price:.2%}\n"

        # Build trader ranking info
        ranking_str = ""
        if whale_trade.trader_ranking:
            rank = whale_trade.trader_ranking
            rank_display = f"#{rank.rank}" if rank.rank else "未上榜"
            pnl_display = f"${rank.pnl:,.2f}" if rank.pnl else "N/A"
            vol_display = f"${rank.volume:,.2f}" if rank.volume else "N/A"
            verified_display = "✅ 已认证" if rank.verified else "未认证"
            ranking_str = f"""
### 交易者排名信息（盈利排行榜）
- **排名**: {rank_display} (时间范围: {rank.time_period})
- **累计盈亏 (PnL)**: {pnl_display}
- **总交易量**: {vol_display}
- **用户名**: {rank.user_name or 'Anonymous'}
- **认证状态**: {verified_display}
"""
        else:
            ranking_str = """
### 交易者排名信息
- 该交易者不在盈利排行榜上（可能是新用户或小额交易者）
"""

        # Build trader history info
        history_str = ""
        if whale_trade.trader_history:
            hist = whale_trade.trader_history
            history_str = f"""
### 交易者历史交易记录（重要！）
- **近期交易总数**: {hist.total_trades} 笔
- **近期交易总额**: ${hist.total_volume:,.2f} USDC
- **平均交易金额**: ${hist.avg_trade_size:,.2f} USDC
- **大额交易次数** (≥$5000): {hist.large_trades_count} 笔
- **活跃市场**: {', '.join(hist.recent_markets[:5]) if hist.recent_markets else 'N/A'}
"""
            # Add recent large trades details
            if hist.recent_trades:
                history_str += "\n**近期大额交易明细**:\n"
                for i, t in enumerate(hist.recent_trades[:5], 1):
                    title = t.get('title', 'N/A')
                    if len(title) > 40:
                        title = title[:40] + "..."
                    history_str += f"  {i}. {t.get('side', 'N/A')} ${t.get('usdc_size', 0):,.2f} @ {t.get('price', 0):.4f} - {title}\n"
        else:
            history_str = """
### 交易者历史交易记录
- 无法获取该交易者的历史交易记录
"""

        return f"""
## 大额交易异常检测报告

### 交易详情
- **交易金额**: ${context['trade_size_usd']:,.2f} USDC
- **交易方向**: {context['trade_side']}
- **交易价格**: {context['trade_price']:.4f} ({context['trade_price']:.2%})
- **交易结果**: {context['trade_outcome']}
- **交易时间**: {datetime.fromtimestamp(trade.timestamp).strftime('%Y-%m-%d %H:%M:%S')}
- **交易者钱包**: {trade.proxy_wallet or 'Unknown'}
- **异常评分**: {context['anomaly_score']:.2f}/1.00

### 交易解读
- **方向含义**: {context['direction_meaning']}
- **隐含概率**: 交易者认为结果发生的概率约为 {context['implied_probability']:.2%}
- **信心程度**: {context['conviction_level']}
{ranking_str}{history_str}
### 市场状态
- **市场问题**: {context['market_question']}
- **市场状态**: {context['market_state']}
- **当前赔率**:
{prices_str}

### 分析要点
1. 这是一笔 ${context['trade_size_usd']:,.2f} 的大额交易，表明交易者有{context['conviction_level']}的信心
2. 交易价格 {context['trade_price']:.4f} 说明市场尚未形成明确共识
3. {context['direction_meaning']}
4. **请重点分析交易者的排名和历史交易记录，判断其专业性和可信度**
5. 这可能暗示交易者掌握了某些市场尚未充分反映的信息

请分析这笔交易并给出你的交易建议。
"""
