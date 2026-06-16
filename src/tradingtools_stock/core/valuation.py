"""Read and summarise stored valuation history for the dashboard Valuation tab.

The ratios themselves are sourced upstream by ``fetch valuation`` and stored in
``valuation_history``; this module only reads them back, picks one row per
quarter, and derives sector medians and distribution stats for display. A ratio
is treated as meaningful only when it is present and strictly positive (a
non-positive P/E etc. signals a loss and is shown as N/A rather than charted).
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable

import pandas as pd

# All stored metrics with their display labels.
VALUATION_METRICS: dict[str, str] = {
    "trailing_pe": "Trailing P/E",
    "forward_pe": "Forward P/E",
    "pb": "P/B",
    "ps": "P/S",
    "peg": "PEG",
    "ev_ebitda": "EV/EBITDA",
    "ev_revenue": "EV/Revenue",
    "dividend_yield": "Dividend Yield",
}

# Metrics with meaningful quarterly history to offer in the chart selector.
CHARTABLE_METRICS: dict[str, str] = {
    "trailing_pe": "Trailing P/E",
    "forward_pe": "Forward P/E",
    "pb": "P/B",
    "ps": "P/S",
    "ev_ebitda": "EV/EBITDA",
    "ev_revenue": "EV/Revenue",
}

# Metrics where a lower value is "cheaper" (used for colour direction). Every
# multiple is lower-is-cheaper; dividend yield is the exception (higher is more
# income) and is excluded here.
LOWER_IS_CHEAPER = set(VALUATION_METRICS) - {"dividend_yield"}

_RATIO_COLUMNS = (
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
_V_COLUMNS = "v.symbol, v.as_of_date, v.period_type, " + ", ".join(
    f"v.{c}" for c in _RATIO_COLUMNS
)


def _read_sql(query: str, conn, params=None) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql_query(query, conn, params=params, parse_dates=["as_of_date"])


def _dedupe_periods(
    df: pd.DataFrame, subset: Iterable[str] = ("as_of_date",)
) -> pd.DataFrame:
    """Keep one row per ``subset``, preferring the ``TTM`` period when present."""
    subset = list(subset)
    if df.empty or "period_type" not in df.columns:
        return df.reset_index(drop=True)
    ranked = df.assign(_ttm=(df["period_type"] == "TTM").astype(int))
    return (
        ranked.sort_values([*subset, "_ttm"])
        .drop_duplicates(subset=subset, keep="last")
        .drop(columns="_ttm")
        .sort_values("as_of_date")
        .reset_index(drop=True)
    )


def fetch_valuation_history(conn, symbol: str) -> pd.DataFrame:
    """Quarterly valuation series for one ticker (deduped, oldest first)."""
    query = (
        f"SELECT {_V_COLUMNS} FROM valuation_history v "
        "WHERE v.symbol = %s ORDER BY v.as_of_date"
    )
    return _dedupe_periods(_read_sql(query, conn, (symbol,)))


def fetch_latest_valuation(conn) -> pd.DataFrame:
    """Latest valuation row per active ticker, enriched with sector/industry."""
    query = f"""
        SELECT DISTINCT ON (v.symbol)
            {_V_COLUMNS},
            COALESCE(t.sector, 'Unknown') AS sector,
            COALESCE(t.industry, 'Unknown') AS industry
        FROM valuation_history v
        JOIN tickers t ON v.symbol = t.symbol
        WHERE t.active = true
        ORDER BY v.symbol, v.as_of_date DESC, (v.period_type = 'TTM') DESC
    """
    return _read_sql(query, conn)


def fetch_sector_valuation_history(conn, sector: str) -> pd.DataFrame:
    """Full valuation history for every active ticker in ``sector``."""
    query = f"""
        SELECT {_V_COLUMNS}
        FROM valuation_history v
        JOIN tickers t ON v.symbol = t.symbol
        WHERE t.active = true AND COALESCE(t.sector, 'Unknown') = %s
        ORDER BY v.symbol, v.as_of_date
    """
    return _read_sql(query, conn, (sector,))


def _positive(series) -> pd.Series:
    """Numeric series with only strictly-positive values retained."""
    values = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return values[values > 0]


def compute_stats(series) -> dict | None:
    """Distribution stats for a date-ordered metric series, or None if empty.

    ``current`` is the most recent positive value; ``percentile`` is its rank
    within the positive history (0–100).
    """
    positive = _positive(series)
    if positive.empty:
        return None
    current = float(positive.iloc[-1])
    return {
        "current": current,
        "mean": float(positive.mean()),
        "min": float(positive.min()),
        "max": float(positive.max()),
        "percentile": float((positive <= current).mean() * 100.0),
        "count": int(positive.size),
    }


def sector_median(latest_df: pd.DataFrame, sector: str, metric: str) -> float | None:
    """Median of ``metric`` across positive values in ``sector`` (latest rows)."""
    if latest_df.empty or metric not in latest_df.columns:
        return None
    rows = latest_df[latest_df["sector"] == sector]
    values = _positive(rows[metric])
    return float(values.median()) if not values.empty else None


def sector_sample_size(latest_df: pd.DataFrame, sector: str, metric: str) -> int:
    """How many positive ``metric`` values back the sector median."""
    if latest_df.empty or metric not in latest_df.columns:
        return 0
    rows = latest_df[latest_df["sector"] == sector]
    return int(_positive(rows[metric]).size)


def compute_sector_median_series(sector_hist: pd.DataFrame, metric: str) -> pd.Series:
    """Median of ``metric`` per quarter across a sector's tickers (positive only)."""
    if sector_hist.empty or metric not in sector_hist.columns:
        return pd.Series(dtype="float64")
    deduped = _dedupe_periods(sector_hist, subset=["symbol", "as_of_date"])
    values = pd.to_numeric(deduped[metric], errors="coerce")
    frame = deduped.assign(_v=values)
    frame = frame[frame["_v"] > 0]
    if frame.empty:
        return pd.Series(dtype="float64")
    return frame.groupby("as_of_date")["_v"].median().sort_index()
