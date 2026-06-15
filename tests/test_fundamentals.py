import pytest
from typer.testing import CliRunner

from tradingtools_stock.core.fundamentals import (
    EODHD_API_KEY_ENV,
    EODHDProvider,
    format_eodhd_ticker,
    get_fundamentals_provider,
    parse_eodhd_fundamentals,
)
from tradingtools_stock.main import app

# Five consecutive quarters. EPS is flat at 1.0 for the first four quarters and
# jumps to 2.0 in the last, so the trailing-twelve-month roll-ups are easy to
# verify by hand.
QUARTERS = [
    "2023-03-31",
    "2023-06-30",
    "2023-09-30",
    "2023-12-31",
    "2024-03-31",
]


def _sample_payload() -> dict:
    eps = [1.0, 1.0, 1.0, 1.0, 2.0]
    revenue = [100.0, 100.0, 100.0, 100.0, 200.0]
    ebitda = [50.0, 50.0, 50.0, 50.0, 60.0]
    return {
        "Highlights": {"EPSEstimateNextYear": 5.0},
        "SplitsDividends": {"ForwardAnnualDividendRate": 0.5},
        "Earnings": {
            "History": {q: {"epsActual": eps[i]} for i, q in enumerate(QUARTERS)}
        },
        "Financials": {
            "Income_Statement": {
                "quarterly": {
                    q: {"totalRevenue": revenue[i], "ebitda": ebitda[i]}
                    for i, q in enumerate(QUARTERS)
                }
            },
            "Balance_Sheet": {
                "quarterly": {
                    q: {
                        "totalStockholderEquity": 1000.0,
                        "netDebt": 200.0,
                        "commonStockSharesOutstanding": 100.0,
                    }
                    for q in QUARTERS
                }
            },
        },
    }


def test_format_eodhd_ticker_us_default() -> None:
    assert format_eodhd_ticker("AAPL", None) == "AAPL.US"
    assert format_eodhd_ticker("AAPL", "") == "AAPL.US"


def test_format_eodhd_ticker_known_markets() -> None:
    assert format_eodhd_ticker("VOD", "LSE") == "VOD.LSE"
    assert format_eodhd_ticker("BMW", "XETR") == "BMW.XETRA"
    assert format_eodhd_ticker("SAN", "BME") == "SAN.MC"


def test_format_eodhd_ticker_unknown_market_falls_back_to_us() -> None:
    assert format_eodhd_ticker("FOO", "NOPE") == "FOO.US"


def test_parse_returns_one_row_per_quarter_sorted() -> None:
    records = parse_eodhd_fundamentals("AAPL", _sample_payload())
    assert [r["period_end"].isoformat() for r in records] == QUARTERS


def test_parse_computes_ttm_for_latest_quarter() -> None:
    records = parse_eodhd_fundamentals("AAPL", _sample_payload())
    latest = records[-1]
    assert latest["eps_ttm"] == pytest.approx(5.0)  # 1+1+1+2
    assert latest["sales_ps"] == pytest.approx(5.0)  # (100+100+100+200)/100
    assert latest["ebitda"] == pytest.approx(210.0)  # 50+50+50+60
    assert latest["book_value_ps"] == pytest.approx(10.0)  # 1000/100
    assert latest["net_debt"] == pytest.approx(200.0)
    assert latest["shares_out"] == pytest.approx(100.0)


def test_parse_attaches_snapshot_estimates_to_latest_only() -> None:
    records = parse_eodhd_fundamentals("AAPL", _sample_payload())
    assert records[-1]["forward_eps"] == pytest.approx(5.0)
    assert records[-1]["dps_ttm"] == pytest.approx(0.5)
    assert records[0]["forward_eps"] is None
    assert records[0]["dps_ttm"] is None


def test_parse_leaves_ttm_none_before_full_window() -> None:
    records = parse_eodhd_fundamentals("AAPL", _sample_payload())
    first = records[0]  # only one quarter of history -> no TTM yet
    assert first["eps_ttm"] is None
    assert first["sales_ps"] is None
    assert first["book_value_ps"] == pytest.approx(10.0)  # not TTM-based


def test_parse_empty_or_irrelevant_payload_returns_empty() -> None:
    assert parse_eodhd_fundamentals("AAPL", {}) == []
    assert parse_eodhd_fundamentals("AAPL", {"General": {"Name": "x"}}) == []


def test_get_provider_without_key_raises_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(EODHD_API_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError) as exc:
        get_fundamentals_provider()
    assert EODHD_API_KEY_ENV in str(exc.value)


def test_get_provider_with_key_returns_eodhd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(EODHD_API_KEY_ENV, "token-123")
    provider = get_fundamentals_provider()
    assert isinstance(provider, EODHDProvider)
    assert provider.api_key == "token-123"


def test_provider_get_fundamentals_parses_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = EODHDProvider("token-123")
    monkeypatch.setattr(provider, "_request", lambda ticker: _sample_payload())
    records = provider.get_fundamentals("AAPL", None)
    assert len(records) == len(QUARTERS)
    assert records[-1]["eps_ttm"] == pytest.approx(5.0)


def test_valuation_command_help(runner: CliRunner) -> None:
    result = runner.invoke(app, ["fetch", "valuation", "--help"])
    assert result.exit_code == 0
    assert "fundamentals" in result.stdout.lower()


def test_valuation_command_without_key_exits(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(EODHD_API_KEY_ENV, raising=False)
    result = runner.invoke(app, ["fetch", "valuation"])
    assert result.exit_code == 1
    assert EODHD_API_KEY_ENV in result.stdout
