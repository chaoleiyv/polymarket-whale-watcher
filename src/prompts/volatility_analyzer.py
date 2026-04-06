"""Prompts for price volatility analysis - detecting leading signals."""
from datetime import datetime, timezone


class VolatilityAnalyzerPrompts:
    """Prompts for LLM price volatility analysis."""

    @staticmethod
    def system_prompt() -> str:
        """Get the system prompt for volatility analysis."""
        current_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        return f"""你是一位专业的预测市场分析师，专门研究"价格领先于新闻"的现象。

**当前真实时间**：{current_utc}

**你的核心任务**：判断一次市场价格异常波动是否"领先于公开新闻"——即价格变动发生在相关新闻公开报道之前。

## 背景知识

在预测市场中，有时会出现这样的现象：
1. 市场价格突然大幅波动
2. 但此时主流新闻媒体尚未报道相关事件
3. 随后（几小时或几天后），相关新闻才公开

这种"价格领先于新闻"的现象可能说明：
- 有知情人士提前获知了信息并进行交易
- 市场参与者通过社交媒体、小道消息等渠道获取了信息
- 纯粹的市场投机或技术性波动

## 你的工作流程

### 第一步：分析提供的 Web 搜索结果
- 分析与市场主题相关的最新新闻
- 特别关注新闻的发布时间
- 判断是否有重大新闻可以解释这次价格波动

### 第二步：分析 Twitter 社交媒体数据
- 分析提供的 Twitter 搜索结果
- 查看是否有早期的社交媒体讨论
- 关注 KOL、内部人士的发言时间

### 第三步：判断价格波动的性质
根据搜索结果，将价格波动分为以下几类：

1. **LEADING_SIGNAL（领先信号）**：价格波动明显早于公开新闻
   - 搜索不到能解释波动的已发布新闻
   - 或者找到的新闻发布时间晚于价格波动
   - 这是我们最关注的类型！

2. **NEWS_DRIVEN（新闻驱动）**：价格波动是对已发布新闻的反应
   - 找到了明确的相关新闻
   - 新闻发布时间早于或接近价格波动时间

3. **SOCIAL_DRIVEN（社交驱动）**：价格波动由社交媒体讨论引发
   - Twitter 上有大量讨论，但主流媒体尚未报道
   - 介于领先信号和新闻驱动之间

4. **SPECULATION（投机波动）**：无明显信息来源的波动
   - 搜索不到相关新闻或讨论
   - 可能是纯粹的市场投机

**重要原则**：
- 务必仔细分析提供的 Web 搜索结果中的最新新闻
- 仔细分析 Twitter 搜索结果
- 特别关注新闻和讨论的时间戳
- 如果是 LEADING_SIGNAL，详细记录证据"""

    @staticmethod
    def analyze_volatility(
        market_question: str,
        price_change_percent: float,
        direction: str,
        start_price: float,
        end_price: float,
        window_seconds: int,
        detected_at: str,
        twitter_context: str = "",
        web_search_context: str = "",
    ) -> str:
        """
        Get the prompt for analyzing a price volatility event.

        Args:
            market_question: The market question
            price_change_percent: Price change as decimal (e.g., 0.25 for 25%)
            direction: "UP" or "DOWN"
            start_price: Starting price
            end_price: Ending price
            window_seconds: Time window in seconds
            detected_at: Detection timestamp
            twitter_context: Twitter search results
            web_search_context: Web search results from Tavily

        Returns:
            Complete prompt for LLM
        """
        direction_cn = "上涨" if direction == "UP" else "下跌"
        window_minutes = window_seconds // 60

        web_search_section = ""
        if web_search_context:
            web_search_section = f"""
---

## Web 搜索结果（新闻与分析）

以下是与该市场相关的最新网络搜索结果，请仔细分析发布时间和内容：

{web_search_context}

---
"""

        twitter_section = ""
        if twitter_context:
            twitter_section = f"""
---

## Twitter 社交媒体搜索结果

以下是与该市场相关的 Twitter 实时讨论，请仔细分析发布时间和内容：

{twitter_context}

---
"""

        return f"""## 价格异常波动检测报告

### 波动详情
- **市场问题**: {market_question}
- **价格变动**: {direction_cn} {abs(price_change_percent):.1%}
- **起始价格**: {start_price:.2%}
- **结束价格**: {end_price:.2%}
- **时间窗口**: {window_minutes} 分钟内
- **检测时间**: {detected_at}

{web_search_section}{twitter_section}

---

# 价格波动验证任务

你检测到了一次显著的价格异常波动，请判断这是否是一个"领先于新闻"的信号。

---

## 第一步：Web 搜索结果分析（必须分析！）

**请仔细分析上文提供的 Web 搜索结果，重点关注：**

1. 与"{market_question}"相关的最新新闻（重点关注过去24小时）
2. 可能触发这次价格波动的事件或公告
3. 每条新闻的发布时间

**Web 搜索结果摘要**：
（请在此列出搜索结果中的关键新闻，必须包含发布时间）

---

## 第二步：Twitter 社交媒体分析

**分析上文提供的 Twitter 搜索结果：**

1. 最早的相关讨论是什么时候？
2. 讨论的主要内容是什么？
3. 是否有 KOL 或内部人士发言？
4. 社交媒体讨论是否早于主流新闻报道？

**Twitter 分析摘要**：
（请在此总结 Twitter 上的关键信息和时间线）

---

## 第三步：时间线对比分析

**关键问题**：价格波动发生在新闻公开之前还是之后？

- 价格波动检测时间: {detected_at}
- 找到的最早相关新闻发布时间: [请填写]
- 找到的最早社交媒体讨论时间: [请填写]

**时间线结论**：
（价格波动是领先于新闻，还是滞后于新闻？）

---

## 第四步：最终判定

基于以上分析，给出你的判断，并用以下 JSON 格式输出：

```json
{{
    "signal_type": "LEADING_SIGNAL/NEWS_DRIVEN/SOCIAL_DRIVEN/SPECULATION",
    "confidence": 0.0-1.0之间的数字,
    "is_leading_signal": true/false,
    "news_found": true/false,
    "earliest_news_time": "找到的最早相关新闻的发布时间，格式 YYYY-MM-DD HH:MM UTC，如无则为 null",
    "earliest_social_time": "找到的最早社交媒体讨论时间，格式 YYYY-MM-DD HH:MM UTC，如无则为 null",
    "time_advantage_minutes": 价格领先于新闻的分钟数（如果是领先信号），否则为 0,
    "key_news_headlines": ["相关新闻标题1", "相关新闻标题2"],
    "key_social_posts": ["关键社交媒体帖子摘要1", "关键社交媒体帖子摘要2"],
    "reasoning": "简要说明你的判断依据",
    "potential_information_source": "推测的信息来源（如：内部人士、社交媒体泄露、官方提前通知等）"
}}
```

**判断标准**：
- **LEADING_SIGNAL**: 价格波动发生时，Web 搜索不到相关新闻，或新闻发布时间明显晚于价格波动（>=30分钟）
- **NEWS_DRIVEN**: 找到了明确相关的新闻，且新闻发布时间早于或接近价格波动时间
- **SOCIAL_DRIVEN**: Twitter 上有早期讨论，但主流媒体尚未报道
- **SPECULATION**: 既没有新闻也没有社交讨论，可能是纯投机

**特别注意**：
- is_leading_signal 为 true 时，必须详细说明证据
- time_advantage_minutes 表示价格领先于新闻的时间优势
- 这个数据将用于构建"价格领先于新闻"的研究数据集

---

⚠️ 免责声明：本分析仅供研究参考，不构成投资建议。"""
