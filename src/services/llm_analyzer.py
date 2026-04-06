"""
LLM analyzer service - analyzes whale trades using AI with tool-use.

Architecture:
1. Build context (trade info + historical signals)
2. Send to LLM with tool schemas (search_twitter, search_web, etc.)
3. LLM decides which tools to call (if any)
4. Execute tool calls, return results to LLM
5. LLM produces final analysis + JSON decision

The LLM controls which information sources to query based on the market type.
"""
import json
import logging
import re
from datetime import datetime
from typing import Optional

from openai import OpenAI

from src.config import get_settings
from src.models.trade import WhaleTrade
from src.models.decision import LLMDecision, TradeRecommendation, TradeAction, TraderCredibility
from src.models.anomaly_signal import AnomalySignal
from src.services.anomaly_detector import AnomalyDetector
from src.services.anomaly_history import AnomalyHistoryService
from src.services.tools import ToolRegistry
from src.prompts.whale_analyzer import WhaleAnalyzerPrompts

logger = logging.getLogger(__name__)

# Maximum tool-use rounds to prevent infinite loops
# 14 tools available; LLM can call multiple per round but may need
# several rounds for chain-of-investigation (search → discover → verify)
MAX_TOOL_ROUNDS = 5


class LLMAnalyzer:
    """Analyzes whale trades using LLM with function-calling tools."""

    def __init__(self):
        self.settings = get_settings()

        self.client = OpenAI(
            base_url=self.settings.llm_base_url,
            api_key=self.settings.gemini_api_key,
        )

        self.anomaly_detector = AnomalyDetector()
        self.prompts = WhaleAnalyzerPrompts()
        self.anomaly_history = AnomalyHistoryService(self.settings.db_path)

        # Tool registry — LLM decides which tools to call
        self.tool_registry = ToolRegistry(
            twitter_api_key=self.settings.twitter_api_key,
            tavily_api_key=self.settings.tavily_api_key,
            fred_api_key=self.settings.fred_api_key,
            polygon_api_key=self.settings.polygon_api_key,
            congress_api_key=self.settings.congress_api_key,
            etherscan_api_key=self.settings.etherscan_api_key,
            serper_api_key=self.settings.serper_api_key,
            telegram_api_id=self.settings.telegram_api_id,
            telegram_api_hash=self.settings.telegram_api_hash,
            telegram_session_string=self.settings.telegram_session_string,
            telegram_channels=self.settings.telegram_channels,
        )

        self._last_historical_signal_count = 0

    @property
    def last_historical_signal_count(self) -> int:
        return self._last_historical_signal_count

    # ================================================================
    # Response parsing
    # ================================================================

    # Fields that identify the final recommendation JSON (vs intermediate tool-call JSONs)
    _RECOMMENDATION_FIELDS = {"information_asymmetry_score", "confidence", "trader_credibility"}

    def _extract_json_from_response(self, response: str) -> Optional[dict]:
        """Extract the final recommendation JSON from LLM response text.

        When the response contains multiple JSON code blocks (e.g. an
        intermediate ANALYZE decision followed by the real assessment),
        prefer the block that contains recommendation-specific fields.
        Falls back to the last parseable block.
        """
        candidates: list[dict] = []
        for match in re.findall(r"```(?:json)?\s*([\s\S]*?)```", response):
            try:
                candidates.append(json.loads(match.strip()))
            except json.JSONDecodeError:
                continue

        if candidates:
            # Prefer the block that looks like a final recommendation
            for c in reversed(candidates):
                if c.keys() & self._RECOMMENDATION_FIELDS:
                    return c
            # No block has recommendation fields — return the last one
            return candidates[-1]

        # Try raw JSON
        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        return None

    def _parse_recommendation(self, json_data: dict) -> TradeRecommendation:
        """Parse JSON into TradeRecommendation."""
        action_str = json_data.get("action", "HOLD").upper()
        try:
            action = TradeAction(action_str)
        except ValueError:
            action = TradeAction.HOLD

        confidence = max(0.0, min(1.0, float(json_data.get("confidence", 0.0))))

        suggested_price = json_data.get("suggested_price")
        if suggested_price is not None:
            suggested_price = float(suggested_price)

        suggested_size = max(0.0, min(1.0, float(json_data.get("suggested_size_percent", 0.1))))

        insider_likelihood = max(0.0, min(1.0, float(json_data.get("information_asymmetry_score", 0.0))))

        credibility_str = json_data.get("trader_credibility", "UNKNOWN").upper()
        try:
            trader_credibility = TraderCredibility(credibility_str)
        except ValueError:
            trader_credibility = TraderCredibility.UNKNOWN

        return TradeRecommendation(
            action=action,
            outcome=str(json_data.get("outcome", "")),
            confidence=confidence,
            suggested_price=suggested_price,
            suggested_size_percent=suggested_size,
            reasoning=str(json_data.get("reasoning", "")),
            information_asymmetry_score=insider_likelihood,
            trader_credibility=trader_credibility,
            insider_evidence=str(json_data.get("insider_evidence", "")),
        )

    # ================================================================
    # Anomaly signal storage
    # ================================================================

    def _store_anomaly_signal_if_qualified(
        self,
        whale_trade: WhaleTrade,
        decision: LLMDecision,
    ) -> None:
        """Store anomaly signal if information asymmetry score qualifies."""
        rec = decision.recommendation

        if not self.anomaly_history.should_store_signal(rec.information_asymmetry_score):
            logger.debug(
                f"Signal not stored: IAS {rec.information_asymmetry_score:.2f} "
                f"below threshold"
            )
            return

        signal = AnomalySignal(
            id=whale_trade.id,
            market_id=whale_trade.market_id,
            market_question=whale_trade.market_question,
            market_slug=whale_trade.trade.slug,
            condition_id=whale_trade.trade.condition_id,
            transaction_hash=whale_trade.trade.transaction_hash,
            trade_timestamp=whale_trade.trade.timestamp,
            trade_side=whale_trade.trade.side,
            trade_price=whale_trade.trade.price,
            trade_size_usd=whale_trade.trade.usdc_size,
            trade_outcome=whale_trade.trade.outcome,
            trader_wallet=whale_trade.trade.proxy_wallet,
            trader_ranking=whale_trade.trader_ranking,
            trader_history=whale_trade.trader_history,
            information_asymmetry_score=rec.information_asymmetry_score,
            reasoning=rec.reasoning,
            insider_evidence=rec.insider_evidence,
            detected_at=whale_trade.detected_at,
        )

        stored = self.anomaly_history.store_signal(signal)
        if stored:
            logger.info(
                f"Stored anomaly signal: {whale_trade.market_question[:50]}... "
                f"IAS={rec.information_asymmetry_score:.0%}"
            )

    # ================================================================
    # Tool-use loop
    # ================================================================

    def _execute_tool_calls(self, tool_calls) -> list[dict]:
        """Execute tool calls from the LLM and return message dicts."""
        results = []
        for tc in tool_calls:
            fn_name = tc.function.name
            try:
                fn_args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                fn_args = {}

            logger.info(f"LLM requested tool: {fn_name}({fn_args})")
            output = self.tool_registry.call(fn_name, **fn_args)

            results.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": output,
            })
        return results

    async def analyze_whale_trade(self, whale_trade: WhaleTrade) -> LLMDecision:
        """
        Analyze a whale trade using LLM with tool-use.

        Flow:
        0. Pre-screening: lightweight check if signal is worth full analysis
        1. Build initial context (trade + historical signals)
        2. Send to LLM with available tool schemas
        3. If LLM requests tools → execute → return results → repeat (up to MAX_TOOL_ROUNDS)
        4. Parse final text response for JSON decision
        """
        # Build context
        trade_context = self.anomaly_detector.format_for_llm(whale_trade)

        historical_context = ""
        historical_signals = self.anomaly_history.get_signals_for_market(
            whale_trade.market_id, top_recent=5, top_likelihood=5,
        )
        self._last_historical_signal_count = len(historical_signals)
        if historical_signals:
            historical_context = self.anomaly_history.format_historical_signals_context(historical_signals)
            logger.info(f"Found {len(historical_signals)} historical anomaly signals for market")

        # Build initial messages
        system_prompt = self.prompts.system_prompt()
        user_prompt = self.prompts.analyze_whale_trade(trade_context, historical_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Tool schemas (empty list if no tools available)
        tool_schemas = self.tool_registry.openai_tool_schemas()

        try:
            analysis_text = ""

            # Tool-use loop
            for round_idx in range(MAX_TOOL_ROUNDS + 1):
                # Call LLM
                call_kwargs = {
                    "model": self.settings.llm_model,
                    "messages": messages,
                }
                if tool_schemas and round_idx < MAX_TOOL_ROUNDS:
                    call_kwargs["tools"] = tool_schemas
                    call_kwargs["tool_choice"] = "auto"

                response = self.client.chat.completions.create(**call_kwargs)
                msg = response.choices[0].message

                # If LLM wants to call tools
                if msg.tool_calls:
                    logger.info(
                        f"Round {round_idx + 1}: LLM requested "
                        f"{len(msg.tool_calls)} tool call(s)"
                    )

                    # Append assistant message with tool calls
                    messages.append(msg.model_dump())

                    # Execute tools and append results
                    tool_results = self._execute_tool_calls(msg.tool_calls)
                    messages.extend(tool_results)

                    continue  # Next round — LLM processes tool results

                # No tool calls — final response
                analysis_text = msg.content or ""
                logger.info(
                    f"Analysis complete after {round_idx + 1} round(s) "
                    f"({len(analysis_text)} chars)"
                )
                break

            # Parse JSON decision from final response
            json_data = self._extract_json_from_response(analysis_text)

            if json_data:
                # Check if LLM decided to skip (pre-screening in prompt)
                if json_data.get("action") == "SKIP":
                    reason = json_data.get("reason", "not in scope")
                    logger.info(
                        f"⏭️ Pre-screening SKIP: {reason} "
                        f"(market: {whale_trade.market_question[:40]}...)"
                    )
                    return LLMDecision(
                        whale_trade_id=whale_trade.id,
                        market_id=whale_trade.market_id,
                        analysis=f"Pre-screening: {reason}",
                        recommendation=TradeRecommendation(
                            action=TradeAction.HOLD,
                            outcome="",
                            confidence=0.0,
                            reasoning=f"Signal filtered: {reason}",
                        ),
                    )

                recommendation = self._parse_recommendation(json_data)
            else:
                logger.warning("Could not parse LLM response as JSON, defaulting to HOLD")
                recommendation = TradeRecommendation(
                    action=TradeAction.HOLD,
                    outcome="",
                    confidence=0.0,
                    reasoning="Failed to parse LLM response",
                )

            decision = LLMDecision(
                whale_trade_id=whale_trade.id,
                market_id=whale_trade.market_id,
                analysis=analysis_text,
                recommendation=recommendation,
            )

            self._store_anomaly_signal_if_qualified(whale_trade, decision)
            return decision

        except Exception as e:
            logger.error(f"Error in LLM analysis: {e}")
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

    # ================================================================
    # Report formatting (unchanged)
    # ================================================================

    def format_full_report(
        self,
        whale_trade: WhaleTrade,
        decision: LLMDecision,
        historical_signal_count: int = 0,
    ) -> str:
        """Format a complete analysis report."""
        trade = whale_trade.trade
        rec = decision.recommendation

        prices_str = ""
        if whale_trade.market_outcomes and whale_trade.market_outcome_prices:
            prices_str = " | ".join([
                f"{o}: {p:.1%}"
                for o, p in zip(whale_trade.market_outcomes, whale_trade.market_outcome_prices)
            ])

        action_indicator = {
            TradeAction.BUY: "🟢 BUY",
            TradeAction.SELL: "🔴 SELL",
            TradeAction.HOLD: "⚪ HOLD",
        }

        ias = rec.information_asymmetry_score
        if ias >= 0.7:
            insider_indicator = f"🔴 高信息不对称 ({ias:.0%})"
        elif ias >= 0.4:
            insider_indicator = f"🟡 中等信息不对称 ({ias:.0%})"
        else:
            insider_indicator = f"🟢 低信息不对称 ({ias:.0%})"

        rank_num = whale_trade.trader_ranking.rank if whale_trade.trader_ranking and whale_trade.trader_ranking.rank else None
        credibility_indicators = {
            TraderCredibility.HIGH: f"🏆 高可信度 (#{rank_num})" if rank_num else "🏆 高可信度",
            TraderCredibility.MEDIUM: f"⭐ 中等可信度 (#{rank_num})" if rank_num else "⭐ 中等可信度",
            TraderCredibility.LOW: f"📉 低可信度 (#{rank_num})" if rank_num else "📉 低可信度",
            TraderCredibility.UNKNOWN: "❓ 未知 (未上榜)",
        }
        credibility_str = credibility_indicators.get(rec.trader_credibility, "❓ 未知")

        trader_ranking_str = ""
        if whale_trade.trader_ranking:
            tr = whale_trade.trader_ranking
            rank_str = f"#{tr.rank}" if tr.rank else "未上榜"
            pnl_str = f"${tr.pnl:,.2f}" if tr.pnl else "N/A"
            trader_ranking_str = f"| **交易者排名** | {rank_str} (PnL: {pnl_str}) |"

        historical_info = ""
        if historical_signal_count > 0:
            historical_info = f"\n**参考历史异常信号**: {historical_signal_count} 笔 (已综合分析)"

        report = f"""
{'='*70}
# 🐋 鲸鱼交易分析报告
{'='*70}

**生成时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{historical_info}

## 交易摘要

| 项目 | 详情 |
|------|------|
| **市场** | {whale_trade.market_question} |
| **交易金额** | ${trade.usdc_size:,.2f} USDC |
| **交易方向** | BUY {trade.outcome} Token ({'看多' if trade.outcome == 'Yes' else '看空'}) |
| **交易价格** | {trade.price:.4f} ({trade.price:.1%}) |
| **当前赔率** | {prices_str} |
| **交易时间** | {datetime.fromtimestamp(trade.timestamp).strftime('%Y-%m-%d %H:%M:%S') if trade.timestamp else 'N/A'} |
{trader_ranking_str}

{'='*70}

{decision.analysis}

{'='*70}
## 🔍 信息不对称评估
{'='*70}

| 项目 | 评估 |
|------|------|
| **信息不对称程度** | {insider_indicator} |
| **交易者可信度** | {credibility_str} |

**关键证据**: {rec.insider_evidence or '无明确证据'}

**推理过程**: {rec.reasoning}

{'='*70}
⚠️ 免责声明：本报告由AI生成，仅供参考，不构成投资建议。
{'='*70}
"""
        return report
