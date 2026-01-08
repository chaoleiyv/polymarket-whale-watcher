"""Report history service - finds and summarizes historical reports for the same market."""
import os
import re
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class HistoricalReport:
    """Represents a historical report for the same market."""
    filepath: str
    filename: str
    timestamp: datetime
    side: str
    amount_usd: float
    market_name: str
    content: str

    @property
    def summary(self) -> str:
        """Extract key decision info from the report."""
        # Try to extract the JSON decision section
        json_pattern = r'```json\s*([\s\S]*?)```'
        matches = re.findall(json_pattern, self.content)

        decision_info = ""
        for match in matches:
            try:
                import json
                data = json.loads(match.strip())
                action = data.get('action', 'N/A')
                confidence = data.get('confidence', 'N/A')
                insider_likelihood = data.get('insider_trading_likelihood', 'N/A')
                reasoning = data.get('reasoning', 'N/A')

                if isinstance(confidence, (int, float)):
                    confidence = f"{confidence:.0%}"
                if isinstance(insider_likelihood, (int, float)):
                    insider_likelihood = f"{insider_likelihood:.0%}"

                decision_info = f"""
- **操作建议**: {action}
- **信心程度**: {confidence}
- **内幕交易可能性**: {insider_likelihood}
- **决策理由**: {reasoning}"""
                break
            except (json.JSONDecodeError, KeyError):
                continue

        return f"""**报告时间**: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S')}
**交易方向**: {self.side}
**交易金额**: ${self.amount_usd:,.2f} USDC
{decision_info}"""


class ReportHistoryService:
    """Service for finding and managing historical reports."""

    def __init__(self, reports_dir: Optional[Path] = None):
        """
        Initialize the report history service.

        Args:
            reports_dir: Path to the reports directory. Defaults to project's reports dir.
        """
        if reports_dir is None:
            self.reports_dir = Path(__file__).parent.parent.parent / "reports"
        else:
            self.reports_dir = reports_dir

    def _parse_filename(self, filename: str) -> Optional[dict]:
        """
        Parse a report filename to extract metadata.

        Filename format: {timestamp}_{side}_{amount}USD_{market_name}.md
        Example: 20260105_150505_BUY_7520USD_Trump_out_as_President_before_2027.md

        Args:
            filename: The filename to parse

        Returns:
            Dictionary with parsed metadata or None if parsing fails
        """
        if not filename.endswith('.md'):
            return None

        # Pattern: timestamp_side_amountUSD_market_name.md
        pattern = r'^(\d{8}_\d{6})_(BUY|SELL)_(\d+)USD_(.+)\.md$'
        match = re.match(pattern, filename)

        if not match:
            return None

        timestamp_str, side, amount_str, market_name = match.groups()

        try:
            timestamp = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S')
            amount = float(amount_str)
        except ValueError:
            return None

        return {
            'timestamp': timestamp,
            'side': side,
            'amount_usd': amount,
            'market_name': market_name,
        }

    def _sanitize_market_name(self, market_question: str, max_length: int = 50) -> str:
        """
        Sanitize market question for matching with filenames.

        Args:
            market_question: The market question to sanitize
            max_length: Maximum length of the sanitized name

        Returns:
            Sanitized market name
        """
        # Remove special characters, keep alphanumeric and spaces
        sanitized = re.sub(r'[^\w\s-]', '', market_question)
        # Replace spaces with underscores
        sanitized = re.sub(r'\s+', '_', sanitized)
        # Truncate if too long
        return sanitized[:max_length]

    def _calculate_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate similarity between two market names.

        Uses a simple word overlap method for fuzzy matching.

        Args:
            name1: First market name (sanitized)
            name2: Second market name (from filename)

        Returns:
            Similarity score between 0 and 1
        """
        # Convert to lowercase and split into words
        words1 = set(name1.lower().replace('_', ' ').split())
        words2 = set(name2.lower().replace('_', ' ').split())

        # Remove common stop words
        stop_words = {'the', 'a', 'an', 'is', 'are', 'will', 'by', 'to', 'of', 'in', 'on', 'for'}
        words1 = words1 - stop_words
        words2 = words2 - stop_words

        if not words1 or not words2:
            return 0.0

        # Calculate Jaccard similarity
        intersection = len(words1 & words2)
        union = len(words1 | words2)

        return intersection / union if union > 0 else 0.0

    def find_historical_reports(
        self,
        market_question: str,
        similarity_threshold: float = 0.5,
        max_reports: int = 5,
    ) -> List[HistoricalReport]:
        """
        Find historical reports for the same or similar market.

        Args:
            market_question: The market question to search for
            similarity_threshold: Minimum similarity score to include a report
            max_reports: Maximum number of reports to return

        Returns:
            List of HistoricalReport objects, sorted by timestamp (newest first)
        """
        if not self.reports_dir.exists():
            return []

        sanitized_question = self._sanitize_market_name(market_question)
        matching_reports = []

        for filename in os.listdir(self.reports_dir):
            metadata = self._parse_filename(filename)
            if metadata is None:
                continue

            # Calculate similarity between market names
            similarity = self._calculate_similarity(
                sanitized_question,
                metadata['market_name']
            )

            if similarity >= similarity_threshold:
                filepath = self.reports_dir / filename
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                except Exception:
                    continue

                report = HistoricalReport(
                    filepath=str(filepath),
                    filename=filename,
                    timestamp=metadata['timestamp'],
                    side=metadata['side'],
                    amount_usd=metadata['amount_usd'],
                    market_name=metadata['market_name'],
                    content=content,
                )
                matching_reports.append((similarity, report))

        # Sort by similarity (descending) then by timestamp (descending)
        matching_reports.sort(key=lambda x: (x[0], x[1].timestamp), reverse=True)

        # Return only the reports (without similarity scores)
        return [report for _, report in matching_reports[:max_reports]]

    def format_historical_context(
        self,
        reports: List[HistoricalReport],
    ) -> str:
        """
        Format historical reports into a context string for LLM.

        Args:
            reports: List of historical reports

        Returns:
            Formatted string for LLM context
        """
        if not reports:
            return ""

        context = f"""
### 历史报告分析 (共 {len(reports)} 份历史报告)

**重要**: 该市场之前已经生成过分析报告，请结合历史报告进行综合分析。

"""
        for i, report in enumerate(reports, 1):
            context += f"""
---
#### 历史报告 {i}
{report.summary}
---
"""

        context += """
**综合分析要点**:
1. 对比历史报告中的交易方向和当前交易方向，分析是否有趋势变化
2. 对比历史的内幕交易可能性评估，判断该市场是否持续有异常交易
3. 如果多份报告都指向同一方向，这可能加强信号的可信度
4. 如果报告方向相反，需要分析原因并给出更审慎的判断
5. 考虑时间因素：越近期的报告越有参考价值
"""
        return context
