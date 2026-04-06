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
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import typer

from src.config import get_settings
from src.db.database import SignalDatabase
from src.services.market_fetcher import MarketFetcher
from src.services.trade_monitor import TradeMonitor
from src.services.price_monitor import PriceMonitor, VolatilityAlert
from src.services.llm_analyzer import LLMAnalyzer
from src.services.volatility_analyzer import VolatilityAnalyzer
from src.services.daily_briefing import DailyBriefingGenerator
from src.services.resolution_tracker import ResolutionTracker
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

        # Volatility analyzer for detecting "price leads news" signals
        self.volatility_analyzer = VolatilityAnalyzer()

        # Price monitor for ALL active markets (independent from trade monitor)
        self.price_monitor = PriceMonitor(
            window_seconds=3600,  # 1 hour
            threshold=0.20,  # 20%
            poll_interval=60,  # Poll every 60 seconds
            on_volatility_detected=self.on_volatility_detected,
        )

        # Database and resolution tracker
        self.db = SignalDatabase(self.settings.db_path)
        self.resolution_tracker = ResolutionTracker(self.db)

        # Daily briefing generator
        self.briefing_generator = DailyBriefingGenerator(self.settings.db_path)

        self._running = False
        self._refresh_interval = 900  # Refresh markets every 15 minutes
        self._resolution_check_interval = 1800  # Check resolutions every 30 minutes
        self._last_briefing_date = None  # Track last briefing date

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
        date_str = datetime.now().strftime("%Y%m%d")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        market_name = self._sanitize_filename(whale_trade.market_question)

        day_dir = self.REPORTS_DIR / date_str
        day_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{timestamp}_{trade.side}_{int(trade.usdc_size)}USD_{market_name}.md"
        filepath = day_dir / filename

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
            side=f"BUY {trade.outcome}",
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
            historical_signal_count=self.llm_analyzer.last_historical_signal_count,
        )
        print(full_report)

        # Save report to file
        filepath = self._save_report(whale_trade, full_report)
        logger.info(f"Report saved to: {filepath}")

        # Real-time email alert for high information asymmetry (>= 60%)
        ias = decision.recommendation.information_asymmetry_score
        if ias >= 0.6:
            self._send_alert_email(whale_trade, full_report, ias)

        logger.separator()

    def _send_alert_email(self, whale_trade: WhaleTrade, report: str, likelihood: float):
        """Send real-time email alert for high insider trading likelihood signals."""
        settings = get_settings()
        if not settings.email_enabled or not settings.email_sender or not settings.email_password:
            return

        alert_recipient = "1253608463@qq.com"
        trade = whale_trade.trade

        try:
            subject = (
                f"内幕交易警报 ({likelihood:.0%}) — "
                f"BUY {trade.outcome} @ {trade.price:.4f} "
                f"${trade.usdc_size:,.0f} — {whale_trade.market_question[:50]}"
            )

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.email_sender
            msg["To"] = alert_recipient
            msg.attach(MIMEText(report, "plain", "utf-8"))

            with smtplib.SMTP_SSL(settings.email_smtp_server, settings.email_smtp_port) as server:
                server.login(settings.email_sender, settings.email_password)
                server.sendmail(settings.email_sender, alert_recipient, msg.as_string())

            logger.info(f"Insider alert email sent to {alert_recipient} (likelihood: {likelihood:.0%})")
        except Exception as e:
            logger.error(f"Failed to send alert email: {e}")

    async def on_volatility_detected(self, alert: VolatilityAlert) -> None:
        """
        Callback when price volatility is detected.

        Analyzes the volatility to determine if it's a "price leads news" signal.

        Args:
            alert: The volatility alert
        """
        logger.info(
            f"Volatility detected: {alert.market_question[:50]}... "
            f"{alert.direction} {abs(alert.price_change_percent):.1%}"
        )

        # Analyze with LLM to check if price leads news
        logger.info("Analyzing volatility for leading signal detection...")
        signal = await self.volatility_analyzer.analyze_volatility(alert)

        if signal:
            # Print the analysis report
            report = self.volatility_analyzer.format_signal_report(signal)
            print(report)

            if signal.is_leading_signal:
                logger.info(
                    f"LEADING SIGNAL recorded: {alert.market_question[:50]}... "
                    f"Time advantage: {signal.time_advantage_minutes} minutes"
                )
            else:
                logger.info(
                    f"Signal analyzed: {signal.signal_type.value} "
                    f"(confidence: {signal.confidence:.1%})"
                )

            # Print stats
            stats = self.volatility_analyzer.get_leading_signals_stats()
            logger.info(
                f"Dataset stats: {stats['total_signals']} total, "
                f"{stats['leading_signals']} leading signals"
            )

        logger.separator()

    async def refresh_markets(self) -> None:
        """Fetch and update the list of monitored markets."""
        logger.info("Fetching trending markets...")

        trending_markets = self.market_fetcher.get_trending_markets(
            limit=self.settings.trending_markets_limit
        )

        # Additionally scan for specialized market categories
        # that may not be in the top trending list
        existing_ids = {tm.market.id for tm in trending_markets}

        # 1. Token launch / crypto project markets
        token_markets = self.market_fetcher.get_token_launch_markets()
        token_added = 0
        for tm in token_markets:
            if tm.market.id not in existing_ids:
                trending_markets.append(tm)
                existing_ids.add(tm.market.id)
                token_added += 1

        if token_added:
            logger.info(
                f"Added {token_added} token launch markets "
                f"(total: {len(trending_markets)})"
            )

        if trending_markets:
            self.trade_monitor.set_monitored_markets(trending_markets)
            logger.info(f"Now monitoring {len(trending_markets)} markets")
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

        # Start monitoring tasks:
        # 1. Trade monitor - watches top markets for whale trades
        # 2. Market refresh - refreshes the market list periodically
        # 3. Daily briefing - generates daily summary at midnight
        # 4. Resolution check - checks if markets with signals have resolved
        # NOTE: Price volatility monitor is temporarily disabled
        monitor_task = asyncio.create_task(self.trade_monitor.run())
        # price_monitor_task = asyncio.create_task(self.price_monitor.run())
        refresh_task = asyncio.create_task(self._refresh_loop())
        briefing_task = asyncio.create_task(self._briefing_loop())
        resolution_task = asyncio.create_task(self._resolution_check_loop())

        try:
            await asyncio.gather(monitor_task, refresh_task, briefing_task, resolution_task)
        except asyncio.CancelledError:
            logger.info("Shutting down...")
        finally:
            self.trade_monitor.stop()
            self.price_monitor.stop()
            await self.trade_monitor.close()

    async def _refresh_loop(self) -> None:
        """Periodically refresh the market list."""
        while self._running:
            await asyncio.sleep(self._refresh_interval)
            if self._running:
                await self.refresh_markets()

    def _briefing_already_sent(self, date: datetime) -> bool:
        """Check if briefing for a date was already generated (file exists)."""
        from src.services.daily_briefing import BRIEFINGS_DIR
        date_str = date.strftime("%Y-%m-%d")
        return (BRIEFINGS_DIR / f"briefing_{date_str}.md").exists()

    async def _briefing_loop(self) -> None:
        """Generate daily briefing for previous day at 10:00 local time."""
        while self._running:
            now = datetime.now()
            today = now.date()
            yesterday = now - timedelta(days=1)

            # Generate at 10:00 local time, skip if already sent (survives restart)
            if now.hour == 10 and now.minute >= 0:
                if self._last_briefing_date != today and not self._briefing_already_sent(yesterday):
                    try:
                        filepath = self.briefing_generator.generate_briefing()
                        if filepath:
                            logger.info(f"Daily briefing generated: {filepath}")
                        self._last_briefing_date = today
                    except Exception as e:
                        logger.error(f"Error generating daily briefing: {e}")
                else:
                    self._last_briefing_date = today

            # Check every minute
            await asyncio.sleep(60)

    async def _resolution_check_loop(self) -> None:
        """Periodically check if markets with signals have resolved."""
        # Initial delay to let the system start up
        await asyncio.sleep(60)

        while self._running:
            try:
                result = await self.resolution_tracker.check_all()
                if result["resolved"] > 0:
                    logger.info(
                        f"Resolution check: {result['resolved']} markets resolved, "
                        f"{result['signals_updated']} signals updated"
                    )
            except Exception as e:
                logger.error(f"Error in resolution check: {e}")

            await asyncio.sleep(self._resolution_check_interval)

    def stop(self) -> None:
        """Stop the whale watcher."""
        self._running = False
        self.trade_monitor.stop()
        self.price_monitor.stop()


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


@app.command()
def briefing(
    date: str = typer.Option(None, "--date", "-d", help="Date in YYYY-MM-DD format (defaults to yesterday)"),
    today: bool = typer.Option(False, "--today", "-t", help="Generate briefing for today instead of yesterday"),
):
    """Generate daily briefing manually."""
    setup_logging("INFO")

    settings = get_settings()
    generator = DailyBriefingGenerator(settings.db_path)

    if today:
        filepath = generator.generate_today_briefing()
    elif date:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d")
            filepath = generator.generate_briefing(target_date)
        except ValueError:
            print(f"Invalid date format: {date}. Use YYYY-MM-DD")
            raise typer.Exit(1)
    else:
        filepath = generator.generate_briefing()  # Yesterday by default

    if filepath:
        print(f"\nBriefing generated: {filepath}")

        # Print the content
        with open(filepath, 'r', encoding='utf-8') as f:
            print("\n" + "=" * 80)
            print(f.read())
    else:
        print("\nNo signals found for the specified date. No briefing generated.")


@app.command()
def migrate():
    """Migrate anomaly signals from JSON files to SQLite database."""
    setup_logging("INFO")

    settings = get_settings()
    db = SignalDatabase(settings.db_path)

    json_dir = Path(__file__).parent.parent / "anomaly_signals"
    print(f"Migrating signals from {json_dir} to {settings.db_path}")

    count = db.migrate_from_json(json_dir)
    print(f"Migration complete: {count} signals migrated")

    # Show stats
    stats = db.get_stats()
    print(f"\nDatabase stats:")
    print(f"  Total signals: {stats['total_signals']}")
    print(f"  Resolved: {stats['resolved']}")


@app.command()
def dashboard(
    port: int = typer.Option(8000, "--port", "-p", help="Port to run the dashboard on"),
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Host to bind to"),
):
    """Start the signal performance dashboard web server."""
    setup_logging("INFO")

    import uvicorn
    from src.dashboard import app as dashboard_app

    print(f"Starting dashboard at http://{host}:{port}")
    uvicorn.run(dashboard_app, host=host, port=port)


def main():
    """Entry point."""
    app()


if __name__ == "__main__":
    main()
