import pandas as pd
import pytest

from tradingtools_stock.core.valuation import (
    STOCK_TABLE_METRICS,
    _dedupe_periods,
    aggregate_valuation,
    compute_sector_median_series,
    compute_stats,
    sector_median,
    sector_sample_size,
    stocks_table,
)


def test_dedupe_periods_prefers_ttm() -> None:
    df = pd.DataFrame(
        {
            "as_of_date": pd.to_datetime(["2024-03-31", "2024-03-31", "2023-12-31"]),
            "period_type": ["3M", "TTM", "TTM"],
            "trailing_pe": [99.0, 28.0, 25.0],
        }
    )
    out = _dedupe_periods(df)
    assert list(out["as_of_date"].dt.strftime("%Y-%m-%d")) == [
        "2023-12-31",
        "2024-03-31",
    ]
    # The 2024-03-31 TTM row (28.0) wins over the 3M row (99.0).
    assert out.iloc[-1]["trailing_pe"] == 28.0


def test_compute_stats_basic() -> None:
    stats = compute_stats([10.0, 20.0, 30.0, 25.0])
    assert stats is not None
    assert stats["current"] == pytest.approx(25.0)
    assert stats["mean"] == pytest.approx(21.25)
    assert stats["min"] == pytest.approx(10.0)
    assert stats["max"] == pytest.approx(30.0)
    assert stats["percentile"] == pytest.approx(75.0)  # 10, 20, 25 are <= 25
    assert stats["count"] == 4


def test_compute_stats_ignores_non_positive_and_nulls() -> None:
    stats = compute_stats([None, -5.0, 15.0, 20.0])
    assert stats is not None
    assert stats["current"] == pytest.approx(20.0)
    assert stats["min"] == pytest.approx(15.0)
    assert stats["count"] == 2
    assert stats["percentile"] == pytest.approx(100.0)


def test_compute_stats_returns_none_when_no_positive_values() -> None:
    assert compute_stats([]) is None
    assert compute_stats([None, -1.0, 0.0]) is None


def _latest_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC", "DDD"],
            "sector": ["Tech", "Tech", "Tech", "Energy"],
            # CCC is loss-making (negative P/E) -> excluded from the median.
            "trailing_pe": [20.0, 30.0, -5.0, 12.0],
        }
    )


def test_sector_median_excludes_loss_makers() -> None:
    # Only AAA (20) and BBB (30) are positive -> median 25.
    assert sector_median(_latest_df(), "Tech", "trailing_pe") == pytest.approx(25.0)
    assert sector_sample_size(_latest_df(), "Tech", "trailing_pe") == 2


def test_sector_median_none_when_no_valid_values() -> None:
    df = pd.DataFrame({"symbol": ["X"], "sector": ["Tech"], "trailing_pe": [-3.0]})
    assert sector_median(df, "Tech", "trailing_pe") is None
    assert sector_sample_size(df, "Tech", "trailing_pe") == 0


def _scope_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC"],
            "sector": ["Tech", "Tech", "Tech"],
            "trailing_pe": [10.0, 20.0, -5.0],  # CCC loss -> excluded from median
            "forward_pe": [8.0, 16.0, 12.0],
            "pb": [1.0, 3.0, 2.0],
            "ps": [2.0, 4.0, 6.0],
            "peg": [1.0, 2.0, 3.0],
            "ev_ebitda": [5.0, 7.0, 9.0],
            "ev_revenue": [1.5, 2.5, 3.5],
            "dividend_yield": [0.01, 0.02, 0.03],
            "market_cap": [3e11, 1e11, 2e11],
        }
    )


def test_aggregate_valuation_medians_and_count() -> None:
    agg = aggregate_valuation(_scope_df())
    assert agg["count"] == 3
    assert agg["trailing_pe"] == pytest.approx(15.0)  # median of 10, 20
    assert agg["forward_pe"] == pytest.approx(12.0)
    assert agg["dividend_yield"] == pytest.approx(0.02)


def test_aggregate_valuation_empty() -> None:
    agg = aggregate_valuation(pd.DataFrame())
    assert agg["count"] == 0
    assert agg["trailing_pe"] is None


def test_stocks_table_sorted_by_ticker() -> None:
    table = stocks_table(_scope_df())
    assert list(table["symbol"]) == ["AAA", "BBB", "CCC"]
    assert "market_cap" not in table.columns
    for metric in STOCK_TABLE_METRICS:
        assert metric in table.columns


def test_stocks_table_empty_has_columns() -> None:
    table = stocks_table(pd.DataFrame())
    assert table.empty
    assert "symbol" in table.columns
    assert "trailing_pe" in table.columns


def test_compute_sector_median_series_per_quarter() -> None:
    hist = pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "AAA", "BBB"],
            "as_of_date": pd.to_datetime(
                ["2024-03-31", "2024-03-31", "2023-12-31", "2023-12-31"]
            ),
            "period_type": ["TTM", "TTM", "TTM", "TTM"],
            "trailing_pe": [20.0, 30.0, 10.0, 40.0],
        }
    )
    series = compute_sector_median_series(hist, "trailing_pe")
    assert series.loc[pd.Timestamp("2024-03-31")] == pytest.approx(25.0)
    assert series.loc[pd.Timestamp("2023-12-31")] == pytest.approx(25.0)
