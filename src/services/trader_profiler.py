"""Trader profiler service - generates structured trader profiles for LLM consumption."""
import json
import logging
from typing import Optional

from src.models.trade import TraderRanking, TraderHistory

logger = logging.getLogger(__name__)


class TraderProfiler:
    """
    Generates structured trader profiles for LLM consumption.

    Only organizes raw data into a clean JSON structure.
    All interpretation and judgment is left to the LLM.
    """

    def generate_profile(
        self,
        wallet_address: str,
        ranking: Optional[TraderRanking],
        history: Optional[TraderHistory],
    ) -> dict:
        """
        Generate a structured trader profile from raw data.

        Returns:
            dict with raw trader data for LLM consumption
        """
        # Ranking - raw numbers only
        ranking_data = {
            "rank": ranking.rank if ranking else None,
            "pnl": ranking.pnl if ranking else None,
            "total_volume": ranking.volume if ranking else None,
            "verified": ranking.verified if ranking else False,
            "username": ranking.user_name if ranking else None,
        }

        # Trading behavior - raw numbers only
        large_trade_ratio = 0.0
        if history and history.total_trades > 0:
            large_trade_ratio = history.large_trades_count / history.total_trades

        behavior_data = {
            "total_trades": history.total_trades if history else 0,
            "total_volume": history.total_volume if history else 0.0,
            "avg_trade_size": history.avg_trade_size if history else 0.0,
            "large_trades_count": history.large_trades_count if history else 0,
            "large_trade_ratio": round(large_trade_ratio, 3),
            "active_markets": history.recent_markets[:5] if history and history.recent_markets else [],
        }

        # Recent trades - raw data
        recent_trades = []
        if history and history.recent_trades:
            for t in history.recent_trades[:10]:
                recent_trades.append({
                    "side": t.get("side", ""),
                    "size_usd": t.get("usdc_size", 0),
                    "price": t.get("price", 0),
                    "market": t.get("title", "")[:50],
                })

        return {
            "ranking": ranking_data,
            "behavior": behavior_data,
            "recent_trades": recent_trades,
        }

    def format_profile_for_llm(self, profile: dict) -> str:
        """Format the profile dict as JSON for LLM input."""
        profile_json = json.dumps(profile, ensure_ascii=False, indent=2)

        return f"""
### Trader Profile

```json
{profile_json}
```
"""
