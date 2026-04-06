"""Stats engine - computes signal performance statistics from the database."""
import logging
from typing import List

from src.db.database import SignalDatabase
from src.models.anomaly_signal import AnomalySignal

logger = logging.getLogger(__name__)


class StatsEngine:
    """Computes signal performance statistics."""

    def __init__(self, db: SignalDatabase):
        self.db = db

    def get_overview(self) -> dict:
        """
        Get overall signal performance stats.

        Returns:
            Dict with total_signals, resolved, correct, win_rate, avg_roi, total_theoretical_pnl.
        """
        return self.db.get_stats()

    def get_stats_by_likelihood_tier(self) -> List[dict]:
        """
        Get stats broken down by information_asymmetry_score tiers.

        Tiers: 0.4-0.6, 0.6-0.8, 0.8-1.0

        Returns:
            List of tier stat dicts.
        """
        return self.db.get_stats_by_tier()

    def get_recent_resolved(self, limit: int = 20) -> List[AnomalySignal]:
        """Get recently resolved signals."""
        return self.db.get_recent_resolved(limit)

    def get_best_worst(self, n: int = 5) -> dict:
        """Get best and worst signals by ROI."""
        return self.db.get_best_worst(n)

    def format_stats_summary(self) -> str:
        """
        Format a human-readable stats summary for briefings.

        Returns:
            Markdown-formatted stats string.
        """
        stats = self.get_overview()
        tier_stats = self.get_stats_by_likelihood_tier()

        if stats["resolved"] == 0:
            return ""

        lines = [
            "## 信号历史战绩",
            "",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| 总信号数 | {stats['total_signals']} |",
            f"| 已验证 | {stats['resolved']} |",
            f"| 正确 | {stats['correct']} |",
            f"| 胜率 | **{stats['win_rate']:.1%}** |",
            f"| 平均ROI | **{stats['avg_roi']:+.1%}** |",
            f"| 理论总PnL | **{stats['total_theoretical_pnl']:+.2f}x** |",
            "",
        ]

        # Tier breakdown
        has_resolved_tiers = any(t["resolved"] > 0 for t in tier_stats)
        if has_resolved_tiers:
            lines.extend([
                "### 按信号可信度分层",
                "",
                "| 可信度区间 | 信号数 | 已验证 | 胜率 | 平均ROI |",
                "|-----------|-------|-------|------|---------|",
            ])
            for t in tier_stats:
                if t["total"] > 0:
                    wr = f"{t['win_rate']:.0%}" if t["resolved"] > 0 else "N/A"
                    roi = f"{t['avg_roi']:+.1%}" if t["resolved"] > 0 else "N/A"
                    lines.append(
                        f"| {t['tier']} | {t['total']} | {t['resolved']} | {wr} | {roi} |"
                    )
            lines.append("")

        return "\n".join(lines)
