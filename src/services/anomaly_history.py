"""Anomaly history service - stores and retrieves historical anomaly signals by market."""
import json
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

from src.models.anomaly_signal import AnomalySignal

logger = logging.getLogger(__name__)


class AnomalyHistoryService:
    """
    Service for storing and retrieving historical anomaly signals.

    Anomaly signals are stored in JSON files, organized by market.
    Only trades with medium or higher insider trading likelihood (>= 0.4) are stored.
    """

    # Minimum insider trading likelihood to store a signal
    MIN_INSIDER_LIKELIHOOD = 0.4

    def __init__(self, storage_dir: Optional[Path] = None):
        """
        Initialize the anomaly history service.

        Args:
            storage_dir: Path to the storage directory. Defaults to project's anomaly_signals dir.
        """
        if storage_dir is None:
            self.storage_dir = Path(__file__).parent.parent.parent / "anomaly_signals"
        else:
            self.storage_dir = storage_dir

        # Ensure storage directory exists
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_market_id(self, market_id: str) -> str:
        """
        Sanitize market ID for use in filename.

        Args:
            market_id: The market ID

        Returns:
            Sanitized market ID safe for filenames
        """
        # Keep only alphanumeric characters and hyphens
        return re.sub(r'[^\w\-]', '_', market_id)

    def _get_market_filepath(self, market_id: str) -> Path:
        """
        Get the filepath for a market's anomaly signals.

        Args:
            market_id: The market ID

        Returns:
            Path to the market's anomaly signals file
        """
        sanitized_id = self._sanitize_market_id(market_id)
        return self.storage_dir / f"{sanitized_id}.json"

    def should_store_signal(self, insider_likelihood: float) -> bool:
        """
        Check if a signal should be stored based on insider trading likelihood.

        Args:
            insider_likelihood: The insider trading likelihood score (0-1)

        Returns:
            True if the signal should be stored, False otherwise
        """
        return insider_likelihood >= self.MIN_INSIDER_LIKELIHOOD

    def store_signal(self, signal: AnomalySignal) -> bool:
        """
        Store an anomaly signal for a market.

        Args:
            signal: The anomaly signal to store

        Returns:
            True if stored successfully, False otherwise
        """
        if not self.should_store_signal(signal.insider_trading_likelihood):
            logger.debug(
                f"Signal not stored: insider likelihood {signal.insider_trading_likelihood:.2f} "
                f"below threshold {self.MIN_INSIDER_LIKELIHOOD}"
            )
            return False

        filepath = self._get_market_filepath(signal.market_id)

        # Load existing signals
        existing_signals = self._load_signals_from_file(filepath)

        # Check for duplicate (same transaction hash)
        for existing in existing_signals:
            if existing.transaction_hash == signal.transaction_hash:
                logger.debug(f"Signal already exists for transaction: {signal.transaction_hash}")
                return False

        # Add new signal
        existing_signals.append(signal)

        # Save back to file
        try:
            self._save_signals_to_file(filepath, existing_signals)
            logger.info(
                f"Stored anomaly signal for market {signal.market_id}: "
                f"${signal.trade_size_usd:,.2f} {signal.trade_side} "
                f"(insider likelihood: {signal.insider_trading_likelihood:.0%})"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to store anomaly signal: {e}")
            return False

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
        filepath = self._get_market_filepath(market_id)
        signals = self._load_signals_from_file(filepath)

        if not signals:
            return []

        # Get top N most recent signals (by trade timestamp)
        signals_by_time = sorted(signals, key=lambda s: s.trade_timestamp, reverse=True)
        recent_signals = signals_by_time[:top_recent]

        # Get top N highest insider trading likelihood signals
        signals_by_likelihood = sorted(signals, key=lambda s: s.insider_trading_likelihood, reverse=True)
        high_likelihood_signals = signals_by_likelihood[:top_likelihood]

        # Deduplicate by transaction_hash
        seen_hashes = set()
        combined_signals = []

        for signal in recent_signals + high_likelihood_signals:
            if signal.transaction_hash not in seen_hashes:
                seen_hashes.add(signal.transaction_hash)
                combined_signals.append(signal)

        # Sort final result by trade timestamp (newest first)
        combined_signals.sort(key=lambda s: s.trade_timestamp, reverse=True)

        return combined_signals

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

**重要**: 该市场之前已经检测到 {signal_count} 笔异常交易。请将这些历史信号与当前最新信号一起进行综合分析，统一评估内幕交易可能性。

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
3. 如果多个高排名交易者都指向同一方向，内幕交易可能性显著提高
4. 如果信号方向相反，需要分析原因（时间变化、新信息、不同判断）
5. 考虑时间因素：越近期的信号越有参考价值
6. 观察交易金额的变化趋势：金额是否在增加？
"""
        return context

    def _load_signals_from_file(self, filepath: Path) -> List[AnomalySignal]:
        """
        Load anomaly signals from a JSON file.

        Args:
            filepath: Path to the JSON file

        Returns:
            List of AnomalySignal objects
        """
        if not filepath.exists():
            return []

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)

            signals = []
            for item in data:
                try:
                    signal = AnomalySignal.model_validate(item)
                    signals.append(signal)
                except Exception as e:
                    logger.warning(f"Failed to parse anomaly signal: {e}")
                    continue

            return signals
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON file {filepath}: {e}")
            return []
        except Exception as e:
            logger.error(f"Failed to load signals from {filepath}: {e}")
            return []

    def _save_signals_to_file(self, filepath: Path, signals: List[AnomalySignal]) -> None:
        """
        Save anomaly signals to a JSON file.

        Args:
            filepath: Path to the JSON file
            signals: List of AnomalySignal objects to save
        """
        data = [signal.model_dump(mode='json') for signal in signals]

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    def get_all_market_ids(self) -> List[str]:
        """
        Get all market IDs that have stored anomaly signals.

        Returns:
            List of market IDs
        """
        market_ids = []
        for filepath in self.storage_dir.glob("*.json"):
            market_id = filepath.stem
            market_ids.append(market_id)
        return market_ids

    def get_signal_count(self, market_id: str) -> int:
        """
        Get the number of stored signals for a market.

        Args:
            market_id: The market ID

        Returns:
            Number of stored signals
        """
        filepath = self._get_market_filepath(market_id)
        signals = self._load_signals_from_file(filepath)
        return len(signals)

    def cleanup_old_signals(self, max_age_days: int = 30) -> int:
        """
        Remove signals older than the specified number of days.

        Args:
            max_age_days: Maximum age of signals to keep

        Returns:
            Number of signals removed
        """
        from datetime import datetime, timedelta, timezone

        cutoff_time = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        total_removed = 0

        for filepath in self.storage_dir.glob("*.json"):
            signals = self._load_signals_from_file(filepath)
            original_count = len(signals)

            # Filter out old signals
            signals = [s for s in signals if s.detected_at >= cutoff_time]
            removed_count = original_count - len(signals)

            if removed_count > 0:
                self._save_signals_to_file(filepath, signals)
                total_removed += removed_count
                logger.info(f"Removed {removed_count} old signals from {filepath.stem}")

        return total_removed
