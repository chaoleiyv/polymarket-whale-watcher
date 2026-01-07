"""LLM analyzer service - analyzes whale trades using AI."""
import json
import logging
import os
import re
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types

from src.config import get_settings
from src.models.trade import WhaleTrade
from src.models.decision import LLMDecision, TradeRecommendation, TradeAction, TraderCredibility
from src.services.anomaly_detector import AnomalyDetector
from src.prompts.whale_analyzer import WhaleAnalyzerPrompts

logger = logging.getLogger(__name__)


class LLMAnalyzer:
    """
    Analyzes whale trades using LLM (Google Gemini models).

    Combines trade context with superforecaster methodology to generate
    comprehensive analysis reports with trading recommendations.
    """

    def __init__(self):
        self.settings = get_settings()

        # Configure Gemini API using new client SDK
        os.environ["GOOGLE_API_KEY"] = self.settings.gemini_api_key
        self.client = genai.Client()

        self.anomaly_detector = AnomalyDetector()
        self.prompts = WhaleAnalyzerPrompts()

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
            # Find JSON-like content
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        return None

    def _parse_recommendation(self, json_data: dict) -> TradeRecommendation:
        """
        Parse JSON data into TradeRecommendation.

        Args:
            json_data: Parsed JSON from LLM

        Returns:
            TradeRecommendation object
        """
        action_str = json_data.get("action", "HOLD").upper()
        try:
            action = TradeAction(action_str)
        except ValueError:
            action = TradeAction.HOLD

        confidence = float(json_data.get("confidence", 0.0))
        # Clamp confidence to valid range
        confidence = max(0.0, min(1.0, confidence))

        suggested_price = json_data.get("suggested_price")
        if suggested_price is not None:
            suggested_price = float(suggested_price)

        suggested_size = float(json_data.get("suggested_size_percent", 0.1))
        suggested_size = max(0.0, min(1.0, suggested_size))

        # Parse insider trading assessment fields
        insider_likelihood = float(json_data.get("insider_trading_likelihood", 0.0))
        insider_likelihood = max(0.0, min(1.0, insider_likelihood))

        credibility_str = json_data.get("trader_credibility", "UNKNOWN").upper()
        try:
            trader_credibility = TraderCredibility(credibility_str)
        except ValueError:
            trader_credibility = TraderCredibility.UNKNOWN

        insider_evidence = str(json_data.get("insider_evidence", ""))

        return TradeRecommendation(
            action=action,
            outcome=str(json_data.get("outcome", "")),
            confidence=confidence,
            suggested_price=suggested_price,
            suggested_size_percent=suggested_size,
            reasoning=str(json_data.get("reasoning", "")),
            insider_trading_likelihood=insider_likelihood,
            trader_credibility=trader_credibility,
            insider_evidence=insider_evidence,
        )

    async def analyze_whale_trade(self, whale_trade: WhaleTrade) -> LLMDecision:
        """
        Analyze a whale trade using LLM.

        Args:
            whale_trade: The whale trade to analyze

        Returns:
            LLMDecision with analysis and recommendation
        """
        # Format trade context for LLM
        trade_context = self.anomaly_detector.format_for_llm(whale_trade)

        # Build prompt (Gemini uses single prompt with system instruction)
        system_prompt = self.prompts.system_prompt()
        user_prompt = self.prompts.analyze_whale_trade(trade_context)
        full_prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

        try:
            # Call Gemini API with Google Search tool enabled
            response = self.client.models.generate_content(
                model=self.settings.llm_model,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())],
                ),
            )

            analysis_text = response.text
            logger.debug(f"LLM response: {analysis_text[:500]}...")

            # Extract JSON from response
            json_data = self._extract_json_from_response(analysis_text)

            if json_data:
                recommendation = self._parse_recommendation(json_data)
            else:
                # Default to HOLD if we can't parse the response
                logger.warning("Could not parse LLM response as JSON, defaulting to HOLD")
                recommendation = TradeRecommendation(
                    action=TradeAction.HOLD,
                    outcome="",
                    confidence=0.0,
                    reasoning="Failed to parse LLM response",
                )

            return LLMDecision(
                whale_trade_id=whale_trade.id,
                market_id=whale_trade.market_id,
                analysis=analysis_text,
                recommendation=recommendation,
            )

        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
            # Return a safe default decision
            return LLMDecision(
                whale_trade_id=whale_trade.id,
                market_id=whale_trade.market_id,
                analysis=f"Error during analysis: {str(e)}",
                recommendation=TradeRecommendation(
                    action=TradeAction.HOLD,
                    outcome="",
                    confidence=0.0,
                    reasoning=f"Analysis failed: {str(e)}",
                ),
            )

    def format_full_report(self, whale_trade: WhaleTrade, decision: LLMDecision) -> str:
        """
        Format a complete analysis report with trade info, analysis, and decision.

        Args:
            whale_trade: The whale trade
            decision: The LLM decision

        Returns:
            Formatted report string
        """
        trade = whale_trade.trade
        rec = decision.recommendation

        # Format outcome prices
        prices_str = ""
        if whale_trade.market_outcomes and whale_trade.market_outcome_prices:
            prices_str = " | ".join([
                f"{o}: {p:.1%}"
                for o, p in zip(whale_trade.market_outcomes, whale_trade.market_outcome_prices)
            ])

        # Action emoji and color indicator
        action_indicator = {
            TradeAction.BUY: "🟢 BUY",
            TradeAction.SELL: "🔴 SELL",
            TradeAction.HOLD: "⚪ HOLD",
        }

        # Insider trading likelihood indicator
        insider_likelihood = rec.insider_trading_likelihood
        if insider_likelihood >= 0.7:
            insider_indicator = f"🔴 高度可疑 ({insider_likelihood:.0%})"
        elif insider_likelihood >= 0.4:
            insider_indicator = f"🟡 中等可能 ({insider_likelihood:.0%})"
        else:
            insider_indicator = f"🟢 普通交易 ({insider_likelihood:.0%})"

        # Trader credibility indicator
        credibility_indicators = {
            TraderCredibility.HIGH: "🏆 高可信度 (前100名)",
            TraderCredibility.MEDIUM: "⭐ 中等可信度 (100-500名)",
            TraderCredibility.LOW: "📉 低可信度 (500名+)",
            TraderCredibility.UNKNOWN: "❓ 未知 (未上榜)",
        }
        credibility_str = credibility_indicators.get(rec.trader_credibility, "❓ 未知")

        # Trader ranking info
        trader_ranking_str = ""
        if whale_trade.trader_ranking:
            tr = whale_trade.trader_ranking
            rank_str = f"#{tr.rank}" if tr.rank else "未上榜"
            pnl_str = f"${tr.pnl:,.2f}" if tr.pnl else "N/A"
            trader_ranking_str = f"| **交易者排名** | {rank_str} (PnL: {pnl_str}) |"

        report = f"""
{'='*70}
# 🐋 鲸鱼交易分析报告
{'='*70}

**生成时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

## 交易摘要

| 项目 | 详情 |
|------|------|
| **市场** | {whale_trade.market_question} |
| **交易金额** | ${trade.usdc_size:,.2f} USDC |
| **交易方向** | {trade.side} |
| **交易价格** | {trade.price:.4f} ({trade.price:.1%}) |
| **交易结果** | {trade.outcome} |
| **当前赔率** | {prices_str} |
| **交易时间** | {datetime.fromtimestamp(trade.timestamp).strftime('%Y-%m-%d %H:%M:%S') if trade.timestamp else 'N/A'} |
{trader_ranking_str}

{'='*70}

{decision.analysis}

{'='*70}
## 🔍 内幕交易评估
{'='*70}

| 项目 | 评估 |
|------|------|
| **内幕交易可能性** | {insider_indicator} |
| **交易者可信度** | {credibility_str} |

**关键证据**: {rec.insider_evidence or '无明确证据'}

{'='*70}
## 📊 决策摘要
{'='*70}

| 项目 | 建议 |
|------|------|
| **操作建议** | {action_indicator.get(rec.action, '⚪ HOLD')} |
| **目标结果** | {rec.outcome or 'N/A'} |
| **信心程度** | {rec.confidence:.1%} |
| **建议仓位** | {rec.suggested_size_percent:.1%} |
| **建议价格** | {f'{rec.suggested_price:.4f}' if rec.suggested_price else 'Market'} |

**决策理由**: {rec.reasoning}

{'='*70}
⚠️ 免责声明：本报告由AI生成，仅供参考，不构成投资建议。
预测市场具有高风险，请基于自身判断谨慎决策。
{'='*70}
"""
        return report
