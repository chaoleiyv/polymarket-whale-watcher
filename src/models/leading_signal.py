"""Data models for leading signal detection - price moves before news."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class SignalType(str, Enum):
    """Type of price volatility signal."""
    LEADING_SIGNAL = "LEADING_SIGNAL"  # Price moved before news
    NEWS_DRIVEN = "NEWS_DRIVEN"  # Price reacted to news
    SOCIAL_DRIVEN = "SOCIAL_DRIVEN"  # Price driven by social media
    SPECULATION = "SPECULATION"  # No clear information source


@dataclass
class LeadingSignal:
    """
    A case where price movement preceded public news.

    This is used to build a dataset of "price leads news" events
    for research purposes.
    """
    # Basic info
    id: str
    market_id: str
    market_question: str

    # Price movement details
    price_change_percent: float  # e.g., 0.25 for 25%
    direction: str  # "UP" or "DOWN"
    start_price: float
    end_price: float
    window_seconds: int

    # Timing
    detected_at: str  # ISO format timestamp
    volatility_detected_at: str  # When price volatility was detected

    # LLM analysis results
    signal_type: SignalType
    confidence: float  # 0-1
    is_leading_signal: bool

    # News analysis
    news_found: bool
    earliest_news_time: Optional[str] = None  # ISO format
    key_news_headlines: List[str] = field(default_factory=list)

    # Social media analysis
    earliest_social_time: Optional[str] = None  # ISO format
    key_social_posts: List[str] = field(default_factory=list)

    # Time advantage
    time_advantage_minutes: int = 0  # How many minutes price led news

    # Analysis
    reasoning: str = ""
    potential_information_source: str = ""

    # Full LLM analysis text
    full_analysis: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "market_id": self.market_id,
            "market_question": self.market_question,
            "price_change_percent": self.price_change_percent,
            "direction": self.direction,
            "start_price": self.start_price,
            "end_price": self.end_price,
            "window_seconds": self.window_seconds,
            "detected_at": self.detected_at,
            "volatility_detected_at": self.volatility_detected_at,
            "signal_type": self.signal_type.value if isinstance(self.signal_type, SignalType) else self.signal_type,
            "confidence": self.confidence,
            "is_leading_signal": self.is_leading_signal,
            "news_found": self.news_found,
            "earliest_news_time": self.earliest_news_time,
            "key_news_headlines": self.key_news_headlines,
            "earliest_social_time": self.earliest_social_time,
            "key_social_posts": self.key_social_posts,
            "time_advantage_minutes": self.time_advantage_minutes,
            "reasoning": self.reasoning,
            "potential_information_source": self.potential_information_source,
            "full_analysis": self.full_analysis,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LeadingSignal":
        """Create from dictionary."""
        signal_type = data.get("signal_type", "SPECULATION")
        if isinstance(signal_type, str):
            try:
                signal_type = SignalType(signal_type)
            except ValueError:
                signal_type = SignalType.SPECULATION

        return cls(
            id=data["id"],
            market_id=data["market_id"],
            market_question=data["market_question"],
            price_change_percent=data["price_change_percent"],
            direction=data["direction"],
            start_price=data["start_price"],
            end_price=data["end_price"],
            window_seconds=data["window_seconds"],
            detected_at=data["detected_at"],
            volatility_detected_at=data["volatility_detected_at"],
            signal_type=signal_type,
            confidence=data.get("confidence", 0.0),
            is_leading_signal=data.get("is_leading_signal", False),
            news_found=data.get("news_found", False),
            earliest_news_time=data.get("earliest_news_time"),
            key_news_headlines=data.get("key_news_headlines", []),
            earliest_social_time=data.get("earliest_social_time"),
            key_social_posts=data.get("key_social_posts", []),
            time_advantage_minutes=data.get("time_advantage_minutes", 0),
            reasoning=data.get("reasoning", ""),
            potential_information_source=data.get("potential_information_source", ""),
            full_analysis=data.get("full_analysis", ""),
        )
