import pandas as pd
import pytest

from tradingtools_stock.core import screener


def _dashboard():
    """Two sectors: Tech (strong/broad) and Energy (weak/narrow)."""
    return pd.DataFrame(
        {
            "Ticker": ["AAA", "BBB", "CCC", "DDD"],
            "Sector": ["Tech", "Tech", "Energy", "Energy"],
            "Signal": [
                "🟢 Strong Entry",
                "🟡 Weak Entry",
                "🟢 Strong Entry",
                "⚪ None",
            ],
            # Tech well above 200 SMA (broad, green); Energy below (narrow).
            "Price": [120.0, 110.0, 80.0, 70.0],
            "200 SMA": [100.0, 100.0, 100.0, 100.0],
            "1M Trend": ["🟥 🟩 🟩", "🟩 🟩 🟩", "🟥 🟥 🟥", "🟥 🟥 🟥"],
        }
    )


def _valuation():
    return pd.DataFrame(
        {
            "symbol": ["AAA", "BBB", "CCC", "DDD"],
            "forward_pe": [18.0, 30.0, 12.0, 15.0],
            "trailing_pe": [25.0, 28.0, 20.0, 14.0],
            "peg": [0.9, 1.4, 1.8, 2.5],
        }
    )


def test_sector_aggregates_breadth_and_ha():
    agg = screener.sector_aggregates(_dashboard()).set_index("Sector")
    # Tech: both above 200 SMA -> breadth 100%; both 1M end green -> 100%.
    assert agg.loc["Tech", "breadth"] == pytest.approx(100.0)
    assert agg.loc["Tech", "ha_green"] == pytest.approx(100.0)
    # Energy: none above 200 -> breadth 0; none green -> 0.
    assert agg.loc["Energy", "breadth"] == pytest.approx(0.0)
    assert agg.loc["Energy", "ha_green"] == pytest.approx(0.0)
    # RS = median % vs 200 SMA (Tech +15%, Energy -25%).
    assert agg.loc["Tech", "rs"] == pytest.approx(15.0)
    assert agg.loc["Energy", "rs"] == pytest.approx(-25.0)


def test_qualifying_sectors_early_rotation():
    agg = screener.sector_aggregates(_dashboard())
    quals = screener.qualifying_sectors(
        agg, screener.EARLY_ROTATION, screener.DEFAULT_THRESHOLDS
    )
    # Tech passes breadth>=40 & ha>=20; Energy fails both.
    assert quals == ["Tech"]


def test_qualifying_sectors_trend_pullback_excludes_high_ha():
    agg = screener.sector_aggregates(_dashboard())
    quals = screener.qualifying_sectors(
        agg, screener.TREND_PULLBACK, screener.DEFAULT_THRESHOLDS
    )
    # Tech has RS>=5 & breadth>=60 but ha_green 100% > 15% cap -> excluded.
    assert quals == []


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # PEG 25 + accel 25 + breadth 25 + rotation HA 25 = 100
        (
            dict(peg=0.8, forward_pe=10, trailing_pe=20, breadth=70, ha_green=30),
            100,
        ),
        # PEG 15 (<=1.5) + no accel (fwd>=trail) + breadth 15 (40-60) + HA 25
        (
            dict(peg=1.3, forward_pe=20, trailing_pe=20, breadth=50, ha_green=25),
            55,
        ),
        # PEG 0 (>2) + accel 25 + breadth 0 (<40) + HA 0 (<20)
        (
            dict(peg=2.5, forward_pe=10, trailing_pe=20, breadth=10, ha_green=5),
            25,
        ),
    ],
)
def test_confidence_score_early_rotation(kwargs, expected):
    assert (
        screener.confidence_score(screener.EARLY_ROTATION, **kwargs) == expected
    )


def test_confidence_score_pullback_uses_rs_match():
    # Strategy match keys off RS for pullback, not HA.
    score = screener.confidence_score(
        screener.TREND_PULLBACK,
        peg=0.5,
        forward_pe=10,
        trailing_pe=20,
        breadth=70,
        rs=6.0,
        ha_green=0,
    )
    assert score == 100


def test_screen_early_rotation_filters_and_scores():
    result, _agg, quals = screener.screen(
        _dashboard(), _valuation(), screener.EARLY_ROTATION
    )
    assert quals == ["Tech"]
    # AAA passes (fwd 18<=25, peg 0.9<=1.5, Strong). BBB fails (fwd 30, peg 1.4
    # ok but fwd>25). So only AAA.
    assert list(result["Ticker"]) == ["AAA"]
    assert result.iloc[0]["Confidence"] == 100  # peg25+accel25+breadth25+ha25


def test_screen_excludes_missing_valuation():
    val = _valuation()
    val.loc[val["symbol"] == "AAA", "peg"] = None  # drop AAA's PEG
    result, _agg, _quals = screener.screen(
        _dashboard(), val, screener.EARLY_ROTATION
    )
    assert "AAA" not in list(result["Ticker"])  # excluded for missing PEG


def test_screen_empty_when_no_qualifying_sectors():
    result, _agg, quals = screener.screen(
        _dashboard(), _valuation(), screener.TREND_PULLBACK
    )
    assert quals == []
    assert result.empty
    assert list(result.columns) == screener.RESULT_COLUMNS
