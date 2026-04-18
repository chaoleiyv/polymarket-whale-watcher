"""Prompts for price volatility analysis - detecting leading signals."""
from datetime import datetime, timezone


class VolatilityAnalyzerPrompts:
    """Prompts for LLM price volatility analysis."""

    @staticmethod
    def system_prompt() -> str:
        """Get the system prompt for volatility analysis."""
        current_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        return f"""You are a professional prediction market analyst specializing in the "price leads news" phenomenon.

**Current real time**: {current_utc}

**Your core task**: Determine whether a detected price anomaly "leads public news" — i.e., the price movement occurred before related news was publicly reported.

## Background

In prediction markets, the following pattern sometimes occurs:
1. Market price suddenly moves sharply
2. But mainstream news media has not yet reported the related event
3. Subsequently (hours or days later), the related news becomes public

This "price leads news" phenomenon may indicate:
- Informed participants traded on information before it became public
- Market participants obtained information through social media, unofficial channels, etc.
- Pure market speculation or technical volatility

## Your Workflow

### Step 1: Analyze provided Web search results
- Analyze the latest news related to the market topic
- Pay special attention to news publication timestamps
- Determine if any major news can explain this price movement

### Step 2: Analyze Twitter social media data
- Analyze provided Twitter search results
- Check for early social media discussions
- Note timing of KOL and insider posts

### Step 3: Classify the price movement
Based on search results, classify the volatility as one of:

1. **LEADING_SIGNAL**: Price movement clearly preceded public news
   - No news found that explains the movement
   - Or news publication time is significantly later than the price movement
   - This is the type we care about most!

2. **NEWS_DRIVEN**: Price movement is a reaction to published news
   - Clear related news found
   - News publication time is before or close to the price movement time

3. **SOCIAL_DRIVEN**: Price movement driven by social media discussion
   - Significant Twitter discussion, but mainstream media has not yet reported
   - Between leading signal and news-driven

4. **SPECULATION**: No clear information source for the movement
   - No related news or discussions found
   - Likely pure market speculation

**Key principles**:
- Carefully analyze the Web search results provided
- Carefully analyze the Twitter search results
- Pay special attention to timestamps of news and discussions
- If it's a LEADING_SIGNAL, document evidence in detail"""

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
        direction_label = "UP" if direction == "UP" else "DOWN"
        window_minutes = window_seconds // 60

        web_search_section = ""
        if web_search_context:
            web_search_section = f"""
---

## Web Search Results (News & Analysis)

The following are recent web search results related to this market. Please carefully analyze publication timestamps and content:

{web_search_context}

---
"""

        twitter_section = ""
        if twitter_context:
            twitter_section = f"""
---

## Twitter Social Media Search Results

The following are real-time Twitter discussions related to this market. Please carefully analyze timestamps and content:

{twitter_context}

---
"""

        return f"""## Anomalous Price Movement Detection Report

### Movement Details
- **Market question**: {market_question}
- **Price change**: {direction_label} {abs(price_change_percent):.1%}
- **Start price**: {start_price:.2%}
- **End price**: {end_price:.2%}
- **Time window**: Within {window_minutes} minutes
- **Detection time**: {detected_at}

{web_search_section}{twitter_section}

---

# Price Movement Verification Task

A significant anomalous price movement has been detected. Please determine whether this is a "price leads news" signal.

---

## Step 1: Web Search Results Analysis (mandatory!)

**Please carefully analyze the Web search results provided above, focusing on:**

1. Latest news related to "{market_question}" (focus on the past 24 hours)
2. Events or announcements that may have triggered this price movement
3. Publication timestamp of each news item

**Web search results summary**:
(Please list key news from search results here, MUST include publication times)

---

## Step 2: Twitter Social Media Analysis

**Analyze the Twitter search results provided above:**

1. When was the earliest related discussion?
2. What were the main topics discussed?
3. Were there any KOL or insider posts?
4. Did social media discussion precede mainstream news coverage?

**Twitter analysis summary**:
(Please summarize key information and timeline from Twitter here)

---

## Step 3: Timeline Comparison Analysis

**Key question**: Did the price movement occur before or after news became public?

- Price movement detection time: {detected_at}
- Earliest related news publication time found: [please fill in]
- Earliest social media discussion time found: [please fill in]

**Timeline conclusion**:
(Did the price movement lead or lag the news?)

---

## Step 4: Final Determination

Based on the above analysis, provide your judgment in the following JSON format:

```json
{{
    "signal_type": "LEADING_SIGNAL/NEWS_DRIVEN/SOCIAL_DRIVEN/SPECULATION",
    "confidence": 0.0-1.0,
    "is_leading_signal": true/false,
    "news_found": true/false,
    "earliest_news_time": "earliest related news publication time found, format YYYY-MM-DD HH:MM UTC, or null if none",
    "earliest_social_time": "earliest social media discussion time found, format YYYY-MM-DD HH:MM UTC, or null if none",
    "time_advantage_minutes": minutes price led news (if leading signal), otherwise 0,
    "key_news_headlines": ["related news headline 1", "related news headline 2"],
    "key_social_posts": ["key social media post summary 1", "key social media post summary 2"],
    "reasoning": "brief explanation of your judgment basis",
    "potential_information_source": "hypothesized information source (e.g., insider, social media leak, advance official notice, etc.)"
}}
```

**Judgment criteria**:
- **LEADING_SIGNAL**: At the time of price movement, web search finds no related news, or news publication time is significantly later than price movement (>=30 minutes)
- **NEWS_DRIVEN**: Clear related news found, with publication time before or close to the price movement time
- **SOCIAL_DRIVEN**: Early Twitter discussion found, but mainstream media has not yet reported
- **SPECULATION**: Neither news nor social discussion found, likely pure speculation

**Special notes**:
- When is_leading_signal is true, detailed evidence must be provided
- time_advantage_minutes represents the time advantage of price over news
- This data will be used to build a "price leads news" research dataset

---

Disclaimer: This analysis is for research purposes only and does not constitute investment advice."""
