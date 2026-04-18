"""Anomaly detection service - multi-dimensional scoring for whale trades."""
import logging
import time
from collections import defaultdict, deque
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

from src.config import get_settings
from src.models.trade import WhaleTrade, TradeActivity, TraderHistory
from src.models.market import Market
from src.services.trader_profiler import TraderProfiler

logger = logging.getLogger(__name__)


class AnomalyDetector:
    """
    Multi-dimensional anomaly detection for whale trades.

    Scoring dimensions:
    1. Size relative to market (trade vs market 24h volume)
    2. Price uncertainty (closer to 0.5 = more uncertain = more interesting)
    3. Time-of-day (off-peak hours = more suspicious)
    4. Trader deviation (trade size vs trader's historical average)
    5. Cluster signal (multiple same-direction trades in short window)
    """

    # --- Time-of-day weights (US Eastern Time) ---
    # Polymarket is US-dominated, so we use ET to judge trading hour anomaly.
    # Higher weight = more unusual trading hour = more suspicious.
    _ET_HOUR_WEIGHTS = {
        # ET 0-5 (midnight to 5am) — very unusual, most suspicious
        0: 0.6, 1: 0.7, 2: 0.8, 3: 0.9, 4: 0.8, 5: 0.6,
        # ET 6-8 (early morning) — some early traders
        6: 0.4, 7: 0.3, 8: 0.2,
        # ET 9-17 (US business hours) — peak activity, least suspicious
        9: 0.1, 10: 0.0, 11: 0.0, 12: 0.0, 13: 0.0,
        14: 0.0, 15: 0.0, 16: 0.0, 17: 0.1,
        # ET 18-20 (evening) — moderate
        18: 0.2, 19: 0.2, 20: 0.3,
        # ET 21-23 (late night) — unusual
        21: 0.4, 22: 0.5, 23: 0.5,
    }
    # UTC offset for US Eastern: -5 (EST) or -4 (EDT).
    # Use -4 as default (EDT covers ~Mar-Nov, most of the year).
    _ET_UTC_OFFSET = -4

    # Cluster detection: track recent trades per market
    # Key: market_id, Value: deque of (timestamp, side, usdc_size)
    _CLUSTER_WINDOW_SECONDS = 300  # 5 minutes
    _CLUSTER_MIN_COUNT = 3  # minimum trades for cluster signal

    def __init__(self):
        self.settings = get_settings()
        self.trader_profiler = TraderProfiler()
        # Recent trades for cluster detection: market_id -> deque
        self._recent_trades: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )

    # ================================================================
    # Core scoring
    # ================================================================

    def get_anomaly_score(
        self,
        activity: TradeActivity,
        market: Optional[Market] = None,
        trader_history: Optional[TraderHistory] = None,
        market_id: str = "",
    ) -> Tuple[float, dict]:
        """
        Calculate multi-dimensional anomaly score.

        Returns:
            (total_score, breakdown_dict) where breakdown has per-dimension scores.
        """
        breakdown = {}

        # --- 1. Size score (absolute) ---
        # $5k=0.1, $20k=0.25, $50k=0.4, $100k+=0.5
        raw_size = min(0.5, 0.1 + (activity.usdc_size - 5000) / 250000)
        breakdown["size_abs"] = max(0.0, raw_size)

        # --- 2. Size relative to market volume ---
        if market and market.volume_24hr > 0:
            # What fraction of 24h volume is this single trade?
            ratio = activity.usdc_size / market.volume_24hr
            # ratio 0.001=noise, 0.01=notable, 0.05=significant, 0.1+=massive
            rel_score = min(0.3, ratio * 6.0)  # 0.05 ratio -> 0.3
            breakdown["size_relative"] = rel_score
        else:
            breakdown["size_relative"] = 0.15  # unknown market volume, use neutral

        # --- 3. Price uncertainty ---
        # Price is taker's buy price (no normalization).
        # Lower price = more uncertain/risky bet = more interesting.
        # 0.5 -> 0.2, 0.3/0.7 -> 0.1, 0.1/0.9 -> 0.0
        dist = abs(activity.price - 0.5)
        if dist <= 0.3:
            price_score = 0.2 * (1 - dist / 0.3)
        else:
            price_score = 0.0
        breakdown["price_uncertainty"] = price_score

        # --- 4. Time-of-day ---
        utc_hour = datetime.utcfromtimestamp(activity.timestamp).hour
        et_hour = (utc_hour + self._ET_UTC_OFFSET) % 24
        breakdown["time_of_day"] = self._ET_HOUR_WEIGHTS.get(et_hour, 0.1) * 0.15

        # --- 5. Trader deviation ---
        if trader_history and trader_history.avg_trade_size > 0:
            # How many X of their average is this trade?
            multiple = activity.usdc_size / trader_history.avg_trade_size
            # 1x=normal, 2x=notable, 5x=very unusual, 10x+=extreme
            if multiple >= 5:
                deviation_score = 0.15
            elif multiple >= 2:
                deviation_score = 0.05 + (multiple - 2) / 3 * 0.10
            else:
                deviation_score = 0.0
            breakdown["trader_deviation"] = deviation_score
        else:
            # Unknown trader history — slightly suspicious
            breakdown["trader_deviation"] = 0.05

        # --- 6. Cluster signal ---
        cluster_score = self._get_cluster_score(activity, market_id=market_id)
        breakdown["cluster"] = cluster_score

        # --- 7. Niche market bonus ---
        # Small/niche markets have higher information asymmetry value.
        # Large political/macro markets (volume > $5M/day) are noisy;
        # small markets (< $500k/day) are where insider signals matter most.
        if market and market.volume_24hr > 0:
            vol = market.volume_24hr
            if vol < 100_000:
                niche_score = 0.15  # very niche
            elif vol < 500_000:
                niche_score = 0.10
            elif vol < 2_000_000:
                niche_score = 0.05
            else:
                niche_score = 0.0  # large/macro market, no bonus
            breakdown["niche_market"] = niche_score
        else:
            breakdown["niche_market"] = 0.05

        # --- Total ---
        total = sum(breakdown.values())
        total = min(1.0, max(0.0, total))

        return total, breakdown

    def record_trade(self, activity: TradeActivity, market_id: str):
        """Record a trade for cluster detection. Call for every trade, not just whales."""
        self._recent_trades[market_id].append((
            activity.timestamp,
            activity.side,
            activity.usdc_size,
        ))

    def _get_cluster_score(self, activity: TradeActivity, market_id: str = "") -> float:
        """
        Check if there are multiple same-direction trades in a short window.

        A cluster of BUY or SELL in the same market in 5 minutes suggests
        coordinated or informed trading.
        """
        # Use the same market_id key as record_trade()
        key = market_id or activity.condition_id
        recent = self._recent_trades.get(key)
        if not recent:
            return 0.0

        now = activity.timestamp
        cutoff = now - self._CLUSTER_WINDOW_SECONDS

        # Count same-direction trades in window
        same_dir_count = 0
        same_dir_volume = 0.0
        for ts, side, size in recent:
            if ts >= cutoff and side == activity.side:
                same_dir_count += 1
                same_dir_volume += size

        if same_dir_count >= self._CLUSTER_MIN_COUNT:
            # 3 trades = 0.05, 5+ = 0.10, volume also matters
            count_score = min(0.10, 0.02 * same_dir_count)
            vol_bonus = min(0.05, same_dir_volume / 500000)
            return count_score + vol_bonus

        return 0.0

    # ================================================================
    # Pre-filter (before LLM)
    # ================================================================

    def should_analyze(
        self,
        activity: TradeActivity,
        market: Optional[Market] = None,
        trader_history: Optional[TraderHistory] = None,
        market_id: str = "",
        min_score: float = 0.40,
    ) -> Tuple[bool, float, dict]:
        """
        Decide whether a whale trade warrants LLM analysis.

        Returns:
            (should_analyze, score, breakdown)
        """
        score, breakdown = self.get_anomaly_score(
            activity, market, trader_history, market_id=market_id,
        )
        return score >= min_score, score, breakdown

    # ================================================================
    # Legacy compatibility
    # ================================================================

    def is_anomalous_trade(self, activity: TradeActivity) -> bool:
        """Check if a trade is anomalous based on size and price."""
        if activity.usdc_size < self.settings.min_trade_size_usd:
            return False
        if not (self.settings.min_price <= activity.price <= self.settings.max_price):
            return False
        return True

    def filter_whale_trades(
        self,
        trades: List[WhaleTrade],
        min_score: float = 0.5,
    ) -> List[WhaleTrade]:
        """Filter whale trades by anomaly score."""
        filtered = []
        for trade in trades:
            score, _ = self.get_anomaly_score(trade.trade)
            if score >= min_score:
                filtered.append(trade)
        return filtered

    # ================================================================
    # LLM context formatting
    # ================================================================

    def analyze_trade_context(self, whale_trade: WhaleTrade) -> dict:
        """Analyze the context of a whale trade for LLM input."""
        trade = whale_trade.trade

        # Direction interpretation (only BUY trades, no normalization)
        if trade.outcome == "Yes":
            direction_meaning = f"交易者买入 Yes Token @ {trade.price:.4f}，看多（认为事件会发生）"
        else:
            direction_meaning = f"交易者买入 No Token @ {trade.price:.4f}，看空（认为事件不会发生）"

        # Buy price directly reflects taker's conviction — lower price = higher odds bet
        implied_prob = trade.price

        # Market state
        market_state = "uncertain"
        if whale_trade.market_outcome_prices:
            max_price = max(whale_trade.market_outcome_prices)
            if max_price > 0.7:
                market_state = "leaning towards one outcome"
            elif max_price < 0.6:
                market_state = "highly uncertain"

        # Multi-dimensional anomaly score
        score, breakdown = self.get_anomaly_score(trade)

        return {
            "trade_size_usd": trade.usdc_size,
            "trade_side": trade.side,
            "trade_price": trade.price,
            "trade_outcome": trade.outcome,
            "direction_meaning": direction_meaning,
            "implied_probability": implied_prob,
            "market_state": market_state,
            "anomaly_score": score,
            "anomaly_breakdown": breakdown,
            "market_question": whale_trade.market_question,
            "market_outcomes": whale_trade.market_outcomes,
            "current_prices": whale_trade.market_outcome_prices,
        }

    def format_for_llm(self, whale_trade: WhaleTrade) -> str:
        """Format whale trade data for LLM analysis."""
        context = self.analyze_trade_context(whale_trade)
        trade = whale_trade.trade

        # Build outcome prices string
        prices_str = ""
        for outcome, price in zip(context["market_outcomes"], context["current_prices"]):
            prices_str += f"  - {outcome}: {price:.2%}\n"

        # Trader profile
        trader_profile = self.trader_profiler.generate_profile(
            wallet_address=trade.proxy_wallet or "Unknown",
            ranking=whale_trade.trader_ranking,
            history=whale_trade.trader_history,
        )
        trader_profile_str = self.trader_profiler.format_profile_for_llm(trader_profile)

        # Anomaly breakdown string
        bd = context["anomaly_breakdown"]
        breakdown_str = (
            f"  绝对金额: {bd.get('size_abs', 0):.2f} | "
            f"相对市场: {bd.get('size_relative', 0):.2f} | "
            f"价格不确定性: {bd.get('price_uncertainty', 0):.2f} | "
            f"交易时间: {bd.get('time_of_day', 0):.2f} | "
            f"交易者偏离: {bd.get('trader_deviation', 0):.2f} | "
            f"聚集信号: {bd.get('cluster', 0):.2f}"
        )

        return f"""
## 大额交易异常检测报告

### 交易详情
- **交易金额**: ${context['trade_size_usd']:,.2f} USDC
- **交易方向**: BUY {context['trade_outcome']} Token ({'看多' if context['trade_outcome'] == 'Yes' else '看空'})
- **买入价格**: {context['trade_price']:.4f}（赔率约 {1/context['trade_price']:.1f}x）
- **交易时间**: {datetime.fromtimestamp(trade.timestamp).strftime('%Y-%m-%d %H:%M:%S UTC')}
- **交易者钱包**: {trade.proxy_wallet or 'Unknown'}

### 异常评分
- **综合评分**: {context['anomaly_score']:.2f}/1.00
- **评分分解**:
{breakdown_str}

### 交易解读
- **方向含义**: {context['direction_meaning']}
{trader_profile_str}

### 市场信息
- **市场问题**: {context['market_question']}
- **市场描述**: {whale_trade.market_description or 'N/A'}
- **市场状态**: {context['market_state']}
- **当前赔率**:
{prices_str}

{whale_trade.format_event_positions()}

{whale_trade.format_top_traders()}

### 分析要点
1. 这是一笔 ${context['trade_size_usd']:,.2f} 的大额交易，方向为 **BUY {context['trade_outcome']} Token**
2. {context['direction_meaning']}
3. **重点分析上方的 Trader Profile JSON，综合排名、PnL、交易行为和近期交易记录判断交易者可信度**
4. **注意分析该鲸鱼在同一事件下的其他持仓** — 如果持有反向仓位可能是对冲策略
5. **参考该市场 Top 多空持仓者的阵营** — 高排名交易者集中在哪一方

请分析这笔交易的信息不对称可能性。
"""
