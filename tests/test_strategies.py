import numpy as np
import pandas as pd
import pytest

from tradingtools_stock.core import strategies


def _synthetic_daily(n=300, start="2023-01-02"):
    """Build a deterministic daily OHLC frame indexed by date."""
    idx = pd.bdate_range(start=start, periods=n, name="date")
    close = pd.Series(np.linspace(100.0, 200.0, n), index=idx)
    df = pd.DataFrame(
        {
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
        },
        index=idx,
    )
    return df


def test_as_of_date_truncates_to_historical_bar(monkeypatch):
    df = _synthetic_daily()
    monkeypatch.setattr(strategies, "fetch_daily_data", lambda conn, sym: df.copy())

    as_of = df.index[200]
    result = strategies.get_dashboard_data(
        None, tickers_to_process=["TEST"], as_of_date=as_of.date()
    )

    # The computed snapshot must be anchored to the as-of bar, not the latest.
    assert result.iloc[0]["calc_date"] == as_of.date()


def test_as_of_date_none_uses_latest_bar(monkeypatch):
    df = _synthetic_daily()
    monkeypatch.setattr(strategies, "fetch_daily_data", lambda conn, sym: df.copy())

    result = strategies.get_dashboard_data(None, tickers_to_process=["TEST"])

    assert result.iloc[0]["calc_date"] == df.index[-1].date()


def test_as_of_date_on_non_trading_day_picks_prior_bar(monkeypatch):
    # Frame starts Mon 2023-01-02, so 2023-01-07 is a Saturday (no bar) and
    # the prior trading bar is Fri 2023-01-06.
    df = _synthetic_daily()
    monkeypatch.setattr(strategies, "fetch_daily_data", lambda conn, sym: df.copy())

    result = strategies.get_dashboard_data(
        None, tickers_to_process=["TEST"], as_of_date="2023-01-07"
    )

    # <= truncation falls back to the last available trading bar.
    assert result.iloc[0]["calc_date"] == pd.Timestamp("2023-01-06").date()


def test_1000_sma_split_into_value_and_touch_days(monkeypatch):
    # The old combined "1000 SMA Touch" string column is replaced by a numeric
    # "1000 SMA" value and an integer "1000 SMA Touch Days" column.
    df = _synthetic_daily()
    monkeypatch.setattr(strategies, "fetch_daily_data", lambda conn, sym: df.copy())

    result = strategies.get_dashboard_data(None, tickers_to_process=["TEST"])

    assert "1000 SMA" in result.columns
    assert "1000 SMA Touch Days" in result.columns
    assert "1000 SMA Touch" not in result.columns


def test_1000_sma_touch_days_populated_on_touch(monkeypatch):
    # A flat price series sits on its own 1000-day SMA, so the most recent bar
    # registers a touch (0 days ago) and the SMA value is the flat price.
    idx = pd.bdate_range(start="2019-01-01", periods=1100, name="date")
    close = pd.Series(100.0, index=idx)
    df = pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close},
        index=idx,
    )
    monkeypatch.setattr(strategies, "fetch_daily_data", lambda conn, sym: df.copy())

    row = strategies.get_dashboard_data(None, tickers_to_process=["TEST"]).iloc[0]

    assert row["1000 SMA Touch Days"] == 0
    assert row["1000 SMA"] == pytest.approx(100.0)
