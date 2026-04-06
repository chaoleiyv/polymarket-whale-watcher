"""Backtest script - analyze historical trades for a specific user."""
import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from openai import OpenAI
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Settings from .env
MIN_TRADE_SIZE_USD = float(os.getenv("MIN_TRADE_SIZE_USD", 5000))
MIN_PRICE = float(os.getenv("MIN_PRICE", 0))
MAX_PRICE = float(os.getenv("MAX_PRICE", 0.9))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.0-flash")

# Target user
TARGET_USER = "0x31a56e9e690c621ed21de08cb559e9524cdb8ed9"

# API endpoints
DATA_API_URL = "https://data-api.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"

# Reports directory
REPORTS_DIR = Path(__file__).parent / "backtest_reports"
REPORTS_DIR.mkdir(exist_ok=True)


class BacktestAnalyzer:
    """Analyzer for backtesting historical trades."""

    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        self.genai_client = OpenAI(
            base_url="http://apicz.boyuerichdata.com/v1/",
            api_key=GEMINI_API_KEY,
        )

    async def close(self):
        await self.client.aclose()

    async def fetch_user_trades(self, wallet_address: str, limit: int = 500) -> list:
        """Fetch all trades for a user."""
        try:
            params = {
                "user": wallet_address,
                "limit": limit,
            }
            response = await self.client.get(f"{DATA_API_URL}/trades", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching trades: {e}")
            return []

    async def fetch_market_info(self, condition_id: str) -> Optional[dict]:
        """Fetch market information."""
        try:
            response = await self.client.get(f"{GAMMA_API_URL}/markets/{condition_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.debug(f"Error fetching market info for {condition_id}: {e}")
            return None

    async def fetch_trader_ranking(self, wallet_address: str) -> Optional[dict]:
        """Fetch trader ranking from leaderboard."""
        try:
            params = {
                "user": wallet_address,
                "timePeriod": "ALL",
                "orderBy": "PNL",
            }
            response = await self.client.get(f"{DATA_API_URL}/v1/leaderboard", params=params)
            response.raise_for_status()
            data = response.json()
            if data and len(data) > 0:
                return data[0]
            return None
        except Exception as e:
            logger.debug(f"Error fetching ranking: {e}")
            return None

    def filter_trades(self, trades: list) -> list:
        """Filter trades by criteria."""
        filtered = []
        for trade in trades:
            usdc_size = float(trade.get("usdcSize", 0) or 0)
            if usdc_size == 0:
                size = float(trade.get("size", 0) or 0)
                price = float(trade.get("price", 0) or 0)
                usdc_size = size * price

            price = float(trade.get("price", 0) or 0)

            if usdc_size >= MIN_TRADE_SIZE_USD and MIN_PRICE <= price <= MAX_PRICE:
                trade["_usdc_size"] = usdc_size
                filtered.append(trade)

        # Sort by timestamp (newest first)
        filtered.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
        return filtered

    def build_trade_context(self, trade: dict, market_info: Optional[dict], prior_trades: list = None) -> str:
        """Build trade context for LLM analysis with prior trades history."""
        usdc_size = trade.get("_usdc_size", 0)
        side = trade.get("side", "")
        price = float(trade.get("price", 0) or 0)
        outcome = trade.get("outcome", "")
        timestamp = trade.get("timestamp", 0)
        proxy_wallet = trade.get("proxyWallet", trade.get("maker", ""))

        # Market info
        market_question = trade.get("title", trade.get("marketTitle", "Unknown"))
        market_description = ""
        outcomes = []
        outcome_prices = []

        if market_info:
            market_question = market_info.get("question", market_question)
            market_description = market_info.get("description", "")
            outcomes = market_info.get("outcomes", [])
            outcome_prices = market_info.get("outcomePrices", [])

        # Format prices
        prices_str = ""
        if outcomes and outcome_prices:
            try:
                prices_str = ", ".join([f"{o}: {float(p):.4f}" for o, p in zip(outcomes, outcome_prices)])
            except:
                prices_str = "N/A"

        time_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else 'N/A'
        trade_date = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d') if timestamp else 'N/A'

        # Build prior trades history
        prior_trades_str = ""
        if prior_trades:
            prior_trades_str = "\n### 该交易者之前的交易记录（本次交易之前）\n"
            for i, pt in enumerate(prior_trades, 1):
                pt_time = datetime.fromtimestamp(pt.get("timestamp", 0)).strftime('%Y-%m-%d %H:%M:%S') if pt.get("timestamp") else 'N/A'
                pt_usdc = pt.get("_usdc_size", 0)
                pt_side = pt.get("side", "")
                pt_price = float(pt.get("price", 0) or 0)
                pt_outcome = pt.get("outcome", "")
                pt_market = pt.get("title", pt.get("marketTitle", "Unknown"))
                prior_trades_str += f"  {i}. [{pt_time}] {pt_side} ${pt_usdc:,.2f} @ {pt_price:.4f} - {pt_outcome} - {pt_market[:50]}\n"
        else:
            prior_trades_str = "\n### 该交易者之前的交易记录\n- 无之前的交易记录\n"

        return f"""
## 异常交易检测

### 交易信息
- 交易金额: ${usdc_size:,.2f} USDC
- 交易方向: {side}
- 交易价格: {price:.4f}
- 交易结果: {outcome}
- 交易时间: {time_str}
- 交易者钱包: {proxy_wallet}

### 交易者信息
- 无排行榜排名信息（未知）
- 无盈亏记录（未知）
{prior_trades_str}
### 市场信息
- 市场问题: {market_question}
- 市场描述: {market_description or 'N/A'}
- 可能结果: {', '.join(outcomes) if outcomes else 'N/A'}
- 当前价格: {prices_str or 'N/A'}

### 重要提示
- **搜索新闻时，只能使用 {trade_date} 及之前的新闻**
- 不要使用交易发生之后的任何信息

### 分析要点
1. 这笔大额交易 (${usdc_size:,.2f}) 表明交易者对 "{outcome}" 结果有很强的信心
2. 交易价格 {price:.4f} 说明市场尚未形成明确共识
3. 交易方向为 {side}，可能暗示内部信息或深度分析结论
4. 请分析交易者之前的交易记录，判断其交易模式和专业程度
"""

    def get_system_prompt(self, trade_date: str) -> str:
        """Get system prompt with Web Search enabled."""
        return f"""你是一位专业的预测市场分析师和内幕交易识别专家，专门分析 Polymarket 上的大额异常交易。

**你的核心任务**：验证一笔"疑似异常交易"是否真的是"内幕交易"（即交易者掌握了市场尚未反映的信息）。

## 严格时间限制（必须遵守！）
**交易发生时间**: {trade_date}

### 绝对禁止：
- ❌ 绝对禁止引用或提及任何 2026 年的新闻或事件
- ❌ 绝对禁止虚构、编造、推测任何新闻事件
- ❌ 绝对禁止假设交易之后发生了什么
- ❌ 绝对禁止使用"后续事件"、"之后发生"等表述

### 必须遵守：
- ✅ 只能搜索和使用 2025 年 12 月及之前的真实新闻
- ✅ 搜索关键词必须包含 "December 2025" 或 "2025"
- ✅ 只能基于 2025 年 12 月的信息进行分析
- ✅ 如果搜索不到相关新闻，必须如实说明"未找到相关新闻"

### 分析视角：
你必须假装现在是 2026 年 1 月 3 日，你不知道交易之后会发生什么。
你只能基于 2025 年 12 月及之前的公开信息来分析这笔交易。

## 你的工作流程

### 第一步：接收疑似异常交易信号
你会收到一笔被系统标记为"疑似异常"的交易，包含：
- 交易金额（$5,000+的大额交易）
- 交易方向（BUY/SELL）和价格
- 交易者之前的交易记录（如有）

### 第二步：获取市场信息
你会同时收到该交易对应的市场信息：
- 市场问题（预测的事件）
- 市场描述
- 当前各结果的价格/概率

### 第三步：使用 Web Search 验证（关键步骤！）
**你必须使用 Web 搜索来验证这笔交易是否基于真实信息：**
- 搜索委内瑞拉相关的 2025 年 12 月新闻
- 搜索关键词示例：
  - "Venezuela Maduro December 2025"
  - "委内瑞拉 马杜罗 2025年12月"
  - "Venezuela political news December 2025"
  - "Maduro opposition December 2025"
- 查找是否有可能影响马杜罗政权的重要信息
- **只报告你真实搜索到的新闻，不要编造任何内容！**

### 第四步：综合判断并生成报告
结合所有信息，判断：
- 这笔交易是"真正的内幕交易"还是"普通大额交易"
- 给出内幕交易可能性评分（0-100%）
- 提供跟单建议（BUY/SELL/HOLD）

## 内幕交易识别框架

1. **交易者历史行为分析**：
   - 查看交易者之前的交易记录
   - 分析其交易模式（是否有规律性加仓、是否集中在特定领域）
   - 判断其专业程度

2. **信息验证**：
   - 搜索是否有支持该交易方向的 2025 年 12 月新闻
   - 判断市场是否已经反映了这些信息
   - 评估信息的时效性和可靠性

3. **综合判断标准**：
   - 有历史交易记录 + 有最新未反映信息 = 可能是内幕交易 (0.6+)
   - 有历史记录但无明显信息 = 可能基于深度分析 (0.4-0.6)
   - 无历史记录 + 无信息 = 普通投机交易 (<0.4)

**重要原则**：
- **务必使用 Web Search！** 不要仅依赖你的历史知识
- **只搜索 2025 年 12 月的新闻！**
- **只报告真实搜索到的新闻，绝对不能虚构！**
- 如果搜索不到支持信息，内幕交易可能性应该降低
- 信心不足时建议观望（HOLD）"""

    def get_analysis_prompt(self, trade_context: str, trade_date: str) -> str:
        """Get analysis prompt with Web Search instructions."""
        return f"""{trade_context}

---

# 鲸鱼交易验证报告

你收到了一笔**疑似异常交易信号**，请按照以下步骤验证这是否是"真正的内幕交易"。

**重要：只搜索 2025 年 12 月的委内瑞拉新闻！不要虚构任何内容！**

---

## 第一步：Web 搜索验证（必须执行！）

**请立即使用 Web Search 搜索以下内容：**

1. 搜索委内瑞拉 2025 年 12 月的政治新闻
2. 搜索马杜罗政权 2025 年 12 月的动态
3. 搜索委内瑞拉反对派 2025 年 12 月的活动

**必须使用的搜索关键词**（包含 December 2025）：
- "Venezuela Maduro December 2025"
- "Venezuela opposition December 2025"
- "Maduro government December 2025"
- "Venezuela political crisis December 2025"

**搜索结果摘要**：
（请在此列出你**真实搜索到**的 2025 年 12 月新闻，包括：
- 新闻标题
- 来源网站
- 发布日期（必须是 2025 年 12 月或之前）
- 主要内容摘要

**严格要求**：
- ❌ 绝对不能编造任何新闻
- ❌ 绝对不能引用任何 2026 年的新闻或事件
- ❌ 绝对不能推测交易之后发生了什么
- ✅ 如果搜索不到 2025 年 12 月的相关新闻，请如实说明"未找到相关新闻"）

---

## 第二步：交易者历史行为分析

### 2.1 交易记录分析
- 查看交易者之前的交易记录
- 分析其交易模式（加仓频率、金额变化、价格变化）
- 判断其是否在有系统性地建仓

### 2.2 交易时机分析
- 这笔交易发生的时间点是否异常？
- **结合 2025 年 12 月的搜索结果**：是否有 2025 年 12 月的新闻可能触发了这笔交易？
- 基于 2025 年 12 月的公开信息，交易者是否可能掌握了市场尚未反映的信息？

---

## 第三步：市场信息验证（基于 2025 年 12 月信息）

### 3.1 当前市场状态
- 基于 2025 年 12 月的公开信息，市场价格是否合理？
- 交易价格与市场预期的关系如何？

### 3.2 信息差分析（只基于 2025 年 12 月信息）
- **关键问题**：2025 年 12 月的新闻是否支持这笔交易的方向？
- 这些信息是否已被市场完全定价？
- 基于 2025 年 12 月的公开信息，是否存在信息差？

---

## 第四步：内幕交易判定（基于 2025 年 12 月信息）

### 4.1 内幕交易可能性评估
**只基于 2025 年 12 月及之前的公开信息**，判断这笔交易是：
- **可能的内幕交易**：交易者可能掌握了 2025 年 12 月公开信息之外的信息
- **深度分析交易**：交易者基于 2025 年 12 月公开信息的深度分析
- **普通投机交易**：没有明显信息优势

### 4.2 关键证据
列出支持你判断的关键证据（**只能来自 2025 年 12 月的搜索结果和交易历史，禁止引用 2026 年信息**）

---

## 第五步：跟单风险提示

- 鲸鱼也可能犯错或有其他动机（对冲、试探等）
- 基于 2025 年 12 月的公开信息，市场可能已经部分反映了相关预期
- 搜索结果可能不完整
- **注意：我们不知道交易之后会发生什么，只能基于 2025 年 12 月的信息做判断**

---

## 第六步：最终决策

**重要提醒**：你的决策必须只基于 2025 年 12 月及之前的公开信息，不能假设或引用任何 2026 年的事件。

基于以上分析，给出你的交易建议，并用以下JSON格式输出决策：

```json
{{
    "action": "BUY/SELL/HOLD",
    "outcome": "你建议交易的结果选项",
    "confidence": 0.0-1.0之间的数字,
    "information_asymmetry_score": 0.0-1.0之间的数字（信息不对称程度评估）,
    "trader_credibility": "HIGH/MEDIUM/LOW/UNKNOWN",
    "suggested_price": 建议的交易价格,
    "suggested_size_percent": 0.0-1.0之间的数字（建议使用资金的比例）,
    "reasoning": "简要说明你的推理过程",
    "insider_evidence": "支持内幕交易判断的关键证据"
}}
```

注意：
- action为HOLD时，outcome可以为空字符串
- confidence低于0.6时应该选择HOLD
- suggested_size_percent不应超过0.2（20%的资金）
- 请确保输出的是有效的JSON格式

---

**免责声明**：本报告仅供参考，不构成投资建议。预测市场具有高风险，请用户基于自身判断谨慎决策。"""

    def _extract_json_from_response(self, response: str) -> Optional[dict]:
        """Extract JSON from LLM response."""
        json_pattern = r"```(?:json)?\s*([\s\S]*?)```"
        matches = re.findall(json_pattern, response)

        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue

        try:
            start = response.find("{")
            end = response.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(response[start:end])
        except json.JSONDecodeError:
            pass

        return None

    async def analyze_trade(self, trade: dict, market_info: Optional[dict], prior_trades: list = None) -> tuple[str, Optional[dict]]:
        """Analyze a single trade with LLM (with Web Search and prior trades history)."""
        timestamp = trade.get("timestamp", 0)
        trade_date = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d') if timestamp else 'N/A'

        trade_context = self.build_trade_context(trade, market_info, prior_trades)
        system_prompt = self.get_system_prompt(trade_date)
        user_prompt = self.get_analysis_prompt(trade_context, trade_date)

        try:
            # Call LLM API
            response = self.genai_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )

            analysis_text = response.choices[0].message.content
            json_data = self._extract_json_from_response(analysis_text)
            return analysis_text, json_data

        except Exception as e:
            logger.error(f"Error calling LLM: {e}")
            return f"Error: {str(e)}", None

    def format_report(self, trade: dict, market_info: Optional[dict], analysis_text: str, json_data: Optional[dict]) -> str:
        """Format the full report."""
        usdc_size = trade.get("_usdc_size", 0)
        side = trade.get("side", "")
        price = float(trade.get("price", 0) or 0)
        outcome = trade.get("outcome", "")
        timestamp = trade.get("timestamp", 0)
        market_question = trade.get("title", trade.get("marketTitle", "Unknown"))

        if market_info:
            market_question = market_info.get("question", market_question)

        # Format prices
        prices_str = ""
        if market_info:
            outcomes = market_info.get("outcomes", [])
            outcome_prices = market_info.get("outcomePrices", [])
            if outcomes and outcome_prices:
                try:
                    prices_str = " | ".join([f"{o}: {float(p):.1%}" for o, p in zip(outcomes, outcome_prices)])
                except:
                    prices_str = "N/A"

        # Parse recommendation
        action = "HOLD"
        confidence = 0.0
        reasoning = ""
        insider_likelihood = 0.0
        trader_credibility = "UNKNOWN"
        insider_evidence = ""

        if json_data:
            action = json_data.get("action", "HOLD")
            confidence = float(json_data.get("confidence", 0))
            reasoning = json_data.get("reasoning", "")
            insider_likelihood = float(json_data.get("information_asymmetry_score", 0))
            trader_credibility = json_data.get("trader_credibility", "UNKNOWN")
            insider_evidence = json_data.get("insider_evidence", "")

        # Action indicator
        action_indicators = {
            "BUY": "🟢 BUY",
            "SELL": "🔴 SELL",
            "HOLD": "⚪ HOLD",
        }

        # Insider indicator
        if insider_likelihood >= 0.7:
            insider_indicator = f"🔴 高度可疑 ({insider_likelihood:.0%})"
        elif insider_likelihood >= 0.4:
            insider_indicator = f"🟡 中等可能 ({insider_likelihood:.0%})"
        else:
            insider_indicator = f"🟢 普通交易 ({insider_likelihood:.0%})"

        # Credibility indicator
        credibility_indicators = {
            "HIGH": "🏆 高可信度 (前100名)",
            "MEDIUM": "⭐ 中等可信度 (100-500名)",
            "LOW": "📉 低可信度 (500名+)",
            "UNKNOWN": "❓ 未知 (未上榜)",
        }

        time_str = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else 'N/A'

        report = f"""
{'='*70}
# 🐋 鲸鱼交易回测分析报告
{'='*70}

**生成时间**: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
**回测模式**: 无 Web Search

## 交易摘要

| 项目 | 详情 |
|------|------|
| **市场** | {market_question} |
| **交易金额** | ${usdc_size:,.2f} USDC |
| **交易方向** | {side} |
| **交易价格** | {price:.4f} ({price:.1%}) |
| **交易结果** | {outcome} |
| **当前赔率** | {prices_str or 'N/A'} |
| **交易时间** | {time_str} |

{'='*70}

{analysis_text}

{'='*70}
## 🔍 内幕交易评估
{'='*70}

| 项目 | 评估 |
|------|------|
| **内幕交易可能性** | {insider_indicator} |
| **交易者可信度** | {credibility_indicators.get(trader_credibility, '❓ 未知')} |

**关键证据**: {insider_evidence or '无明确证据'}

{'='*70}
## 📊 决策摘要
{'='*70}

| 项目 | 建议 |
|------|------|
| **操作建议** | {action_indicators.get(action, '⚪ HOLD')} |
| **目标结果** | {json_data.get('outcome', 'N/A') if json_data else 'N/A'} |
| **建议仓位** | {json_data.get('suggested_size_percent', 0):.1%} if json_data else 'N/A' |
| **建议价格** | {json_data.get('suggested_price', 'Market') if json_data else 'Market'} |

**决策理由**: {reasoning}

{'='*70}
⚠️ 免责声明：本报告由AI生成，仅供参考，不构成投资建议。
预测市场具有高风险，请基于自身判断谨慎决策。
{'='*70}
"""
        return report

    def save_report(self, trade: dict, report: str) -> str:
        """Save report to file."""
        usdc_size = trade.get("_usdc_size", 0)
        side = trade.get("side", "")
        timestamp = trade.get("timestamp", 0)
        market_title = trade.get("title", trade.get("marketTitle", "Unknown"))

        # Clean title for filename
        clean_title = re.sub(r'[^\w\s-]', '', market_title)[:50].strip().replace(' ', '_')

        time_str = datetime.fromtimestamp(timestamp).strftime('%Y%m%d_%H%M%S') if timestamp else 'unknown'
        filename = f"{time_str}_{side}_{int(usdc_size)}USD_{clean_title}.md"
        filepath = REPORTS_DIR / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(report)

        return str(filepath)


async def main():
    """Main backtest function."""
    analyzer = BacktestAnalyzer()

    try:
        logger.info(f"Starting backtest for user: {TARGET_USER}")
        logger.info(f"Filter criteria: MIN_SIZE=${MIN_TRADE_SIZE_USD}, PRICE_RANGE={MIN_PRICE}-{MAX_PRICE}")

        # Fetch all trades for user
        logger.info("Fetching user trades...")
        all_trades = await analyzer.fetch_user_trades(TARGET_USER)
        logger.info(f"Found {len(all_trades)} total trades")

        # Filter trades
        filtered_trades = analyzer.filter_trades(all_trades)
        logger.info(f"Found {len(filtered_trades)} trades matching criteria")

        if not filtered_trades:
            logger.info("No trades found matching criteria")
            return

        # Sort by timestamp (oldest first) for correct chronological order
        filtered_trades.sort(key=lambda x: x.get("timestamp", 0))

        # Only analyze the latest trade (the $7,215 one at 2026-01-03 10:58:25)
        # Use previous trades as history
        target_trade = filtered_trades[-1]  # The latest trade
        prior_trades = filtered_trades[:-1]  # All trades before the target

        usdc_size = target_trade.get("_usdc_size", 0)
        side = target_trade.get("side", "")
        market_title = target_trade.get("title", target_trade.get("marketTitle", "Unknown"))
        timestamp = target_trade.get("timestamp", 0)
        trade_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else 'N/A'

        logger.info(f"\nAnalyzing target trade: {side} ${usdc_size:,.2f} - {market_title[:50]}")
        logger.info(f"Trade time: {trade_time}")
        logger.info(f"Prior trades count: {len(prior_trades)}")

        for i, pt in enumerate(prior_trades, 1):
            pt_time = datetime.fromtimestamp(pt.get("timestamp", 0)).strftime('%Y-%m-%d %H:%M:%S') if pt.get("timestamp") else 'N/A'
            pt_usdc = pt.get("_usdc_size", 0)
            pt_side = pt.get("side", "")
            logger.info(f"  Prior trade {i}: [{pt_time}] {pt_side} ${pt_usdc:,.2f}")

        # Fetch market info
        condition_id = target_trade.get("conditionId", "")
        market_info = await analyzer.fetch_market_info(condition_id) if condition_id else None

        # Analyze with LLM (with Web Search and prior trades history)
        logger.info("\nCalling LLM with Web Search enabled...")
        analysis_text, json_data = await analyzer.analyze_trade(target_trade, market_info, prior_trades)

        # Format and save report
        report = analyzer.format_report(target_trade, market_info, analysis_text, json_data)
        filepath = analyzer.save_report(target_trade, report)
        logger.info(f"Report saved: {filepath}")

        logger.info(f"\nBacktest complete! {len(filtered_trades)} reports generated in {REPORTS_DIR}")

    finally:
        await analyzer.close()


if __name__ == "__main__":
    asyncio.run(main())
