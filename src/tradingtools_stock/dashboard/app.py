import altair as alt
import pandas as pd
import streamlit as st

from tradingtools_stock.core.fetcher import get_db_connection
from tradingtools_stock.core.ibkr import (
    fetch_portfolio,
    get_ib_settings,
    is_api_port_open,
)
from tradingtools_stock.core.strategies import (
    fetch_dashboard_cache,
    get_dashboard_data,
    refresh_dashboard_cache,
    run_backtest,
)

st.set_page_config(page_title="Trading Tools Dashboard", layout="wide")

st.title("Heikin-Ashi Trends Dashboard")
st.markdown(
    "Analyze the Heikin-Ashi color of the current and previous two periods for 1-Month and 3-Month (Calendar Quarter) views."
)


@st.cache_data(ttl=3600)
def load_data():
    conn = get_db_connection()
    try:
        refresh_dashboard_cache(conn)
        df = fetch_dashboard_cache(conn)
        return df
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_backtest_data(div_pct=2.0):
    conn = get_db_connection()
    try:
        return run_backtest(conn, div_pct)
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_data_asof(as_of_date):
    """Recompute the dashboard signals as of a historical date (uncached path)."""
    conn = get_db_connection()
    try:
        return get_dashboard_data(conn, as_of_date=as_of_date)
    finally:
        conn.close()


@st.cache_data(ttl=60)
def load_portfolio():
    return fetch_portfolio()


tab1, tab2, tab3, tab4 = st.tabs(
    ["Dashboard", "Backtesting", "Sector & Industry Analysis", "IBKR Portfolio"]
)

with tab1:  # noqa: SIM117
    with st.spinner("Loading Heikin-Ashi data..."):
        try:
            df = load_data()

            if df.empty:
                st.warning(
                    "No active tickers found or no data available. Please fetch stock data first."
                )
            else:
                # Sector/Industry lookup, kept before row filters are applied so
                # the as-of recompute (which has no sector data) can be enriched.
                df_meta = df[["Ticker", "Sector", "Industry"]].copy()

                # Refresh button
                if st.button("Refresh Data"):
                    load_data.clear()
                    st.rerun()

                # Filters
                col_filt1, col_filt2 = st.columns(2)
                with col_filt1:
                    sectors = sorted([s for s in df["Sector"].unique() if pd.notna(s)])
                    selected_sectors = st.multiselect(
                        "Filter by Sector", options=sectors
                    )
                with col_filt2:
                    if selected_sectors:
                        filtered_df = df[df["Sector"].isin(selected_sectors)]
                        industries = sorted(
                            [i for i in filtered_df["Industry"].unique() if pd.notna(i)]
                        )
                    else:
                        industries = sorted(
                            [i for i in df["Industry"].unique() if pd.notna(i)]
                        )
                    selected_industries = st.multiselect(
                        "Filter by Industry", options=industries
                    )

                # Apply filters
                if selected_sectors:
                    df = df[df["Sector"].isin(selected_sectors)]
                if selected_industries:
                    df = df[df["Industry"].isin(selected_industries)]

                # Color formatter for Daily MAs relative to 200 SMA
                def highlight_mas(row):
                    sma200 = row["200 SMA"]
                    styles = [""] * len(row)
                    cols_to_check = ["Price", "21 EMA", "50 SMA", "100 SMA"]

                    for col in cols_to_check:
                        if col in row.index:
                            idx = row.index.get_loc(col)
                            if pd.isna(row[col]) or pd.isna(sma200):
                                continue
                            if row[col] < sma200:
                                styles[idx] = (
                                    "color: #ff5555; font-weight: bold;"  # Red
                                )
                            else:
                                styles[idx] = (
                                    "color: #00ff00; font-weight: bold;"  # Green
                                )

                    if "1000 SMA Touch" in row.index:
                        idx = row.index.get_loc("1000 SMA Touch")
                        val = str(row["1000 SMA Touch"])
                        if "(" in val:
                            styles[idx] = "color: #00ff00; font-weight: bold;"  # Green
                        else:
                            styles[idx] = "color: #ff5555; font-weight: bold;"  # Red

                    return styles

                df_entries = df[df["Signal"] != "⚪ None"]
                df_no_entries = df[df["Signal"] == "⚪ None"]

                mask_1000_sma = (
                    df_no_entries["1000 SMA Touch"]
                    .astype(str)
                    .str.contains("(", regex=False)
                )
                df_no_entries_1k = df_no_entries[mask_1000_sma]
                df_no_entries_other = df_no_entries[~mask_1000_sma]

                col_config = {
                    "Price": st.column_config.NumberColumn(format="$%.2f"),
                    "21 EMA": st.column_config.NumberColumn(format="$%.2f"),
                    "50 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "100 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "200 SMA": st.column_config.NumberColumn(format="$%.2f"),
                }

                st.subheader(f"Entries ({len(df_entries)})")
                if not df_entries.empty:
                    st.dataframe(
                        df_entries.style.apply(highlight_mas, axis=1),
                        column_config=col_config,
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.info("No active entries found.")

                # Compare against the signals as of an earlier date (default: 1
                # month ago). Recomputed on demand from full price history.
                show_asof = st.toggle(
                    "Compare with an earlier date", value=False
                )
                if show_asof:
                    today = pd.Timestamp.today().normalize()
                    default_asof = (today - pd.DateOffset(months=1)).date()
                    as_of = st.date_input(
                        "Entries as of",
                        value=default_asof,
                        max_value=today.date(),
                    )
                    with st.spinner(f"Computing entries as of {as_of}..."):
                        df_asof = load_data_asof(as_of)

                    if df_asof.empty:
                        st.info("No data available as of that date.")
                    else:
                        # Enrich with Sector/Industry and align to the live table.
                        df_asof = df_asof.merge(df_meta, on="Ticker", how="left")
                        df_asof = df_asof.reindex(columns=df.columns)
                        if selected_sectors:
                            df_asof = df_asof[
                                df_asof["Sector"].isin(selected_sectors)
                            ]
                        if selected_industries:
                            df_asof = df_asof[
                                df_asof["Industry"].isin(selected_industries)
                            ]

                        df_asof_entries = df_asof[df_asof["Signal"] != "⚪ None"]
                        st.subheader(
                            f"Entries as of {as_of} ({len(df_asof_entries)})"
                        )
                        if not df_asof_entries.empty:
                            st.dataframe(
                                df_asof_entries.style.apply(highlight_mas, axis=1),
                                column_config=col_config,
                                width="stretch",
                                hide_index=True,
                            )
                        else:
                            st.info("No active entries as of that date.")

                st.subheader(
                    f"No Entries (1000 SMA Strategy) ({len(df_no_entries_1k)})"
                )
                if not df_no_entries_1k.empty:
                    st.dataframe(
                        df_no_entries_1k.style.apply(highlight_mas, axis=1),
                        column_config=col_config,
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.info(
                        "No stocks without entries fulfilling the 1000 SMA strategy."
                    )

                st.subheader(f"No Entries (Other) ({len(df_no_entries_other)})")
                if not df_no_entries_other.empty:
                    st.dataframe(
                        df_no_entries_other.style.apply(highlight_mas, axis=1),
                        column_config=col_config,
                        width="stretch",
                        hide_index=True,
                    )
                else:
                    st.info("No other stocks without entries.")

        except Exception as e:
            st.error(f"Error loading dashboard data: {e}")

with tab2:
    st.header("Backtesting: Heikin-Ashi + 1D MAs")
    st.markdown(
        "Simulating holding a $1 position whenever the 1D MAs cross above the 200 SMA while 1M HA is Green and previous 1M HA was Red."
    )

    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        div_slider = st.slider(
            "Dividend Yield (%)", min_value=0.0, max_value=15.0, value=2.0, step=0.1
        )

    if st.button("Refresh Backtest Data"):
        load_backtest_data.clear()
        st.rerun()

    with st.spinner("Running backtest for all active tickers..."):
        try:
            bt_results = load_backtest_data(div_slider)
            if bt_results and not bt_results["summary"].empty:
                st.subheader("Global Performance")
                total_trades = len(bt_results["trades"])

                holder_invested = bt_results["summary"]["Invested ($)"].sum()
                no_div_current = bt_results["summary"][
                    "No_Divs Current Value ($)"
                ].sum()
                no_div_global_return = (
                    (no_div_current - holder_invested) / holder_invested * 100
                    if holder_invested > 0
                    else 0
                )

                drip_current = bt_results["summary"]["DRIP Current Value ($)"].sum()
                drip_global_return = (
                    (drip_current - holder_invested) / holder_invested * 100
                    if holder_invested > 0
                    else 0
                )

                st.write(f"**Total Trades Evaluated:** {total_trades}")

                # Side by side tiles
                col1, col2 = st.columns(2)
                with col1:
                    st.info("📊 **Without Dividends** (Pure capital appreciation)")
                    st.metric("Total Invested", f"${holder_invested:.2f}")
                    st.metric("Current Value", f"${no_div_current:.2f}")
                    st.metric("Global Return", f"{no_div_global_return:.2f}%")
                    st.metric(
                        "Win Rate",
                        f"{bt_results['summary']['No_Divs_Win_Rate'].mean():.1f}%",
                    )

                with col2:
                    st.success(
                        f"💰 **With Dividends (DRIP)** ({div_slider}%/yr, quarterly compounding)"
                    )
                    st.metric("Total Invested", f"${holder_invested:.2f}")
                    st.metric("Current Value", f"${drip_current:.2f}")
                    st.metric("Global Return", f"{drip_global_return:.2f}%")
                    st.metric(
                        "Win Rate",
                        f"{bt_results['summary']['DRIP_Win_Rate'].mean():.1f}%",
                    )

                st.subheader("Summary by Ticker")

                st.dataframe(
                    bt_results["summary"],
                    column_config={
                        "Invested ($)": st.column_config.NumberColumn(format="$%.2f"),
                        "No_Divs Current Value ($)": st.column_config.NumberColumn(
                            format="$%.2f"
                        ),
                        "DRIP Current Value ($)": st.column_config.NumberColumn(
                            format="$%.2f"
                        ),
                        "No_Divs_Avg_Return_Pct": st.column_config.NumberColumn(
                            "No Divs Avg Return", format="%.2f%%"
                        ),
                        "DRIP_Avg_Return_Pct": st.column_config.NumberColumn(
                            "DRIP Avg Return", format="%.2f%%"
                        ),
                        "Avg_Days_Held": st.column_config.NumberColumn(
                            "Avg Days Held", format="%.1f"
                        ),
                        "No_Divs_Win_Rate": st.column_config.NumberColumn(
                            "No Divs Win %", format="%.1f%%"
                        ),
                        "DRIP_Win_Rate": st.column_config.NumberColumn(
                            "DRIP Win %", format="%.1f%%"
                        ),
                    },
                    hide_index=True,
                    use_container_width=True,
                )

                st.subheader("Trade History")
                st.dataframe(
                    bt_results["trades"],
                    column_config={
                        "Entry Price": st.column_config.NumberColumn(format="$%.2f"),
                        "No_Divs Return %": st.column_config.NumberColumn(
                            format="%.2f%%"
                        ),
                        "DRIP Return %": st.column_config.NumberColumn(format="%.2f%%"),
                    },
                    hide_index=True,
                    use_container_width=True,
                )
            else:
                st.info("No trades triggered based on historical data.")
        except Exception as e:
            st.error(f"Error running backtest: {e}")

with tab3:
    st.header("Sector & Industry Analysis")
    st.markdown(
        "Sector rotation view: relative strength vs the 200 SMA, breadth, "
        "Heikin-Ashi trend health and entry-signal concentration."
    )

    with st.spinner("Calculating analysis..."):
        try:
            df = load_data()
            if df.empty:
                st.warning("No data available.")
            else:
                analysis = df.copy()
                price = pd.to_numeric(analysis["Price"], errors="coerce")
                sma200 = pd.to_numeric(analysis["200 SMA"], errors="coerce")
                analysis["Pct vs 200"] = (price / sma200.where(sma200 > 0) - 1) * 100
                analysis["Above 200"] = analysis["Pct vs 200"] > 0
                analysis["HA Green"] = (
                    analysis["1M Trend"].astype(str).str.strip().str.endswith("🟩")
                )
                analysis["Has Entry"] = analysis["Signal"] != "⚪ None"

                valid_pct = analysis["Pct vs 200"].notna()
                breadth = (
                    analysis.loc[valid_pct, "Above 200"].mean() * 100
                    if valid_pct.any()
                    else 0.0
                )
                sector_median = (
                    analysis.groupby("Sector")["Pct vs 200"].median().dropna()
                )
                entries = analysis[analysis["Has Entry"]]

                kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
                kpi1.metric(
                    "Market Breadth",
                    f"{breadth:.0f}%",
                    help="Share of all stocks trading above their 200 SMA.",
                )
                kpi2.metric(
                    "Sectors in Uptrend",
                    f"{int((sector_median > 0).sum())} / {len(sector_median)}",
                    help="Sectors whose median stock is above its 200 SMA.",
                )
                if not sector_median.empty:
                    kpi3.metric(
                        "Leading Sector",
                        sector_median.idxmax(),
                        delta=f"{sector_median.max():+.1f}%",
                        help="Highest median % vs 200 SMA.",
                    )
                    kpi4.metric(
                        "Lagging Sector",
                        sector_median.idxmin(),
                        delta=f"{sector_median.min():+.1f}%",
                        help="Lowest median % vs 200 SMA.",
                    )
                if entries.empty:
                    kpi5.metric("Entry Signals", "0")
                else:
                    entries_by_sector = entries["Sector"].value_counts()
                    kpi5.metric(
                        "Entry Signals",
                        f"{len(entries)}",
                        delta=(
                            f"{entries_by_sector.iloc[0]} in "
                            f"{entries_by_sector.index[0]}"
                        ),
                        delta_color="off",
                        help="Active Strong/Weak entries and where they cluster.",
                    )

                sector_summary = (
                    analysis.groupby("Sector")
                    .agg(
                        Stocks=("Ticker", "count"),
                        Median=("Pct vs 200", "median"),
                        Breadth=("Above 200", "mean"),
                        HAGreen=("HA Green", "mean"),
                        Entries=("Has Entry", "sum"),
                    )
                    .reset_index()
                    .sort_values("Median", ascending=False)
                )
                sector_summary["Breadth"] = sector_summary["Breadth"] * 100
                sector_summary["HAGreen"] = sector_summary["HAGreen"] * 100

                st.subheader("Sector Relative Strength")
                chart_df = sector_summary.dropna(subset=["Median"])
                if chart_df.empty:
                    st.info("No sectors with a valid 200 SMA.")
                else:
                    bars = (
                        alt.Chart(chart_df)
                        .mark_bar()
                        .encode(
                            x=alt.X("Median:Q", title="Median % vs 200 SMA"),
                            y=alt.Y("Sector:N", sort="-x", title=None),
                            color=alt.condition(
                                alt.datum.Median >= 0,
                                alt.value("#00ff00"),
                                alt.value("#ff5555"),
                            ),
                            tooltip=[
                                alt.Tooltip("Sector:N"),
                                alt.Tooltip(
                                    "Median:Q",
                                    format="+.1f",
                                    title="Median % vs 200 SMA",
                                ),
                                alt.Tooltip("Stocks:Q"),
                            ],
                        )
                    )
                    zero_rule = (
                        alt.Chart(pd.DataFrame({"x": [0.0]}))
                        .mark_rule(color="#888888")
                        .encode(x="x:Q")
                    )
                    st.altair_chart(bars + zero_rule)

                st.subheader("Sector Health")
                sector_display = sector_summary.rename(
                    columns={
                        "Median": "Median vs 200 SMA",
                        "Breadth": "Breadth > 200 SMA",
                        "HAGreen": "1M HA Green",
                    }
                )
                low_sample = sector_display["Stocks"] < 3
                sector_display.loc[low_sample, "Sector"] = (
                    sector_display.loc[low_sample, "Sector"] + " ⚠️"
                )

                def highlight_pct_col(col_name):
                    def _highlight(row):
                        styles = [""] * len(row)
                        val = row[col_name]
                        if pd.notna(val):
                            idx = row.index.get_loc(col_name)
                            color = "#00ff00" if val >= 0 else "#ff5555"
                            styles[idx] = f"color: {color}; font-weight: bold;"
                        return styles

                    return _highlight

                st.dataframe(
                    sector_display.style.apply(
                        highlight_pct_col("Median vs 200 SMA"), axis=1
                    ),
                    column_config={
                        "Median vs 200 SMA": st.column_config.NumberColumn(
                            format="%+.1f%%"
                        ),
                        "Breadth > 200 SMA": st.column_config.ProgressColumn(
                            format="%.0f%%", min_value=0, max_value=100
                        ),
                        "1M HA Green": st.column_config.NumberColumn(format="%.0f%%"),
                    },
                    hide_index=True,
                    width="stretch",
                )
                if low_sample.any():
                    st.caption(
                        "⚠️ Fewer than 3 stocks in the sector — "
                        "read its numbers with caution."
                    )

                st.subheader("By Industry")
                industry_summary = (
                    analysis.groupby(["Sector", "Industry"])
                    .agg(
                        Stocks=("Ticker", "count"),
                        Median=("Pct vs 200", "median"),
                        Entries=("Has Entry", "sum"),
                    )
                    .reset_index()
                    .sort_values("Median", ascending=False)
                )
                st.dataframe(
                    industry_summary.style.apply(highlight_pct_col("Median"), axis=1),
                    column_config={
                        "Median": st.column_config.NumberColumn(
                            "Median vs 200 SMA", format="%+.1f%%"
                        ),
                    },
                    hide_index=True,
                    width="stretch",
                )

                st.subheader("Sector Drill-Down")
                sector_options = sorted(analysis["Sector"].dropna().unique())
                selected_sector = st.selectbox(
                    "Sector", options=sector_options, key="sector_drilldown"
                )
                constituents = analysis[analysis["Sector"] == selected_sector]

                detail = constituents[
                    [
                        "Ticker",
                        "Industry",
                        "Signal",
                        "Pct vs 200",
                        "1M Trend",
                        "3M Trend",
                        "Price",
                    ]
                ].sort_values("Pct vs 200", ascending=False)

                st.dataframe(
                    detail.style.apply(highlight_pct_col("Pct vs 200"), axis=1),
                    column_config={
                        "Pct vs 200": st.column_config.NumberColumn(
                            "% vs 200 SMA", format="%+.1f%%"
                        ),
                        "Price": st.column_config.NumberColumn(format="$%.2f"),
                    },
                    hide_index=True,
                    width="stretch",
                )

        except Exception as e:
            st.error(f"Error loading analysis data: {e}")

with tab4:
    st.header("IBKR Portfolio")

    ib_host, ib_port, _ = get_ib_settings()
    if not is_api_port_open(ib_host, ib_port):
        st.warning(
            f"IB Gateway / TWS is not reachable on {ib_host}:{ib_port}. "
            "Start it with `tradingtools-stock ibkr gateway` (or "
            "`tradingtools-stock dashboard start --gateway`), log in, "
            "then click Retry."
        )
        if st.button("Retry", key="ibkr_retry"):
            load_portfolio.clear()
            st.rerun()
    else:
        with st.spinner("Fetching portfolio from IBKR..."):
            try:
                pf = load_portfolio()

                if st.button("Refresh Portfolio", key="ibkr_refresh"):
                    load_portfolio.clear()
                    st.rerun()

                st.caption(f"Account: {pf['account']}")

                summary = pf["summary"]
                positions = pf["positions"]

                total_unrealized = (
                    positions["Unrealized P&L"].sum() if not positions.empty else 0.0
                )

                col1, col2, col3, col4 = st.columns(4)
                col1.metric(
                    "Net Liquidation", f"${summary.get('NetLiquidation', 0):,.2f}"
                )
                col2.metric("Cash", f"${summary.get('TotalCashValue', 0):,.2f}")
                col3.metric("Buying Power", f"${summary.get('BuyingPower', 0):,.2f}")
                col4.metric(
                    "Unrealized P&L",
                    f"${total_unrealized:,.2f}",
                    delta=f"{total_unrealized:,.2f}",
                )

                col5, col6, col7 = st.columns(3)
                col5.metric(
                    "Gross Position Value",
                    f"${summary.get('GrossPositionValue', 0):,.2f}",
                )
                col6.metric(
                    "Available Funds", f"${summary.get('AvailableFunds', 0):,.2f}"
                )
                col7.metric(
                    "Maint. Margin", f"${summary.get('MaintMarginReq', 0):,.2f}"
                )

                st.subheader(f"Open Positions ({len(positions)})")
                if positions.empty:
                    st.info("No open positions.")
                else:

                    def highlight_pnl(row):
                        styles = [""] * len(row)
                        for col in ["Unrealized P&L", "Unrealized %", "Realized P&L"]:
                            if col in row.index and pd.notna(row[col]):
                                idx = row.index.get_loc(col)
                                if row[col] < 0:
                                    styles[idx] = "color: #ff5555; font-weight: bold;"
                                elif row[col] > 0:
                                    styles[idx] = "color: #00ff00; font-weight: bold;"
                        return styles

                    st.dataframe(
                        positions.style.apply(highlight_pnl, axis=1),
                        column_config={
                            "Avg Cost": st.column_config.NumberColumn(format="$%.2f"),
                            "Price": st.column_config.NumberColumn(format="$%.2f"),
                            "Market Value": st.column_config.NumberColumn(
                                format="$%.2f"
                            ),
                            "Unrealized P&L": st.column_config.NumberColumn(
                                format="$%.2f"
                            ),
                            "Unrealized %": st.column_config.NumberColumn(
                                format="%.2f%%"
                            ),
                            "Realized P&L": st.column_config.NumberColumn(
                                format="$%.2f"
                            ),
                            "Weight %": st.column_config.NumberColumn(format="%.2f%%"),
                        },
                        hide_index=True,
                        width="stretch",
                    )

                    if "Weight %" in positions.columns:
                        st.subheader("Allocation (% of Net Liquidation)")
                        st.bar_chart(positions.set_index("Symbol")["Weight %"])

            except Exception as e:
                st.error(f"Error fetching IBKR portfolio: {e}")
                st.info(
                    "Make sure you are logged into IB Gateway and the API is "
                    "enabled (Configure > Settings > API > Settings)."
                )
