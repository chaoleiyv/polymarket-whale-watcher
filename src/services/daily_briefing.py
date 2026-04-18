"""Daily briefing service - generates daily summary of high-value signals."""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

from src.config import get_settings
from src.db.database import SignalDatabase
from src.services.stats_engine import StatsEngine

logger = logging.getLogger(__name__)

# Directories
VOLATILITY_DIR = Path(__file__).parent.parent.parent / "price_volatility"
BRIEFINGS_DIR = Path(__file__).parent.parent.parent / "daily_briefings"


class DailyBriefingGenerator:
    """
    Generates daily briefings summarizing high-value signals.

    Includes:
    - Smart money signals with information asymmetry score >= 60%
    - Price volatility alerts
    - Historical signal performance stats
    """

    # Minimum information asymmetry score to include in briefing
    MIN_IAS = 0.6  # 60%

    # Maximum signals to include when falling back to top-N
    FALLBACK_TOP_N = 5

    def __init__(self, db_path: str = "data/signals.db"):
        """Initialize the briefing generator."""
        BRIEFINGS_DIR.mkdir(parents=True, exist_ok=True)
        self.db = SignalDatabase(db_path)
        self.stats_engine = StatsEngine(self.db)

    def _get_date_range(self, date: datetime) -> tuple:
        """
        Get start and end timestamps for a given date.

        Args:
            date: The date to get range for

        Returns:
            Tuple of (start_timestamp, end_timestamp)
        """
        start = datetime(date.year, date.month, date.day, 0, 0, 0)
        end = start + timedelta(days=1)
        return int(start.timestamp()), int(end.timestamp())

    def _load_insider_signals(self, date: datetime) -> tuple:
        """
        Load smart money signals for a specific date from the database.

        First tries to find signals with likelihood >= 60%.
        If none found, falls back to the top 5 by likelihood.

        Args:
            date: The date to load signals for

        Returns:
            Tuple of (signals list as dicts, is_fallback bool)
        """
        date_str = date.strftime("%Y-%m-%d")

        # Query all signals detected on this date
        all_signals = self.db.get_all_signals(limit=500, offset=0)
        day_signals = []
        for signal in all_signals:
            if signal.detected_at.strftime("%Y-%m-%d") == date_str:
                day_signals.append(signal)

        if not day_signals:
            return [], False

        # Sort by likelihood descending
        day_signals.sort(key=lambda s: s.information_asymmetry_score, reverse=True)

        # Convert to dicts for backward compat with _format_briefing
        def signal_to_dict(s):
            return {
                "market_id": s.market_id,
                "market_question": s.market_question,
                "transaction_hash": s.transaction_hash,
                "trade_size_usd": s.trade_size_usd,
                "trade_price": s.trade_price,
                "trade_outcome": s.trade_outcome,
                "information_asymmetry_score": s.information_asymmetry_score,
                "reasoning": s.reasoning,
                "insider_evidence": s.insider_evidence,
                "detected_at": s.detected_at.isoformat(),
            }

        # Filter high-likelihood signals
        high_likelihood = [
            signal_to_dict(s) for s in day_signals
            if s.information_asymmetry_score >= self.MIN_IAS
        ]

        if high_likelihood:
            return high_likelihood, False

        # Fallback: top N signals by likelihood
        return [signal_to_dict(s) for s in day_signals[:self.FALLBACK_TOP_N]], True

    def _load_volatility_alerts(self, date: datetime) -> List[Dict]:
        """
        Load price volatility alerts for a specific date.

        Args:
            date: The date to load alerts for

        Returns:
            List of volatility alerts
        """
        import json
        alerts_file = VOLATILITY_DIR / "volatility_alerts.json"
        date_str = date.strftime("%Y-%m-%d")

        if not alerts_file.exists():
            return []

        try:
            with open(alerts_file, 'r', encoding='utf-8') as f:
                all_alerts = json.load(f)

            # Filter alerts for the target date
            day_alerts = [
                alert for alert in all_alerts
                if alert.get("detected_at", "").startswith(date_str)
            ]

            # Sort by price change magnitude descending
            day_alerts.sort(
                key=lambda x: abs(x.get("price_change_percent", 0)),
                reverse=True
            )

            return day_alerts

        except Exception as e:
            logger.error(f"Error loading volatility alerts: {e}")
            return []

    def _format_briefing(
        self,
        date: datetime,
        insider_signals: List[Dict],
        volatility_alerts: List[Dict],
        is_fallback: bool = False,
    ) -> str:
        """
        Format the daily briefing as markdown.

        Args:
            date: The date of the briefing
            insider_signals: List of insider signals
            volatility_alerts: List of price volatility alerts
            is_fallback: True if signals are fallback (none >= 60%)

        Returns:
            Formatted markdown briefing
        """
        date_str = date.strftime("%Y-%m-%d")

        lines = [
            f"# Daily Signal Briefing - {date_str}",
            "",
            f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
        ]

        # Summary stats
        if is_fallback:
            summary_line = f"- No signals with confidence >= 60% today; showing the top **{len(insider_signals)}** by confidence"
        else:
            summary_line = f"- High-confidence information asymmetry signals: **{len(insider_signals)}** (confidence >= 60%)"

        lines.extend([
            "## Today's Overview",
            "",
            summary_line,
            f"- Abnormal price volatility events: **{len(volatility_alerts)}**",
            "",
        ])

        # Insider trading signals section
        if is_fallback:
            section_title = "## Today's Top Anomalous Trades by Confidence"
        else:
            section_title = "## High-Confidence Information Asymmetry Signals"

        lines.extend([
            "---",
            "",
            section_title,
            "",
        ])

        if insider_signals:
            for i, signal in enumerate(insider_signals, 1):
                likelihood = signal.get("information_asymmetry_score", 0)
                market_question = signal.get("market_question", "Unknown")
                trade_size = signal.get("trade_size_usd", 0)
                trade_price = signal.get("trade_price", 0)
                trade_outcome = signal.get("trade_outcome", "Yes")
                reasoning = signal.get("reasoning", "")
                insider_evidence = signal.get("insider_evidence", "")
                detected_at = signal.get("detected_at", "")

                # Odds calculation
                odds_str = f"{1/trade_price:.1f}x" if trade_price > 0 else "N/A"

                lines.extend([
                    f"### {i}. {market_question[:80]}{'...' if len(market_question) > 80 else ''}",
                    "",
                    f"| Metric | Value |",
                    f"|--------|-------|",
                    f"| Info Asymmetry | **{likelihood:.0%}** |",
                    f"| Direction | BUY {trade_outcome} Token ({'Bullish' if trade_outcome == 'Yes' else 'Bearish'}) |",
                    f"| Entry Price | {trade_price:.4f} (Odds {odds_str}) |",
                    f"| Trade Size | **${trade_size:,.0f}** USDC |",
                    f"| Detected At | {detected_at} |",
                    "",
                ])

                if reasoning:
                    lines.extend([
                        f"**Analysis**: {reasoning}",
                        "",
                    ])

                if insider_evidence:
                    lines.extend([
                        f"**Insider Evidence**: {insider_evidence}",
                        "",
                    ])

                lines.append("")
        else:
            lines.extend([
                "*No anomalous trade signals today*",
                "",
            ])

        # Volatility alerts section
        lines.extend([
            "---",
            "",
            "## Abnormal Price Volatility",
            "",
        ])

        if volatility_alerts:
            lines.extend([
                "| Market | Direction | Change | Start Price | End Price | Detected At |",
                "|--------|-----------|--------|-------------|-----------|-------------|",
            ])

            for alert in volatility_alerts:
                market_question = alert.get("market_question", "Unknown")
                # Truncate long market questions
                if len(market_question) > 40:
                    market_question = market_question[:37] + "..."

                direction = "Down" if alert.get("direction") == "DOWN" else "Up"
                price_change = abs(alert.get("price_change_percent", 0))
                start_price = alert.get("start_price", 0)
                end_price = alert.get("end_price", 0)
                detected_at = alert.get("detected_at", "")[:16]  # Trim to minute

                lines.append(
                    f"| {market_question} | {direction} | {price_change:.1%} | "
                    f"{start_price:.2%} | {end_price:.2%} | {detected_at} |"
                )

            lines.append("")
        else:
            lines.extend([
                "*No abnormal price volatility today*",
                "",
            ])

        # Signal performance stats section
        stats_summary = self.stats_engine.format_stats_summary()
        if stats_summary:
            lines.extend([
                "---",
                "",
                stats_summary,
            ])

        # Footer
        lines.extend([
            "---",
            "",
            "*This briefing was automatically generated by Polymarket Whale Watcher*",
        ])

        return "\n".join(lines)

    def generate_briefing(self, date: Optional[datetime] = None) -> Optional[str]:
        """
        Generate daily briefing for a specific date.

        Args:
            date: The date to generate briefing for (defaults to yesterday)

        Returns:
            Path to the saved briefing file, or None if no signals
        """
        if date is None:
            # Default to yesterday
            date = datetime.now() - timedelta(days=1)

        date_str = date.strftime("%Y-%m-%d")
        logger.info(f"Generating daily briefing for {date_str}")

        # Load signals
        insider_signals, is_fallback = self._load_insider_signals(date)
        volatility_alerts = self._load_volatility_alerts(date)

        # Check if there's anything to report
        if not insider_signals and not volatility_alerts:
            logger.info(f"No signals for {date_str}, skipping briefing")
            return None

        # Generate briefing
        briefing_content = self._format_briefing(date, insider_signals, volatility_alerts, is_fallback)

        # Save to file
        filename = f"briefing_{date_str}.md"
        filepath = BRIEFINGS_DIR / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(briefing_content)

        logger.info(
            f"Daily briefing saved to {filepath} "
            f"({len(insider_signals)} insider signals, {len(volatility_alerts)} volatility alerts)"
        )

        # Send email notification
        self._send_email(date_str, briefing_content)

        return str(filepath)

    def _send_email(self, date_str: str, content: str) -> None:
        """Send briefing via email if configured."""
        settings = get_settings()
        if not settings.email_enabled:
            return
        if not settings.email_sender or not settings.email_password:
            logger.warning("Email enabled but sender/password not configured, skipping")
            return

        try:
            recipients = [r.strip() for r in settings.email_recipient.split(",") if r.strip()]

            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"Polymarket Whale Daily Briefing - {date_str}"
            msg["From"] = settings.email_sender
            msg["To"] = ", ".join(recipients)

            # Markdown content as plain text
            text_part = MIMEText(content, "plain", "utf-8")
            msg.attach(text_part)

            with smtplib.SMTP_SSL(settings.email_smtp_server, settings.email_smtp_port) as server:
                server.login(settings.email_sender, settings.email_password)
                server.sendmail(settings.email_sender, recipients, msg.as_string())

            logger.info(f"Daily briefing emailed to {', '.join(recipients)}")
        except Exception as e:
            logger.error(f"Failed to send briefing email: {e}")

    def generate_today_briefing(self) -> Optional[str]:
        """
        Generate briefing for today (useful for testing or end-of-day summary).

        Returns:
            Path to the saved briefing file, or None if no signals
        """
        return self.generate_briefing(datetime.now())
