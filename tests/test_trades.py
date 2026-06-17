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
    # Quantity sizing (whole shares).
    assert plan.loc["AAA", "Quantity"] == 15
    assert plan.loc["BBB", "Quantity"] == 0  # 200 > 150
    assert plan.loc["CCC", "Quantity"] == 3
    # Markets carried through for contract resolution.
    assert plan.loc["AAA", "Market"] == "BME"
    assert plan.loc["CCC", "Market"] == "LSE"
    # Estimated cost.
    assert plan.loc["AAA", "Est. Cost"] == pytest.approx(150.0)


def test_build_buy_plan_empty():
    plan = trades.build_buy_plan(pd.DataFrame(), None)
    assert plan.empty
    assert list(plan.columns) == [
        "Symbol",
        "Market",
        "Signal",
        "Source",
        "Price",
        "Quantity",
        "Est. Cost",
    ]


def test_build_buy_plan_accepts_symbol_column():
    """Frames may carry 'Symbol' instead of 'Ticker'."""
    df = pd.DataFrame({"Symbol": ["AAA"], "Price": [10.0]})
    plan = trades.build_buy_plan(df, None)
    assert plan.iloc[0]["Symbol"] == "AAA"
    assert plan.iloc[0]["Source"] == "current"
