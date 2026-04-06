"""Resolution tracker - checks if markets with signals have resolved and updates correctness."""
import asyncio
import logging
from datetime import datetime
from typing import Optional

from src.db.database import SignalDatabase
from src.services.market_fetcher import MarketFetcher

logger = logging.getLogger(__name__)


class ResolutionTracker:
    """Tracks market resolutions and computes signal correctness."""

    def __init__(self, db: SignalDatabase):
        self.db = db
        self.market_fetcher = MarketFetcher()

    def _determine_resolved_outcome(self, market) -> Optional[str]:
        """
        Determine the resolved outcome from a market.

        A market is considered resolved if closed==True and one outcome price >= 0.99.

        Returns:
            The winning outcome string (e.g. "Yes" or "No"), or None if not resolved.
        """
        if not market.closed:
            return None

        if not market.outcomes or not market.outcome_prices:
            return None

        for outcome, price in zip(market.outcomes, market.outcome_prices):
            if price >= 0.99:
                return outcome

        return None

    def _is_past_end_date(self, market) -> bool:
        """Check if a market's end_date has passed."""
        if not market.end_date:
            return True  # No end date, always check
        try:
            end_dt = datetime.fromisoformat(market.end_date.replace("Z", "+00:00"))
            return datetime.utcnow().replace(tzinfo=end_dt.tzinfo) >= end_dt
        except (ValueError, TypeError):
            return True

    async def check_all(self) -> dict:
        """
        Check all unresolved markets for resolution.

        Returns:
            Summary dict with counts.
        """
        unresolved_ids = self.db.get_unresolved_market_ids()
        if not unresolved_ids:
            logger.debug("No unresolved markets to check")
            return {"checked": 0, "resolved": 0, "signals_updated": 0}

        logger.info(f"Checking {len(unresolved_ids)} unresolved markets for resolution")

        checked = 0
        resolved = 0
        signals_updated = 0

        for market_id in unresolved_ids:
            try:
                market = self.market_fetcher.get_market_by_id(market_id)
                if not market:
                    logger.debug(f"Market {market_id} not found on API")
                    checked += 1
                    await asyncio.sleep(0.5)
                    continue

                # Optimization: skip markets whose end_date hasn't passed yet
                if not self._is_past_end_date(market):
                    checked += 1
                    await asyncio.sleep(0.5)
                    continue

                outcome = self._determine_resolved_outcome(market)
                if outcome:
                    updated = self.db.mark_market_resolved(
                        market_id=market_id,
                        resolved_outcome=outcome,
                        resolved_at=datetime.utcnow(),
                    )
                    resolved += 1
                    signals_updated += updated
                    logger.info(
                        f"Market resolved: {market.question[:50]}... "
                        f"outcome={outcome}, {updated} signals updated"
                    )

                checked += 1
                await asyncio.sleep(0.5)  # Rate limiting

            except Exception as e:
                logger.error(f"Error checking market {market_id}: {e}")
                checked += 1
                await asyncio.sleep(0.5)

        result = {
            "checked": checked,
            "resolved": resolved,
            "signals_updated": signals_updated,
        }
        logger.info(
            f"Resolution check complete: {checked} checked, "
            f"{resolved} resolved, {signals_updated} signals updated"
        )
        return result
