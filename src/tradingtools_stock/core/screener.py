"""
Strategy screener: combine sector-level technical health with stock-level
fundamentals to surface trade entries, each scored 1-100.

Two strategies (see issue #44 / PRD):

- **Early Rotation** — accelerating short-term momentum. Sector breadth and a
  rising 1M Heikin-Ashi green share, with cheap/growthy valuations.
- **Trend Pullback** — established leaders in temporary weakness. Strong sector
  relative strength and breadth but a cooling HA trend, bought only on a Strong
  signal.

Sector aggregates come from the dashboard cache:
- **RS** (relative strength) = sector median "% vs 200 SMA".
- **Breadth** = % of the sector trading above its 200 SMA.
- **HA green** = % of the sector with a green 1-month Heikin-Ashi candle.

Stock fundamentals (forward/trailing P/E, PEG) come from the latest valuation
rows. Filter thresholds are configurable (DB ``app_config``); the
confidence-score matrix below is a fixed formula.
"""

import pandas as pd

EARLY_ROTATION = "early_rotation"
TREND_PULLBACK = "trend_pullback"
STRATEGY_LABELS = {
    EARLY_ROTATION: "Early Rotation",
    TREND_PULLBACK: "Trend Pullback",
}

# Configurable filter thresholds (defaults; overridable via app_config).
DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    EARLY_ROTATION: {
        "breadth_min": 40.0,
        "ha_green_min": 20.0,
        "forward_pe_max": 25.0,
        "peg_max": 1.5,
    },
    TREND_PULLBACK: {
        "rs_min": 5.0,
        "breadth_min": 60.0,
        "ha_green_max": 15.0,
        "peg_max": 2.0,
    },
}

# Columns of the screen() result table.
RESULT_COLUMNS = [
    "Ticker",
    "Sector",
    "Signal",
    "RS",
    "Breadth",
    "HA Green",
    "Forward P/E",
    "Trailing P/E",
    "PEG",
    "Confidence",
]


def _is_strong(signal) -> bool:
    return isinstance(signal, str) and "Strong Entry" in signal


def _is_weak(signal) -> bool:
    return isinstance(signal, str) and "Weak Entry" in signal


def _positive(value):
    """Return the value as float when present and strictly positive, else None.

    Mirrors the Valuation module's convention: a non-positive P/E / PEG signals
    a loss or bad data and is treated as missing.
    """
    num = pd.to_numeric(value, errors="coerce")
    if pd.isna(num) or num <= 0:
        return None
    return float(num)


def sector_aggregates(dashboard_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-sector technical aggregates from the dashboard cache.

    Returns columns: Sector, rs (median % vs 200 SMA), breadth (%), ha_green
    (%), stocks (count).
    """
    cols = ["Sector", "rs", "breadth", "ha_green", "stocks"]
    if dashboard_df is None or dashboard_df.empty:
        return pd.DataFrame(columns=cols)

    a = dashboard_df.copy()
    price = pd.to_numeric(a["Price"], errors="coerce")
    sma200 = pd.to_numeric(a["200 SMA"], errors="coerce")
    a["pct_vs_200"] = (price / sma200.where(sma200 > 0) - 1) * 100
    a["above_200"] = a["pct_vs_200"] > 0
    a["ha_green"] = a["1M Trend"].astype(str).str.strip().str.endswith("🟩")

    g = (
        a.groupby("Sector")
        .agg(
            rs=("pct_vs_200", "median"),
            breadth=("above_200", "mean"),
            ha_green=("ha_green", "mean"),
            stocks=("Ticker", "count"),
        )
        .reset_index()
    )
    g["breadth"] = g["breadth"] * 100
    g["ha_green"] = g["ha_green"] * 100
    return g


def qualifying_sectors(
    sector_df: pd.DataFrame, strategy: str, thresholds: dict
) -> list[str]:
    """Sector names passing the strategy's sector-level (macro) filters."""
    if sector_df is None or sector_df.empty:
        return []
    t = thresholds[strategy]
    if strategy == EARLY_ROTATION:
        mask = (sector_df["breadth"] >= t["breadth_min"]) & (
            sector_df["ha_green"] >= t["ha_green_min"]
        )
    else:
        mask = (
            (sector_df["rs"] >= t["rs_min"])
            & (sector_df["breadth"] >= t["breadth_min"])
            & (sector_df["ha_green"] <= t["ha_green_max"])
        )
    return sorted(sector_df.loc[mask, "Sector"].tolist())


def confidence_score(
    strategy: str,
    *,
    peg=None,
    forward_pe=None,
    trailing_pe=None,
    breadth=None,
    rs=None,
    ha_green=None,
) -> int:
    """
    Weighted 1-100 confidence: Fundamental (50) + Macro (50).

    - PEG: <=1.0 -> 25, <=1.5 -> 15, <=2.0 -> 5, else 0.
    - Earnings acceleration: Forward P/E < Trailing P/E -> 25, else 0.
    - Sector breadth: >=60% -> 25, >=40% -> 15, else 0.
    - Strategy match: Pullback RS >= +5.0% -> 25; Early Rotation HA green
      >= 20% -> 25.
    """
    points = 0

    peg_v = pd.to_numeric(peg, errors="coerce")
    if pd.notna(peg_v):
        if peg_v <= 1.0:
            points += 25
        elif peg_v <= 1.5:
            points += 15
        elif peg_v <= 2.0:
            points += 5

    fpe = pd.to_numeric(forward_pe, errors="coerce")
    tpe = pd.to_numeric(trailing_pe, errors="coerce")
    if pd.notna(fpe) and pd.notna(tpe) and fpe < tpe:
        points += 25

    bdth = pd.to_numeric(breadth, errors="coerce")
    if pd.notna(bdth):
        if bdth >= 60:
            points += 25
        elif bdth >= 40:
            points += 15

    if strategy == TREND_PULLBACK:
        rs_v = pd.to_numeric(rs, errors="coerce")
        if pd.notna(rs_v) and rs_v >= 5.0:
            points += 25
    else:
        ha_v = pd.to_numeric(ha_green, errors="coerce")
        if pd.notna(ha_v) and ha_v >= 20.0:
            points += 25

    return int(points)


def screen(
    dashboard_df: pd.DataFrame,
    valuation_df: pd.DataFrame,
    strategy: str,
    thresholds: dict | None = None,
):
    """
    Run a strategy screen.

    Returns ``(result, sectors, qualifying)``:
    - ``result``: ticker table (:data:`RESULT_COLUMNS`), sorted by Confidence.
    - ``sectors``: the per-sector aggregates frame.
    - ``qualifying``: list of sectors passing the macro filters.

    Stocks missing (or with non-positive) ``forward_pe`` / ``peg`` are excluded.
    """
    thresholds = thresholds or DEFAULT_THRESHOLDS
    sectors = sector_aggregates(dashboard_df)
    qualifying = qualifying_sectors(sectors, strategy, thresholds)
    empty = pd.DataFrame(columns=RESULT_COLUMNS)
    if not qualifying or dashboard_df is None or dashboard_df.empty:
        return empty, sectors, qualifying

    base = dashboard_df[dashboard_df["Sector"].isin(qualifying)][
        ["Ticker", "Sector", "Signal"]
    ].copy()
    base = base.merge(
        sectors[["Sector", "rs", "breadth", "ha_green"]], on="Sector", how="left"
    )

    val = valuation_df.rename(columns={"symbol": "Ticker"})
    val = val[["Ticker", "forward_pe", "trailing_pe", "peg"]]
    base = base.merge(val, on="Ticker", how="left")

    # Treat missing or non-positive valuation as absent (loss / bad data).
    for col in ("forward_pe", "trailing_pe", "peg"):
        base[col] = base[col].map(_positive)

    # Required fundamentals: drop stocks missing forward_pe or peg.
    base = base.dropna(subset=["forward_pe", "peg"])

    t = thresholds[strategy]
    if strategy == EARLY_ROTATION:
        signal_ok = base["Signal"].map(lambda s: _is_strong(s) or _is_weak(s))
        mask = (
            (base["forward_pe"] <= t["forward_pe_max"])
            & (base["peg"] <= t["peg_max"])
            & signal_ok
        )
    else:
        signal_ok = base["Signal"].map(_is_strong)
        mask = (
            base["trailing_pe"].notna()
            & (base["forward_pe"] < base["trailing_pe"])
            & (base["peg"] <= t["peg_max"])
            & signal_ok
        )
    base = base[mask].copy()

    base["Confidence"] = [
        confidence_score(
            strategy,
            peg=row.peg,
            forward_pe=row.forward_pe,
            trailing_pe=row.trailing_pe,
            breadth=row.breadth,
            rs=row.rs,
            ha_green=row.ha_green,
        )
        for row in base.itertuples()
    ]

    out = base.rename(
        columns={
            "rs": "RS",
            "breadth": "Breadth",
            "ha_green": "HA Green",
            "forward_pe": "Forward P/E",
            "trailing_pe": "Trailing P/E",
            "peg": "PEG",
        }
    )[RESULT_COLUMNS]
    return (
        out.sort_values("Confidence", ascending=False).reset_index(drop=True),
        sectors,
        qualifying,
    )
