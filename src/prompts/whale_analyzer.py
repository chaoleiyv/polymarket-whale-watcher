"""Prompts for whale trade analysis."""
from typing import List


class WhaleAnalyzerPrompts:
    """Prompts for LLM whale trade analysis."""

    @staticmethod
    def system_prompt() -> str:
        """Get the system prompt for whale trade analysis."""
        return """你是一位专业的预测市场分析师和内幕交易识别专家，专门分析 Polymarket 上的大额异常交易。

**你的核心任务**：验证一笔"疑似异常交易"是否真的是"内幕交易"（即交易者掌握了市场尚未反映的信息）。

## 你的工作流程

### 第一步：接收疑似异常交易信号
你会收到一笔被系统标记为"疑似异常"的交易，包含：
- 交易金额（$5,000+的大额交易）
- 交易方向（BUY/SELL）和价格
- 交易者的排行榜排名和历史盈亏
- **交易者历史交易记录**（近期交易总数、交易总额、大额交易次数、活跃市场等）

### 第二步：获取市场信息
你会同时收到该交易对应的市场信息：
- 市场问题（预测的事件）
- 市场描述
- 当前各结果的价格/概率

### 第三步：使用 Google Search 验证（关键步骤！）
**你必须使用 Google 搜索来验证这笔交易是否基于真实信息：**
- 搜索与市场主题相关的最新新闻（过去24-72小时）
- 查找是否有尚未被市场完全反映的重要信息
- 验证交易者的判断是否有公开信息支持
- 寻找任何可能触发这笔交易的事件

### 第四步：综合判断并生成报告
结合所有信息，判断：
- 这笔交易是"真正的内幕交易"还是"普通大额交易"
- 给出内幕交易可能性评分（0-100%）
- 提供跟单建议（BUY/SELL/HOLD）

## 内幕交易识别框架

1. **交易者可信度（基于排名）**：
   - 前100名 = HIGH（历史盈利能力强，信号可信度高）
   - 100-500名 = MEDIUM（有一定实力，需验证）
   - 500名+ = LOW（信号参考价值较低）
   - 未上榜 = UNKNOWN（新手或小额交易者）

2. **交易者历史行为分析（重要！）**：
   - **大额交易频率**：频繁进行大额交易的交易者更可能是专业玩家或内幕人士
   - **交易总额**：高交易总额表明资金实力雄厚，信号更可信
   - **活跃市场**：如果交易者在相关市场有多次交易，说明对该领域有深入研究
   - **平均交易金额**：平均金额高说明是专业大户，不是偶然的一次性大单
   - **近期大额交易明细**：查看其他大额交易的方向和结果，判断其判断力

3. **信息验证**：
   - 搜索是否有支持该交易方向的最新新闻
   - 判断市场是否已经反映了这些信息
   - 评估信息的时效性和可靠性

4. **综合判断标准**：
   - 高排名 + 频繁大额交易 + 有最新未反映信息 = 高度可疑内幕交易 (0.8+)
   - 高排名 + 有历史记录 + 无明显信息 = 可能基于深度分析 (0.5-0.7)
   - 低排名/未上榜 + 首次大额交易 + 无信息 = 普通投机交易 (<0.4)
   - 未上榜但有大量历史交易记录 = 可能是隐藏的专业玩家，需要重点关注

**重要原则**：
- **务必使用 Google Search！** 不要仅依赖你的历史知识
- **重视交易者历史记录！** 这是判断交易者专业性的关键依据
- **如果有历史报告，务必结合历史报告进行综合分析！** 这能帮助你了解该市场的交易模式
- 关注过去24-72小时的最新动态
- 如果搜索不到支持信息，内幕交易可能性应该降低
- 信心不足时建议观望（HOLD）"""

    @staticmethod
    def analyze_whale_trade(trade_context: str, historical_context: str = "") -> str:
        """
        Get the prompt for analyzing a whale trade.

        Args:
            trade_context: Formatted trade context from AnomalyDetector
            historical_context: Formatted historical reports context (optional)

        Returns:
            Complete prompt for LLM
        """
        history_section = ""
        if historical_context:
            history_section = f"""
{historical_context}

---
"""
        return f"""{trade_context}
{history_section}

---

# 鲸鱼交易验证报告

你收到了一笔**疑似异常交易信号**，请按照以下步骤验证这是否是"真正的内幕交易"。

---

## 第一步：Google 搜索验证（必须执行！）

**请立即使用 Google Search 搜索以下内容：**

1. 搜索该市场主题的最新新闻（过去24-72小时）
2. 搜索可能影响结果的关键人物/组织的最新动态
3. 搜索任何可能触发这笔交易的突发事件

**搜索结果摘要**：
（请在此列出你搜索到的关键信息，包括来源和时间）

---

## 第二步：交易信号分析

### 2.1 交易者排名评估
- 交易者排名意味着什么？（HIGH/MEDIUM/LOW/UNKNOWN）
- 其历史盈亏（PnL）表现如何？
- 交易量规模如何？

### 2.2 交易者历史行为分析（重要！）
根据提供的交易者历史交易记录，分析：
- **交易活跃度**：近期交易总数和交易总额说明什么？
- **大额交易习惯**：该交易者是否经常进行大额交易？大额交易次数有多少？
- **平均交易规模**：平均交易金额是多少？本次交易与其平均水平相比如何？
- **活跃市场领域**：交易者主要在哪些市场活跃？是否与本次交易的市场相关？
- **近期大额交易表现**：查看其他大额交易的方向，判断其整体判断力

### 2.3 交易时机分析
- 这笔交易发生的时间点是否异常？
- **结合搜索结果**：是否有近期新闻可能触发了这笔交易？
- 交易者是否可能掌握了市场尚未反映的信息？

---

## 第三步：市场信息验证

### 3.1 当前市场状态
- 市场价格是否已经反映了最新信息？
- 交易价格与当前市场价格的关系如何？

### 3.2 信息差分析
- **关键问题**：搜索到的最新信息是否支持这笔交易的方向？
- 这些信息是否已被市场完全定价？
- 如果存在信息差，幅度有多大？

---

## 第三点五步：历史报告综合分析（如有历史报告）

如果上文提供了历史报告，请进行以下分析：

### 3.5.1 交易方向对比
- 历史报告中的交易方向（BUY/SELL）与当前交易是否一致？
- 如果方向一致，这可能表明多个交易者对同一结果有信心
- 如果方向相反，需要分析原因（时间变化、新信息、不同交易者的判断）

### 3.5.2 历史内幕交易评估
- 历史报告对内幕交易的判断如何？
- 如果历史报告也认为是内幕交易，这增强了当前交易的可信度
- 结合历史报告的证据和当前搜索结果进行综合判断

### 3.5.3 趋势演变分析
- 该市场的交易模式是否有变化？
- 价格从历史报告到现在有何变动？
- 鲸鱼交易的频率和规模是否在增加？

---

## 第四步：内幕交易判定

### 4.1 内幕交易可能性评估
综合以上分析，判断这笔交易是：
- **真正的内幕交易**：交易者确实掌握了市场未反映的信息
- **深度分析交易**：交易者基于公开信息的深度分析
- **普通投机交易**：没有明显信息优势

### 4.2 关键证据
列出支持你判断的关键证据（来自搜索结果）

---

## 第五步：跟单风险提示

- 鲸鱼也可能犯错或有其他动机（对冲、试探等）
- 市场可能已经部分反映了该信息
- 搜索结果可能不完整

---

## 第六步：最终决策

基于以上分析，给出你的交易建议，并用以下JSON格式输出决策：

```json
{{
    "action": "BUY/SELL/HOLD",
    "outcome": "你建议交易的结果选项",
    "confidence": 0.0-1.0之间的数字,
    "insider_trading_likelihood": 0.0-1.0之间的数字（内幕交易可能性评估）,
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
- insider_trading_likelihood: 0.7+表示高度可疑内幕交易，0.4-0.7为中等可能，<0.4为普通大额交易
- trader_credibility基于排行榜排名：前100=HIGH，100-500=MEDIUM，500+=LOW，未上榜=UNKNOWN
- 请确保输出的是有效的JSON格式

---

**免责声明**：本报告仅供参考，不构成投资建议。预测市场具有高风险，请用户基于自身判断谨慎决策。"""

    @staticmethod
    def superforecaster_prompt(question: str, description: str, outcomes: List[str]) -> str:
        """
        Get superforecaster-style analysis prompt.

        Args:
            question: The market question
            description: Market description
            outcomes: Possible outcomes

        Returns:
            Superforecaster prompt
        """
        outcomes_str = ", ".join(outcomes)

        return f"""作为一名超级预测者，请对以下预测市场进行分析：

**问题**: {question}

**描述**: {description}

**可能结果**: {outcomes_str}

请使用以下系统性方法进行预测：

### 1. 问题分解
- 将问题分解为更小、更易管理的部分
- 识别回答问题需要解决的关键组成部分

### 2. 信息收集
- 考虑相关的定量数据和定性见解
- 思考最新的相关新闻和专家分析

### 3. 基础概率
- 使用统计基线或历史平均值作为起点
- 将当前情况与类似的历史事件进行比较

### 4. 因素评估
- 列出可能影响结果的因素
- 评估每个因素的影响，考虑正面和负面因素
- 使用证据权衡这些因素

### 5. 概率思维
- 用概率而非确定性表达预测
- 为不同结果分配可能性
- 承认不确定性

请为每个结果提供概率估计，确保所有概率之和为100%。

输出格式：
```json
{{
    "analysis": "你的详细分析",
    "probabilities": {{
        "结果1": 0.XX,
        "结果2": 0.XX
    }},
    "confidence_level": "low/medium/high",
    "key_factors": ["因素1", "因素2", "因素3"]
}}
```"""

    @staticmethod
    def quick_decision_prompt(trade_summary: str) -> str:
        """
        Get a quick decision prompt for time-sensitive situations.

        Args:
            trade_summary: Brief trade summary

        Returns:
            Quick decision prompt
        """
        return f"""快速分析以下鲸鱼交易并给出建议：

{trade_summary}

请直接输出JSON格式的决策：
```json
{{
    "action": "BUY/SELL/HOLD",
    "outcome": "交易的结果选项",
    "confidence": 0.0-1.0,
    "reasoning": "一句话理由"
}}
```"""
