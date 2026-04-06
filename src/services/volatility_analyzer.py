"""Volatility analyzer service - analyzes price volatility using AI to detect leading signals."""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI

from src.config import get_settings
from src.models.leading_signal import LeadingSignal, SignalType
from src.services.price_monitor import VolatilityAlert
from src.services.twitter_search import TwitterSearchService
from src.prompts.volatility_analyzer import VolatilityAnalyzerPrompts

logger = logging.getLogger(__name__)

# Directory for storing leading signals dataset
LEADING_SIGNALS_DIR = Path(__file__).parent.parent.parent / "leading_signals"


class VolatilityAnalyzer:
    """
    Analyzes price volatility events using LLM to detect "price leads news" signals.

    Uses Tavily web search and Twitter to verify whether a price movement
    preceded public news, building a dataset of leading signals.
    """

    def __init__(self):
        self.settings = get_settings()

        # Configure OpenAI-compatible API client
        self.client = OpenAI(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.gemini_api_key,
        )

        self.prompts = VolatilityAnalyzerPrompts()
        self.twitter_search = TwitterSearchService(api_key=self.settings.twitter_api_key)
        from src.services.web_search import WebSearchService
        self.web_search = WebSearchService(
            tavily_api_key=self.settings.tavily_api_key,
            serper_api_key=self.settings.serper_api_key,
        )

        # Ensure storage directory exists
        LEADING_SIGNALS_DIR.mkdir(parents=True, exist_ok=True)

    def _extract_json_from_response(self, response: str) -> Optional[dict]:
        """
        Extract JSON from LLM response.

        Args:
            response: The LLM response text

        Returns:
            Parsed JSON dict or None
        """
        # Try to find JSON in code blocks
        json_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_pattern, response)

        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue

        # Try to find raw JSON
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        return None

    def _parse_signal_type(self, type_str: str) -> SignalType:
        """Parse signal type string to enum."""
        try:
            return SignalType(type_str.upper())
        except ValueError:
            return SignalType.SPECULATION

    def _store_leading_signal(self, signal: LeadingSignal) -> str:
        """
        Store a leading signal to the dataset.

        Args:
            signal: The leading signal to store

        Returns:
            Path to the stored file
        """
        # Create filename with timestamp and market info
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        market_slug = re.sub(r'[^\w\s-]', '', signal.market_question)[:40]
        market_slug = re.sub(r'\s+', '_', market_slug)

        filename = f"{timestamp}_{signal.signal_type.value}_{market_slug}.json"
        filepath = LEADING_SIGNALS_DIR / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(signal.to_dict(), f, ensure_ascii=False, indent=2)

        return str(filepath)

    def _store_all_signals_index(self, signal: LeadingSignal) -> None:
        """
        Append signal to the master index file for easy querying.

        Args:
            signal: The signal to append
        """
        index_file = LEADING_SIGNALS_DIR / "signals_index.jsonl"

        with open(index_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(signal.to_dict(), ensure_ascii=False) + "\n")

    async def analyze_volatility(self, alert: VolatilityAlert) -> Optional[LeadingSignal]:
        """
        Analyze a price volatility event to determine if it's a leading signal.

        Args:
            alert: The volatility alert to analyze

        Returns:
            LeadingSignal if analysis successful, None otherwise
        """
        logger.info(
            f"Analyzing volatility: {alert.market_question[:50]}... "
            f"{alert.direction} {abs(alert.price_change_percent):.1%}"
        )

        # Search web (Tavily) for news verification
        web_search_context = ""
        if self.web_search.is_available():
            logger.info(f"Searching web for: {alert.market_question[:50]}...")
            web_result = self.web_search.search_for_market(
                market_question=alert.market_question,
                max_results=5,
            )
            if web_result and "unavailable" not in web_result.lower():
                web_search_context = web_result
                logger.info("Web search (Tavily) completed")

        # Search Twitter for social sentiment
        twitter_context = ""
        if self.twitter_search.is_available():
            logger.info(f"Searching Twitter for: {alert.market_question[:50]}...")
            twitter_result = self.twitter_search.search_for_market(
                market_question=alert.market_question,
                limit=10,
            )
            if twitter_result and "unavailable" not in twitter_result.lower():
                twitter_context = twitter_result
                logger.info("Twitter search completed")

        # Build prompts
        system_prompt = self.prompts.system_prompt()
        user_prompt = self.prompts.analyze_volatility(
            market_question=alert.market_question,
            price_change_percent=alert.price_change_percent,
            direction=alert.direction,
            start_price=alert.start_price,
            end_price=alert.end_price,
            window_seconds=alert.window_seconds,
            detected_at=alert.detected_at,
            twitter_context=twitter_context,
            web_search_context=web_search_context,
        )

        try:
            # Call LLM API
            response = self.client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            analysis_text = response.choices[0].message.content
            logger.debug(f"LLM response: {analysis_text[:500]}...")

            # Extract JSON from response
            json_data = self._extract_json_from_response(analysis_text)

            if not json_data:
                logger.warning("Could not parse LLM response as JSON")
                return None

            # Create LeadingSignal from response
            signal_id = f"vol_{alert.market_id}_{int(datetime.now().timestamp())}"

            signal = LeadingSignal(
                id=signal_id,
                market_id=alert.market_id,
                market_question=alert.market_question,
                price_change_percent=alert.price_change_percent,
                direction=alert.direction,
                start_price=alert.start_price,
                end_price=alert.end_price,
                window_seconds=alert.window_seconds,
                detected_at=datetime.utcnow().isoformat(),
                volatility_detected_at=alert.detected_at,
                signal_type=self._parse_signal_type(json_data.get("signal_type", "SPECULATION")),
                confidence=float(json_data.get("confidence", 0.0)),
                is_leading_signal=bool(json_data.get("is_leading_signal", False)),
                news_found=bool(json_data.get("news_found", False)),
                earliest_news_time=json_data.get("earliest_news_time"),
                key_news_headlines=json_data.get("key_news_headlines", []),
                earliest_social_time=json_data.get("earliest_social_time"),
                key_social_posts=json_data.get("key_social_posts", []),
                time_advantage_minutes=int(json_data.get("time_advantage_minutes", 0)),
                reasoning=str(json_data.get("reasoning", "")),
                potential_information_source=str(json_data.get("potential_information_source", "")),
                full_analysis=analysis_text,
            )

            # Store the signal
            filepath = self._store_leading_signal(signal)
            self._store_all_signals_index(signal)

            # Log result
            if signal.is_leading_signal:
                logger.warning(
                    f"🚨 LEADING SIGNAL DETECTED: {alert.market_question[:50]}... "
                    f"Time advantage: {signal.time_advantage_minutes} minutes"
                )
            else:
                logger.info(
                    f"Volatility analyzed: {signal.signal_type.value} "
                    f"(confidence: {signal.confidence:.1%})"
                )

            logger.info(f"Signal stored: {filepath}")

            return signal

        except Exception as e:
            logger.error(f"Error analyzing volatility: {e}")
            return None

    def format_signal_report(self, signal: LeadingSignal) -> str:
        """
        Format a leading signal as a readable report.

        Args:
            signal: The signal to format

        Returns:
            Formatted report string
        """
        direction_cn = "上涨" if signal.direction == "UP" else "下跌"
        signal_type_cn = {
            SignalType.LEADING_SIGNAL: "🚨 领先信号（价格早于新闻）",
            SignalType.NEWS_DRIVEN: "📰 新闻驱动",
            SignalType.SOCIAL_DRIVEN: "🐦 社交驱动",
            SignalType.SPECULATION: "💭 投机波动",
        }

        news_headlines = "\n".join([f"  - {h}" for h in signal.key_news_headlines]) or "  无"
        social_posts = "\n".join([f"  - {p}" for p in signal.key_social_posts]) or "  无"

        report = f"""
{'='*70}
# 📊 价格波动分析报告
{'='*70}

**分析时间**: {signal.detected_at}

## 波动详情

| 项目 | 详情 |
|------|------|
| **市场** | {signal.market_question} |
| **价格变动** | {direction_cn} {abs(signal.price_change_percent):.1%} |
| **起始价格** | {signal.start_price:.2%} |
| **结束价格** | {signal.end_price:.2%} |
| **时间窗口** | {signal.window_seconds // 60} 分钟 |

{'='*70}
## 🔍 分析结果
{'='*70}

| 项目 | 结果 |
|------|------|
| **信号类型** | {signal_type_cn.get(signal.signal_type, '未知')} |
| **置信度** | {signal.confidence:.1%} |
| **是否领先信号** | {'✅ 是' if signal.is_leading_signal else '❌ 否'} |
| **时间优势** | {signal.time_advantage_minutes} 分钟 |

**最早新闻时间**: {signal.earliest_news_time or 'N/A'}
**最早社交时间**: {signal.earliest_social_time or 'N/A'}

## 关键新闻
{news_headlines}

## 关键社交帖子
{social_posts}

## 分析理由
{signal.reasoning}

## 推测信息来源
{signal.potential_information_source or '未知'}

{'='*70}
{signal.full_analysis}
{'='*70}
"""
        return report

    def get_leading_signals_stats(self) -> dict:
        """
        Get statistics about collected leading signals.

        Returns:
            Dictionary with stats
        """
        index_file = LEADING_SIGNALS_DIR / "signals_index.jsonl"

        if not index_file.exists():
            return {
                "total_signals": 0,
                "leading_signals": 0,
                "news_driven": 0,
                "social_driven": 0,
                "speculation": 0,
            }

        stats = {
            "total_signals": 0,
            "leading_signals": 0,
            "news_driven": 0,
            "social_driven": 0,
            "speculation": 0,
            "avg_time_advantage_minutes": 0,
        }

        time_advantages = []

        try:
            with open(index_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        stats["total_signals"] += 1

                        signal_type = data.get("signal_type", "SPECULATION")
                        if signal_type == "LEADING_SIGNAL":
                            stats["leading_signals"] += 1
                            time_advantages.append(data.get("time_advantage_minutes", 0))
                        elif signal_type == "NEWS_DRIVEN":
                            stats["news_driven"] += 1
                        elif signal_type == "SOCIAL_DRIVEN":
                            stats["social_driven"] += 1
                        else:
                            stats["speculation"] += 1

            if time_advantages:
                stats["avg_time_advantage_minutes"] = sum(time_advantages) / len(time_advantages)

        except Exception as e:
            logger.error(f"Error reading signals index: {e}")

        return stats
