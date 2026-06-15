"""Fundamentals data sourcing.

A provider-agnostic interface for fetching quarterly fundamentals (the raw
inputs needed to derive valuation multiples) plus an EOD Historical Data
(EODHD) implementation. Kept separate from the price fetcher so the underlying
source can be swapped without touching ingestion or the dashboard.

Trailing-twelve-month (TTM) figures are computed here from the quarterly
statements so the dashboard only has to divide price by these per-share values
to obtain P/E, P/B, P/S and EV/EBITDA over time.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod

import pandas as pd
import requests  # type: ignore

# Environment variable holding the EODHD API token.
EODHD_API_KEY_ENV = "EODHD_API_KEY"
EODHD_BASE_URL = "https://eodhd.com/api/fundamentals"

# Number of quarters in a trailing-twelve-month window.
TTM_QUARTERS = 4

# Map our internal market codes (as stored on ``tickers.market`` and used by
# ``format_yahoo_ticker``) to EODHD exchange suffixes. EODHD uses its own
# exchange codes, so these are best-effort and MUST be validated against the
# EODHD exchange list on the free trial -- China A-shares (SHG/SHE) and India
# (NSE/BSE) are the most likely to need adjustment. US listings use ``.US``.
EODHD_EXCHANGE_SUFFIX = {
    "LSE": ".LSE",
    "BME": ".MC",
    "XETR": ".XETRA",
    "GETTEX": ".XETRA",
    "MIL": ".MI",
    "SIX": ".SW",
    "TSX": ".TO",
    "OMXSTO": ".ST",
    "EURONEXT": ".PA",
    "LSIN": ".IL",
    "TSE": ".TSE",
    "HKEX": ".HK",
    "NSE": ".NSE",
    "BSE": ".BSE",
    "SSE": ".SHG",
    "SZSE": ".SHE",
}


def format_eodhd_ticker(symbol: str, market: str | None) -> str:
    """Return the EODHD ``SYMBOL.EXCHANGE`` code for a ticker.

    US listings (no/blank market) use the ``.US`` suffix; other markets use the
    mapping in :data:`EODHD_EXCHANGE_SUFFIX`, falling back to ``.US`` for
    unrecognised markets.
    """
    if not market:
        return f"{symbol}.US"
    suffix = EODHD_EXCHANGE_SUFFIX.get(market.upper(), ".US")
    return f"{symbol}{suffix}"


def _to_float(value) -> float | None:
    """Coerce an arbitrary JSON scalar to a plain float, or None if invalid."""
    try:
        if value is None:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _num(value) -> float | None:
    """Coerce a pandas/NumPy scalar to a plain float, mapping NaN/None to None."""
    if value is None or pd.isna(value):
        return None
    return float(value)


def _quarterly_frame(section: dict | None) -> pd.DataFrame:
    """Turn an EODHD ``{date: {field: value}}`` block into a date-indexed frame.

    Always returns a frame with a (possibly empty) ``DatetimeIndex`` so the
    callers can union indices safely.
    """
    if not isinstance(section, dict) or not section:
        return pd.DataFrame(index=pd.DatetimeIndex([]))
    df = pd.DataFrame.from_dict(section, orient="index")
    df.index = pd.to_datetime(df.index, errors="coerce")
    return df[df.index.notna()].sort_index()


def parse_eodhd_fundamentals(symbol: str, payload: dict) -> list[dict]:
    """Map an EODHD fundamentals payload to ``fundamentals_quarterly`` rows.

    Returns one dict per quarter that has at least one usable figure, sorted by
    ``period_end`` ascending. Quarters before a full TTM window has accumulated
    carry ``None`` for the TTM-derived fields (``eps_ttm``, ``sales_ps``,
    ``ebitda``). ``forward_eps`` and ``dps_ttm`` are current-snapshot estimates
    and are attached to the most recent quarter only.
    """
    if not isinstance(payload, dict) or not payload:
        return []

    financials = payload.get("Financials") or {}
    income_q = _quarterly_frame(
        (financials.get("Income_Statement") or {}).get("quarterly")
    )
    balance_q = _quarterly_frame(
        (financials.get("Balance_Sheet") or {}).get("quarterly")
    )
    earnings_hist = _quarterly_frame((payload.get("Earnings") or {}).get("History"))

    if income_q.empty and earnings_hist.empty:
        return []

    idx = income_q.index.union(balance_q.index).union(earnings_hist.index)

    def col(df: pd.DataFrame, name: str) -> pd.Series:
        if name not in df.columns:
            return pd.Series(index=idx, dtype="float64")
        return pd.to_numeric(df[name], errors="coerce").reindex(idx)

    eps_q = col(earnings_hist, "epsActual")
    revenue_q = col(income_q, "totalRevenue")
    ebitda_q = col(income_q, "ebitda")
    equity = col(balance_q, "totalStockholderEquity")
    net_debt = col(balance_q, "netDebt")
    shares = col(balance_q, "commonStockSharesOutstanding")

    # TTM = rolling sum of the trailing four quarters (needs four data points).
    eps_ttm = eps_q.rolling(TTM_QUARTERS).sum()
    revenue_ttm = revenue_q.rolling(TTM_QUARTERS).sum()
    ebitda_ttm = ebitda_q.rolling(TTM_QUARTERS).sum()

    positive_shares = shares.where(shares > 0)
    book_value_ps = equity / positive_shares
    sales_ps = revenue_ttm / positive_shares

    highlights = payload.get("Highlights") or {}
    splits_div = payload.get("SplitsDividends") or {}
    forward_eps = _to_float(highlights.get("EPSEstimateNextYear"))
    dps_ttm = _to_float(splits_div.get("ForwardAnnualDividendRate"))

    last_period = idx.max() if len(idx) else None
    records: list[dict] = []
    for period in idx:
        is_last = period == last_period
        rec = {
            "symbol": symbol,
            "period_end": period.date(),
            "eps_ttm": _num(eps_ttm.get(period)),
            "book_value_ps": _num(book_value_ps.get(period)),
            "sales_ps": _num(sales_ps.get(period)),
            "ebitda": _num(ebitda_ttm.get(period)),
            "net_debt": _num(net_debt.get(period)),
            "shares_out": _num(shares.get(period)),
            "dps_ttm": dps_ttm if is_last else None,
            "forward_eps": forward_eps if is_last else None,
        }
        has_data = any(
            rec[key] is not None
            for key in ("eps_ttm", "sales_ps", "book_value_ps", "ebitda", "shares_out")
        )
        if has_data:
            records.append(rec)
    return records


class FundamentalsProvider(ABC):
    """Source of quarterly fundamentals for a ticker."""

    @abstractmethod
    def get_fundamentals(self, symbol: str, market: str | None) -> list[dict]:
        """Return ``fundamentals_quarterly`` rows for ``symbol`` (may be empty)."""


class EODHDProvider(FundamentalsProvider):
    """EOD Historical Data (eodhd.com) fundamentals provider."""

    def __init__(self, api_key: str, *, timeout: int = 30) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def get_fundamentals(self, symbol: str, market: str | None) -> list[dict]:
        payload = self._request(format_eodhd_ticker(symbol, market))
        return parse_eodhd_fundamentals(symbol, payload)

    def _request(self, eodhd_ticker: str) -> dict:
        resp = requests.get(
            f"{EODHD_BASE_URL}/{eodhd_ticker}",
            params={"api_token": self.api_key, "fmt": "json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


def get_fundamentals_provider() -> FundamentalsProvider:
    """Build the configured fundamentals provider.

    Raises a clear, actionable error when the EODHD API key is not configured.
    """
    api_key = os.environ.get(EODHD_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(
            f"{EODHD_API_KEY_ENV} is not set. Sign up at https://eodhd.com, then "
            f"set the environment variable (PowerShell: "
            f"`$env:{EODHD_API_KEY_ENV}='<token>'`) before running "
            "`tradingtools-stock fetch valuation`."
        )
    return EODHDProvider(api_key)


def upsert_fundamentals(conn, records: list[dict]) -> int:
    """Upsert ``fundamentals_quarterly`` rows, returning the number written."""
    if not records:
        return 0

    from psycopg2.extras import execute_values

    columns = (
        "symbol",
        "period_end",
        "eps_ttm",
        "book_value_ps",
        "sales_ps",
        "ebitda",
        "net_debt",
        "shares_out",
        "dps_ttm",
        "forward_eps",
    )
    values = [tuple(record.get(col) for col in columns) for record in records]

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO fundamentals_quarterly
                (symbol, period_end, eps_ttm, book_value_ps, sales_ps,
                 ebitda, net_debt, shares_out, dps_ttm, forward_eps)
            VALUES %s
            ON CONFLICT (symbol, period_end) DO UPDATE SET
                eps_ttm = EXCLUDED.eps_ttm,
                book_value_ps = EXCLUDED.book_value_ps,
                sales_ps = EXCLUDED.sales_ps,
                ebitda = EXCLUDED.ebitda,
                net_debt = EXCLUDED.net_debt,
                shares_out = EXCLUDED.shares_out,
                dps_ttm = EXCLUDED.dps_ttm,
                forward_eps = EXCLUDED.forward_eps,
                updated_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        conn.commit()
    return len(records)
