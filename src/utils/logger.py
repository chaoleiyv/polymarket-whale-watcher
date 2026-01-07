"""Logging utilities."""
import logging
import sys
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler

from src.config import get_settings


def setup_logging(level: Optional[str] = None) -> None:
    """
    Set up application logging with rich formatting.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR). Defaults to settings.
    """
    settings = get_settings()
    log_level = level or settings.log_level

    # Create rich console
    console = Console()

    # Configure root logger
    logging.basicConfig(
        level=log_level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_path=False,
            )
        ],
    )

    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("web3").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance.

    Args:
        name: Logger name (usually __name__)

    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class WhaleWatcherLogger:
    """Custom logger for whale watcher with formatted output."""

    def __init__(self):
        self.console = Console()
        self.logger = logging.getLogger("whale_watcher")

    def whale_detected(
        self,
        amount: float,
        side: str,
        price: float,
        market: str,
    ) -> None:
        """Log a whale trade detection."""
        self.console.print(
            f"\n[bold cyan]{'='*60}[/bold cyan]\n"
            f"[bold yellow]🐋 WHALE TRADE DETECTED![/bold yellow]\n"
            f"[bold cyan]{'='*60}[/bold cyan]\n"
            f"[green]Amount:[/green] ${amount:,.2f} USDC\n"
            f"[green]Side:[/green] {side}\n"
            f"[green]Price:[/green] {price:.4f}\n"
            f"[green]Market:[/green] {market}\n"
            f"[green]Time:[/green] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"[bold cyan]{'='*60}[/bold cyan]\n"
        )

    def report_generated(self, market: str) -> None:
        """Log that a report was generated."""
        self.console.print(
            f"\n[bold magenta]{'='*60}[/bold magenta]\n"
            f"[bold magenta]📊 ANALYSIS REPORT GENERATED[/bold magenta]\n"
            f"[bold magenta]{'='*60}[/bold magenta]\n"
            f"[green]Market:[/green] {market[:50]}...\n"
            f"[bold magenta]{'='*60}[/bold magenta]\n"
        )

    def monitoring_started(self, market_count: int, interval: int, min_trade_size: float = 1000, min_price: float = 0.2, max_price: float = 0.8) -> None:
        """Log monitoring start."""
        self.console.print(
            f"\n[bold green]{'='*60}[/bold green]\n"
            f"[bold green]🚀 WHALE WATCHER STARTED[/bold green]\n"
            f"[bold green]{'='*60}[/bold green]\n"
            f"[green]Monitoring:[/green] {market_count} markets\n"
            f"[green]Interval:[/green] {interval} seconds\n"
            f"[green]Min Trade Size:[/green] ${min_trade_size:,.0f} USD\n"
            f"[green]Price Range:[/green] {min_price} - {max_price}\n"
            f"[bold green]{'='*60}[/bold green]\n"
        )

    def error(self, message: str) -> None:
        """Log an error."""
        self.console.print(f"[bold red]❌ ERROR:[/bold red] {message}")

    def info(self, message: str) -> None:
        """Log an info message."""
        self.console.print(f"[blue]ℹ️[/blue] {message}")

    def separator(self) -> None:
        """Print a separator line."""
        self.console.print(f"[dim]{'─'*60}[/dim]")
