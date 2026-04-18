"""Anomaly history service - stores and retrieves historical anomaly signals via SQLite."""
import logging
from typing import List, Optional

from src.db.database import SignalDatabase
from src.models.anomaly_signal import AnomalySignal

logger = logging.getLogger(__name__)


class AnomalyHistoryService:
    """
    Service for storing and retrieving historical anomaly signals.

    Backend: SQLite via SignalDatabase.
    All analyzed signals are stored for tracking accuracy.
    Only signals with likelihood >= 0.4 are used as historical context for LLM.
    """

    # Minimum information asymmetry score to use as historical context for LLM
    MIN_CONTEXT_LIKELIHOOD = 0.4

    def __init__(self, db_path: str = "data/signals.db"):
        """
        Initialize the anomaly history service.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db = SignalDatabase(db_path)

    def should_store_signal(self, insider_likelihood: float) -> bool:
        """All analyzed signals should be stored for accuracy tracking."""
        return True

    def store_signal(self, signal: AnomalySignal) -> bool:
        """
        Store an anomaly signal for accuracy tracking.

        All analyzed signals are stored regardless of likelihood.
        """
        stored = self.db.insert_signal(signal)
        if stored:
            logger.info(
                f"Stored signal for market {signal.market_id}: "
                f"${signal.trade_size_usd:,.2f} {signal.trade_side} "
                f"(IAS: {signal.information_asymmetry_score:.0%})"
            )
        return stored

    def get_signals_for_market(
        self,
        market_id: str,
        top_recent: int = 5,
        top_likelihood: int = 5,
    ) -> List[AnomalySignal]:
        """
        Get historical anomaly signals for a market.

        Selects the most recent signals and highest insider likelihood signals,
        then deduplicates and returns the combined list.

        Args:
            market_id: The market ID
            top_recent: Number of most recent signals to include
            top_likelihood: Number of highest insider likelihood signals to include

        Returns:
            List of AnomalySignal objects (deduplicated, sorted by trade timestamp newest first)
        """
        return self.db.get_signals_for_market(market_id, top_recent, top_likelihood)

    def format_historical_signals_context(
        self,
        signals: List[AnomalySignal],
    ) -> str:
        """
        Format historical anomaly signals into a context string for LLM.

        Args:
            signals: List of historical anomaly signals

        Returns:
            Formatted string for LLM context
        """
        if not signals:
            return ""

        signal_count = len(signals)

        context = f"""
### 历史异常交易信号 (共 {signal_count} 笔)

**重要**: 该市场之前已经检测到 {signal_count} 笔异常交易。请将这些历史信号与当前最新信号一起进行综合分析，统一评估信息不对称可能性。

"""
        for i, signal in enumerate(signals, 1):
            context += f"""
---
#### 历史信号 {i}
{signal.to_context_string()}
---
"""

        context += """
**综合分析要点**:
1. 对比所有信号（历史+当前）的交易方向，分析是否有一致趋势
2. 对比不同交易者的排名和历史记录，判断"聪明钱"的流向
3. 如果多个高排名交易者都指向同一方向，信息不对称可能性显著提高
4. 如果信号方向相反，需要分析原因（时间变化、新信息、不同判断）
5. 考虑时间因素：越近期的信号越有参考价值
6. 观察交易金额的变化趋势：金额是否在增加？
"""
        return context

    def get_all_market_ids(self) -> List[str]:
        """
        Get all market IDs that have stored anomaly signals.

        Returns:
            List of market IDs
        """
        return self.db.get_all_market_ids()

    def get_signal_count(self, market_id: Optional[str] = None) -> int:
        """
        Get the number of stored signals.

        Args:
            market_id: Optional market ID to filter by

        Returns:
            Number of stored signals
        """
        return self.db.get_signal_count(market_id)

    def cleanup_old_signals(self, max_age_days: int = 30) -> int:
        """
        Remove signals older than the specified number of days.

        Args:
            max_age_days: Maximum age of signals to keep

        Returns:
            Number of signals removed
        """
        return self.db.cleanup_old_signals(max_age_days)
