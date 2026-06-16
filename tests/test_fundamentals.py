import pandas as pd
import pytest
from typer.testing import CliRunner

from tradingtools_stock.core.fundamentals import (
    YahooFundamentalsProvider,
    get_fundamentals_provider,
    parse_valuation_measures,
)
from tradingtools_stock.main import app


def _sample_valuation_measures() -> pd.DataFrame:
    """Two quarters shaped like yahooquery's ``valuation_measures`` output."""
    return pd.DataFrame(
        [
            {
                "symbol": "AAPL",
                "asOfDate": "2023-12-31",
                "periodType": "TTM",
                "PeRatio": 25.0,
                "ForwardPeRatio": 22.0,
                "PbRatio": 40.0,
                "PsRatio": 7.0,
                "PegRatio": 2.0,
                "EnterprisesValueEBITDARatio": 18.0,
                "EnterprisesValueRevenueRatio": 6.5,
                "MarketCap": 3.0e12,
                "EnterpriseValue": 3.1e12,
            },
            {
                "symbol": "AAPL",
                "asOfDate": "2024-03-31",
                "periodType": "TTM",
                "PeRatio": 28.0,
                "ForwardPeRatio": 24.0,
                "PbRatio": 42.0,
                "PsRatio": 7.5,
                "PegRatio": 2.2,
                "EnterprisesValueEBITDARatio": 19.0,
                "EnterprisesValueRevenueRatio": 6.8,
                "MarketCap": 3.2e12,
                "EnterpriseValue": 3.3e12,
            },
        ]
    )


class _FakeTicker:
    def __init__(self, valuation_measures, summary_detail):
        self.valuation_measures = valuation_measures
        self.summary_detail = summary_detail


def test_parse_returns_rows_sorted_ascending() -> None:
    records = parse_valuation_measures("AAPL", _sample_valuation_measures())
    assert [r["as_of_date"].isoformat() for r in records] == [
        "2023-12-31",
        "2024-03-31",
    ]


def test_parse_maps_all_ratio_fields() -> None:
    latest = parse_valuation_measures("AAPL", _sample_valuation_measures())[-1]
    assert latest["symbol"] == "AAPL"
    assert latest["period_type"] == "TTM"
    assert latest["trailing_pe"] == pytest.approx(28.0)
    assert latest["forward_pe"] == pytest.approx(24.0)
    assert latest["pb"] == pytest.approx(42.0)
    assert latest["ps"] == pytest.approx(7.5)
    assert latest["peg"] == pytest.approx(2.2)
    assert latest["ev_ebitda"] == pytest.approx(19.0)
    assert latest["ev_revenue"] == pytest.approx(6.8)
    assert latest["market_cap"] == pytest.approx(3.2e12)
    assert latest["enterprise_value"] == pytest.approx(3.3e12)


def test_parse_tolerates_singular_ev_column_spelling() -> None:
    df = _sample_valuation_measures().rename(
        columns={
            "EnterprisesValueEBITDARatio": "EnterpriseValueEBITDARatio",
            "EnterprisesValueRevenueRatio": "EnterpriseValueRevenueRatio",
        }
    )
    latest = parse_valuation_measures("AAPL", df)[-1]
    assert latest["ev_ebitda"] == pytest.approx(19.0)
    assert latest["ev_revenue"] == pytest.approx(6.8)


def test_parse_non_dataframe_or_empty_returns_empty() -> None:
    # yahooquery returns an error string when a ticker has no fundamentals.
    assert parse_valuation_measures("AAPL", "NVDA: No fundamentals data found") == []
    assert parse_valuation_measures("AAPL", pd.DataFrame()) == []
    assert parse_valuation_measures("AAPL", pd.DataFrame({"foo": [1]})) == []


def test_get_fundamentals_provider_is_yahoo_without_credentials() -> None:
    provider = get_fundamentals_provider()
    assert isinstance(provider, YahooFundamentalsProvider)


def test_provider_parses_and_attaches_dividend_yield() -> None:
    summary = {"AAPL": {"dividendYield": 0.005}}
    provider = YahooFundamentalsProvider(
        ticker_factory=lambda t: _FakeTicker(_sample_valuation_measures(), summary)
    )
    records = provider.get_fundamentals("AAPL", None)
    assert len(records) == 2
    # Dividend yield is a snapshot -> only on the most recent quarter.
    assert records[-1]["dividend_yield"] == pytest.approx(0.005)
    assert records[0]["dividend_yield"] is None


def test_provider_empty_coverage_returns_empty() -> None:
    provider = YahooFundamentalsProvider(
        ticker_factory=lambda t: _FakeTicker("no data", {})
    )
    assert provider.get_fundamentals("AAPL", None) == []


def test_valuation_command_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["fetch", "valuation", "--help"])
    assert result.exit_code == 0
    assert "valuation" in result.stdout.lower()
