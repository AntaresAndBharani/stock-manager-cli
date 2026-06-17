import pandas as pd
import pytest

from tradingtools_stock.core import trades


@pytest.mark.parametrize(
    "price,budget,expected",
    [
        (10.0, 150.0, 15),
        (149.0, 150.0, 1),
        (151.0, 150.0, 0),  # one share already over budget
        (0.0, 150.0, 0),
        (-5.0, 150.0, 0),
        (None, 150.0, 0),
        (10.0, 0.0, 0),
        (33.0, 150.0, 4),  # floor(150/33) == 4
    ],
)
def test_compute_buy_quantity(price, budget, expected):
    assert trades.compute_buy_quantity(price, budget) == expected


def test_build_buy_plan_union_and_source():
    current = pd.DataFrame(
        {"Ticker": ["AAA", "BBB"], "Price": [10.0, 200.0], "Signal": ["🟢", "🟢"]}
    )
    asof = pd.DataFrame(
        {"Ticker": ["BBB", "CCC"], "Price": [200.0, 50.0], "Signal": ["🟡", "🟡"]}
    )
    markets = {"AAA": "BME", "BBB": None, "CCC": "LSE"}

    plan = trades.build_buy_plan(current, asof, markets, budget=150.0)
    plan = plan.set_index("Symbol")

    # Union of both sets.
    assert set(plan.index) == {"AAA", "BBB", "CCC"}
    # Source classification.
    assert plan.loc["AAA", "Source"] == "current"
    assert plan.loc["BBB", "Source"] == "both"
    assert plan.loc["CCC", "Source"] == "as-of"
    # Whole-share sizing.
    assert plan.loc["AAA", "Shares"] == 15
    assert plan.loc["BBB", "Shares"] == 0  # 200 > 150
    assert plan.loc["CCC", "Shares"] == 3
    # Cash option == budget for every row.
    assert plan.loc["AAA", "Cash"] == pytest.approx(150.0)
    assert plan.loc["BBB", "Cash"] == pytest.approx(150.0)
    # Default method: Cash when no whole share is affordable, else Shares.
    assert plan.loc["AAA", "Method"] == trades.METHOD_SHARES
    assert plan.loc["BBB", "Method"] == trades.METHOD_CASH
    # Markets carried through for contract resolution.
    assert plan.loc["AAA", "Market"] == "BME"
    assert plan.loc["CCC", "Market"] == "LSE"
    # Estimated cost of the shares method.
    assert plan.loc["AAA", "Est. Cost"] == pytest.approx(150.0)


def test_build_buy_plan_excludes_symbols():
    current = pd.DataFrame({"Ticker": ["AAA", "BBB"], "Price": [10.0, 20.0]})
    plan = trades.build_buy_plan(
        current, None, budget=150.0, exclude_symbols={"AAA"}
    )
    assert list(plan["Symbol"]) == ["BBB"]


def test_build_buy_plan_empty():
    plan = trades.build_buy_plan(pd.DataFrame(), None)
    assert plan.empty
    assert list(plan.columns) == trades.PLAN_COLUMNS


def test_build_buy_plan_accepts_symbol_column():
    """Frames may carry 'Symbol' instead of 'Ticker'."""
    df = pd.DataFrame({"Symbol": ["AAA"], "Price": [10.0]})
    plan = trades.build_buy_plan(df, None)
    assert plan.iloc[0]["Symbol"] == "AAA"
    assert plan.iloc[0]["Source"] == "current"
