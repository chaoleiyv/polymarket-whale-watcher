"""
Polymarket Whale Watcher - Main Entry Point

This bot monitors trending Polymarket markets for large (whale) trades
and generates AI-powered analysis reports to assist user decision-making.

Flow:
1. Fetch trending markets (by 24hr volume, excluding sports)
2. Monitor these markets for trades
3. Detect anomalous trades ($1,000+, price 0.2-0.8)
4. Generate analysis reports using LLM
5. Output reports for user review (no automatic trading)
"""
import asyncio
import os
import re
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer

from src.config import get_settings
from src.services.market_fetcher import MarketFetcher
from src.services.trade_monitor import TradeMonitor
from src.services.llm_analyzer import LLMAnalyzer
from src.models.trade import WhaleTrade
from src.utils.logger import setup_logging, WhaleWatcherLogger

app = typer.Typer(help="Polymarket Whale Watcher - AI-powered whale trade analysis")
logger = WhaleWatcherLogger()


class WhaleWatcher:
    """Main whale watcher application."""

    # Reports directory
    REPORTS_DIR = Path(__file__).parent.parent / "reports"

    def __init__(self):
        self.settings = get_settings()
        self.market_fetcher = MarketFetcher()
        self.trade_monitor = TradeMonitor(on_whale_detected=self.on_whale_detected)
        self.llm_analyzer = LLMAnalyzer()

        self._running = False
        self._refresh_interval = 300  # Refresh markets every 5 minutes

        # Ensure reports directory exists
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    def _sanitize_filename(self, text: str, max_length: int = 50) -> str:
        """Sanitize text for use in filename."""
        # Remove special characters, keep alphanumeric and spaces
        sanitized = re.sub(r'[^\w\s-]', '', text)
        # Replace spaces with underscores
        sanitized = re.sub(r'\s+', '_', sanitized)
        # Truncate if too long
        return sanitized[:max_length]

    def _save_report(self, whale_trade: WhaleTrade, full_report: str) -> str:
        """
        Save report to a markdown file.

        Args:
            whale_trade: The whale trade
            full_report: The formatted report

        Returns:
            Path to the saved file
        """
        trade = whale_trade.trade
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        market_name = self._sanitize_filename(whale_trade.market_question)

        filename = f"{timestamp}_{trade.side}_{int(trade.usdc_size)}USD_{market_name}.md"
        filepath = self.REPORTS_DIR / filename

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(full_report)

        return str(filepath)

    async def on_whale_detected(self, whale_trade: WhaleTrade) -> None:
        """
        Callback when a whale trade is detected.

        Args:
            whale_trade: The detected whale trade
        """
        trade = whale_trade.trade

        # Log detection
        logger.whale_detected(
            amount=trade.usdc_size,
            side=trade.side,
            price=trade.price,
            market=whale_trade.market_question,
        )

        # Analyze with LLM
        logger.info("Generating analysis report...")
        decision = await self.llm_analyzer.analyze_whale_trade(whale_trade)

        # Print the full report (includes analysis + decision summary)
        full_report = self.llm_analyzer.format_full_report(
            whale_trade,
            decision,
            historical_report_count=self.llm_analyzer.last_historical_report_count,
        )
        print(full_report)

        # Save report to file
        filepath = self._save_report(whale_trade, full_report)
        logger.info(f"Report saved to: {filepath}")

        logger.separator()

    async def refresh_markets(self) -> None:
        """Fetch and update the list of monitored markets."""
        logger.info("Fetching trending markets...")

        trending_markets = self.market_fetcher.get_trending_markets(
            limit=self.settings.trending_markets_limit
        )

        if trending_markets:
            self.trade_monitor.set_monitored_markets(trending_markets)
            logger.info(f"Now monitoring {len(trending_markets)} trending markets")
        else:
            logger.error("Failed to fetch trending markets")

    async def run(self) -> None:
        """Run the main whale watcher loop."""
        self._running = True

        # Initial market fetch
        await self.refresh_markets()

        # Log startup
        logger.monitoring_started(
            market_count=len(self.trade_monitor._monitored_markets),
            interval=self.settings.fetch_interval_seconds,
            min_trade_size=self.settings.min_trade_size_usd,
            min_price=self.settings.min_price,
            max_price=self.settings.max_price,
        )

        # Start monitoring and market refresh tasks
        monitor_task = asyncio.create_task(self.trade_monitor.run())
        refresh_task = asyncio.create_task(self._refresh_loop())

        try:
            await asyncio.gather(monitor_task, refresh_task)
        except asyncio.CancelledError:
            logger.info("Shutting down...")
        finally:
            self.trade_monitor.stop()
            await self.trade_monitor.close()

    async def _refresh_loop(self) -> None:
        """Periodically refresh the market list."""
        while self._running:
            await asyncio.sleep(self._refresh_interval)
            if self._running:
                await self.refresh_markets()

    def stop(self) -> None:
        """Stop the whale watcher."""
        self._running = False
        self.trade_monitor.stop()


# Global instance for signal handling
_watcher: Optional[WhaleWatcher] = None


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info("Received shutdown signal...")
    if _watcher:
        _watcher.stop()
    sys.exit(0)


@app.command()
def run(
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug logging"),
):
    """Start the whale watcher bot."""
    global _watcher

    # Setup logging
    setup_logging("DEBUG" if debug else "INFO")

    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Create and run watcher
    _watcher = WhaleWatcher()

    try:
        asyncio.run(_watcher.run())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    finally:
        logger.info("Whale watcher stopped")


@app.command()
def check_markets(
    limit: int = typer.Option(10, "--limit", "-l", help="Number of markets to show"),
):
    """Check current trending markets."""
    setup_logging("INFO")

    fetcher = MarketFetcher()
    markets = fetcher.get_trending_markets(limit=limit)

    print(f"\n{'='*80}")
    print(f"Top {len(markets)} Trending Markets by 24hr Volume")
    print(f"{'='*80}\n")

    for tm in markets:
        m = tm.market
        prices = ", ".join(
            [f"{o}: {p:.2%}" for o, p in zip(m.outcomes, m.outcome_prices)]
        )
        print(f"#{tm.rank} | Vol24h: ${tm.volume_24hr:,.0f}")
        print(f"   Question: {m.question}")
        print(f"   Prices: {prices}")
        print(f"   ID: {m.id}")
        print()


@app.command()
def test_analyze(
    market_id: str = typer.Argument(..., help="Market ID to test analysis on"),
):
    """Test LLM analysis on a specific market (simulates a whale trade)."""
    setup_logging("INFO")

    fetcher = MarketFetcher()
    market = fetcher.get_market_by_id(market_id)

    if not market:
        print(f"Market {market_id} not found")
        raise typer.Exit(1)

    # Create a simulated whale trade
    from src.models.trade import TradeActivity, WhaleTrade
    import time

    fake_activity = TradeActivity(
        transaction_hash="test_" + str(int(time.time())),
        timestamp=int(time.time()),
        condition_id=market.condition_id or "",
        asset=market.clob_token_ids[0] if market.clob_token_ids else "",
        side="BUY",
        size=50000.0,
        usdc_size=25000.0,  # Simulated $25k trade
        price=0.45,  # Simulated price
        outcome=market.outcomes[0] if market.outcomes else "",
        outcome_index=0,
        title=market.question,
    )

    whale_trade = WhaleTrade(
        id=f"test_{market_id}",
        trade=fake_activity,
        market_id=market.id,
        market_question=market.question,
        market_description=market.description,
        market_outcomes=market.outcomes,
        market_outcome_prices=market.outcome_prices,
    )

    print(f"\nSimulating whale trade analysis for:")
    print(f"  Market: {market.question}")
    print(f"  Trade: $25,000 BUY @ 0.45")
    print(f"\nAnalyzing with LLM...\n")

    analyzer = LLMAnalyzer()
    decision = asyncio.run(analyzer.analyze_whale_trade(whale_trade))

    print(analyzer.format_decision_report(decision))
    print("\nFull Analysis:")
    print("-" * 60)
    print(decision.analysis)


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
