"""Fundamentals data sourcing.

A provider-agnostic interface for fetching a stock's historical valuation
multiples, plus a Yahoo Finance implementation (via ``yahooquery``). Kept
separate from the price fetcher so the underlying source can be swapped for a
paid, deeper-history provider later without touching ingestion or the
dashboard.

Yahoo's ``valuation_measures`` endpoint already returns *pre-computed* quarterly
valuation ratios (~5 years), so this module simply maps that payload onto
``valuation_history`` rows; no ratio maths is required here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import pandas as pd

from tradingtools_stock.core.fetcher import format_yahoo_ticker

# yahooquery ``valuation_measures`` spells the EV ratios with a trailing
# "Enterprises"; accept the singular form too in case the library changes it.
_EV_EBITDA_COLS = ("EnterprisesValueEBITDARatio", "EnterpriseValueEBITDARatio")
_EV_REVENUE_COLS = ("EnterprisesValueRevenueRatio", "EnterpriseValueRevenueRatio")


def _to_float(value) -> float | None:
    """Coerce an arbitrary scalar to a plain float, or None if invalid/NaN."""
    try:
        if value is None:
            return None
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if pd.notna(result) else None


def _pick(row: pd.Series, *names: str) -> float | None:
    """Return the first present, numeric value among ``names`` on ``row``."""
    for name in names:
        if name in row.index:
            value = _to_float(row.get(name))
            if value is not None:
                return value
    return None


def parse_valuation_measures(symbol: str, data) -> list[dict]:
    """Map a yahooquery ``valuation_measures`` frame to ``valuation_history`` rows.

    Returns one dict per quarter, sorted by ``as_of_date`` ascending. yahooquery
    returns a string (an error message) rather than a frame when a ticker has no
    fundamentals; that and an empty frame both yield ``[]``.
    """
    if not isinstance(data, pd.DataFrame) or data.empty:
        return []
    if "asOfDate" not in data.columns:
        return []

    df = data.copy()
    df["asOfDate"] = pd.to_datetime(df["asOfDate"], errors="coerce")
    df = df[df["asOfDate"].notna()].sort_values("asOfDate")

    records: list[dict] = []
    for _, row in df.iterrows():
        period_type = row.get("periodType")
        records.append(
            {
                "symbol": symbol,
                "as_of_date": row["asOfDate"].date(),
                "period_type": str(period_type) if pd.notna(period_type) else "",
                "trailing_pe": _pick(row, "PeRatio"),
                "forward_pe": _pick(row, "ForwardPeRatio"),
                "pb": _pick(row, "PbRatio"),
                "ps": _pick(row, "PsRatio"),
                "peg": _pick(row, "PegRatio"),
                "ev_ebitda": _pick(row, *_EV_EBITDA_COLS),
                "ev_revenue": _pick(row, *_EV_REVENUE_COLS),
                "market_cap": _pick(row, "MarketCap"),
                "enterprise_value": _pick(row, "EnterpriseValue"),
                "dividend_yield": None,
            }
        )
    return records


def _extract_dividend_yield(ticker, yahoo_ticker: str) -> float | None:
    """Pull the current dividend yield from a yahooquery ``summary_detail``."""
    try:
        summary = ticker.summary_detail
    except Exception:
        return None
    info = summary.get(yahoo_ticker) if isinstance(summary, dict) else None
    if isinstance(info, dict):
        return _to_float(info.get("dividendYield"))
    return None


class FundamentalsProvider(ABC):
    """Source of historical valuation multiples for a ticker."""

    @abstractmethod
    def get_fundamentals(self, symbol: str, market: str | None) -> list[dict]:
        """Return ``valuation_history`` rows for ``symbol`` (may be empty)."""


def _default_ticker_factory(yahoo_ticker: str):
    # Imported lazily so the parser helpers stay importable (and unit-testable)
    # without pulling in yahooquery.
    import yahooquery as yq

    return yq.Ticker(yahoo_ticker)


class YahooFundamentalsProvider(FundamentalsProvider):
    """Fundamentals from Yahoo Finance via ``yahooquery`` (no API key)."""

    def __init__(self, ticker_factory: Callable[[str], Any] | None = None) -> None:
        self._ticker_factory = ticker_factory or _default_ticker_factory

    def get_fundamentals(self, symbol: str, market: str | None) -> list[dict]:
        yahoo_ticker = format_yahoo_ticker(symbol, market or "")
        ticker = self._ticker_factory(yahoo_ticker)
        records = parse_valuation_measures(symbol, ticker.valuation_measures)
        if records:
            # Dividend yield is not part of valuation_measures; attach the
            # current snapshot to the most recent quarter only.
            dividend_yield = _extract_dividend_yield(ticker, yahoo_ticker)
            if dividend_yield is not None:
                records[-1]["dividend_yield"] = dividend_yield
        return records


def get_fundamentals_provider() -> FundamentalsProvider:
    """Build the configured fundamentals provider (Yahoo Finance; no credentials)."""
    return YahooFundamentalsProvider()


def upsert_valuation_history(conn, records: list[dict]) -> int:
    """Upsert ``valuation_history`` rows, returning the number written."""
    if not records:
        return 0

    from psycopg2.extras import execute_values

    columns = (
        "symbol",
        "as_of_date",
        "period_type",
        "trailing_pe",
        "forward_pe",
        "pb",
        "ps",
        "peg",
        "ev_ebitda",
        "ev_revenue",
        "market_cap",
        "enterprise_value",
        "dividend_yield",
    )
    values = [tuple(record.get(col) for col in columns) for record in records]

    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO valuation_history
                (symbol, as_of_date, period_type, trailing_pe, forward_pe, pb, ps,
                 peg, ev_ebitda, ev_revenue, market_cap, enterprise_value,
                 dividend_yield)
            VALUES %s
            ON CONFLICT (symbol, as_of_date, period_type) DO UPDATE SET
                trailing_pe = EXCLUDED.trailing_pe,
                forward_pe = EXCLUDED.forward_pe,
                pb = EXCLUDED.pb,
                ps = EXCLUDED.ps,
                peg = EXCLUDED.peg,
                ev_ebitda = EXCLUDED.ev_ebitda,
                ev_revenue = EXCLUDED.ev_revenue,
                market_cap = EXCLUDED.market_cap,
                enterprise_value = EXCLUDED.enterprise_value,
                dividend_yield = EXCLUDED.dividend_yield,
                updated_at = CURRENT_TIMESTAMP
            """,
            values,
        )
        conn.commit()
    return len(records)
