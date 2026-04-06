"""FRED (Federal Reserve Economic Data) API service for macroeconomic indicators."""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

FRED_API = "https://api.stlouisfed.org/fred"

# Common series IDs for prediction market analysis
SERIES_MAP = {
    # Interest rates
    "fed_funds_rate": "FEDFUNDS",
    "fed_rate": "FEDFUNDS",
    "interest_rate": "FEDFUNDS",
    "10y_treasury": "DGS10",
    "2y_treasury": "DGS2",
    "30y_mortgage": "MORTGAGE30US",
    # Inflation
    "cpi": "CPIAUCSL",
    "core_cpi": "CPILFESL",
    "pce": "PCEPI",
    "core_pce": "PCEPILFE",
    "inflation": "CPIAUCSL",
    # Employment
    "unemployment": "UNRATE",
    "unemployment_rate": "UNRATE",
    "nonfarm_payrolls": "PAYEMS",
    "jobs": "PAYEMS",
    "initial_claims": "ICSA",
    "jobless_claims": "ICSA",
    # GDP
    "gdp": "GDP",
    "real_gdp": "GDPC1",
    "gdp_growth": "A191RL1Q225SBEA",
    # Markets / Financial conditions
    "sp500": "SP500",
    "vix": "VIXCLS",
    "yield_curve": "T10Y2Y",
    "financial_stress": "STLFSI2",
    # Dollar
    "dollar_index": "DTWEXBGS",
    "usd": "DTWEXBGS",
    # Oil / Commodities
    "oil_price": "DCOILWTICO",
    "wti": "DCOILWTICO",
    "crude_oil": "DCOILWTICO",
    "brent": "DCOILBRENTEU",
    "gas_price": "GASREGW",
    "gold": "GOLDAMGBD228NLBM",
}


def _resolve_series_id(query: str) -> str:
    """Resolve a common name to a FRED series ID."""
    q = query.strip().lower().replace(" ", "_")
    if q in SERIES_MAP:
        return SERIES_MAP[q]
    # If already looks like a FRED series ID (uppercase), use as-is
    return query.strip().upper()


class FREDService:
    """
    FRED API client for macroeconomic data.

    Free, unlimited usage with API key.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.Client(timeout=15.0)

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def get_series(self, query: str, num_observations: int = 10) -> str:
        """
        Get recent observations for an economic data series.

        Args:
            query: Common name (e.g. 'fed_rate', 'cpi', 'unemployment', 'oil_price')
                   or a FRED series ID (e.g. 'FEDFUNDS', 'UNRATE')
            num_observations: Number of recent data points to return

        Returns:
            Formatted report with series info and recent values.
        """
        series_id = _resolve_series_id(query)

        try:
            # Get series metadata
            meta_resp = self._client.get(
                f"{FRED_API}/series",
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                },
            )

            if meta_resp.status_code == 400:
                return (
                    f"Series '{query}' (id: {series_id}) not found on FRED. "
                    f"Common names: fed_rate, cpi, unemployment, gdp, oil_price, "
                    f"vix, yield_curve, gold, sp500, jobless_claims"
                )
            meta_resp.raise_for_status()
            meta = meta_resp.json().get("seriess", [{}])[0]

            title = meta.get("title", series_id)
            frequency = meta.get("frequency", "")
            units = meta.get("units", "")
            last_updated = meta.get("last_updated", "")

            # Get recent observations
            obs_resp = self._client.get(
                f"{FRED_API}/series/observations",
                params={
                    "series_id": series_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": num_observations,
                },
            )
            obs_resp.raise_for_status()
            observations = obs_resp.json().get("observations", [])

            lines = [
                f"--- FRED: {title} ({series_id}) ---",
                f"Units: {units}",
                f"Frequency: {frequency}",
                f"Last Updated: {last_updated}",
                "",
                "Recent Data:",
            ]

            for obs in reversed(observations):
                date = obs.get("date", "")
                value = obs.get("value", ".")
                if value == ".":
                    lines.append(f"  {date}: N/A")
                else:
                    try:
                        v = float(value)
                        lines.append(f"  {date}: {v:,.2f}")
                    except ValueError:
                        lines.append(f"  {date}: {value}")

            # Add trend info if enough data
            valid_vals = []
            for obs in observations:
                v = obs.get("value", ".")
                if v != ".":
                    try:
                        valid_vals.append(float(v))
                    except ValueError:
                        pass

            if len(valid_vals) >= 2:
                latest = valid_vals[0]
                prev = valid_vals[1]
                change = latest - prev
                pct = (change / abs(prev) * 100) if prev != 0 else 0
                lines.append(f"\nLatest vs Previous: {change:+.2f} ({pct:+.2f}%)")

            lines.append("---")
            return "\n".join(lines)

        except httpx.HTTPError as e:
            msg = f"FRED API error for '{query}' ({series_id}): {e}"
            logger.error(msg)
            return msg
        except Exception as e:
            msg = f"FRED query failed for '{query}': {e}"
            logger.error(msg)
            return msg
