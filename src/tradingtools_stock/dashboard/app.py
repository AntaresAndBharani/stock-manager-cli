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
        "Summary of stocks per Sector/Industry and their normalized index (Average of Price / 200 SMA)."
    )

    with st.spinner("Calculating analysis..."):
        try:
            df = load_data()
            if df.empty:
                st.warning("No data available.")
            else:
                # Calculate custom index: Price / 200 SMA
                df["Index_Val"] = df.apply(
                    lambda row: (
                        row["Price"] / row["200 SMA"]
                        if pd.notna(row["200 SMA"]) and row["200 SMA"] > 0
                        else None
                    ),
                    axis=1,
                )

                # Sector summary
                st.subheader("By Sector")
                sector_summary = (
                    df.groupby("Sector")
                    .agg(Count=("Ticker", "count"), Index=("Index_Val", "mean"))
                    .reset_index()
                    .sort_values("Index", ascending=False)
                )

                st.dataframe(
                    sector_summary,
                    column_config={
                        "Count": st.column_config.NumberColumn("Number of Stocks"),
                        "Index": st.column_config.NumberColumn(
                            "Custom Index", format="%.4f"
                        ),
                    },
                    hide_index=True,
                    use_container_width=True,
                )

                # Bar chart for Sector Index
                if not sector_summary.empty:
                    st.bar_chart(sector_summary.set_index("Sector")["Index"])

                # Industry summary
                st.subheader("By Industry")
                industry_summary = (
                    df.groupby(["Sector", "Industry"])
                    .agg(Count=("Ticker", "count"), Index=("Index_Val", "mean"))
                    .reset_index()
                    .sort_values("Index", ascending=False)
                )

                st.dataframe(
                    industry_summary,
                    column_config={
                        "Count": st.column_config.NumberColumn("Number of Stocks"),
                        "Index": st.column_config.NumberColumn(
                            "Custom Index", format="%.4f"
                        ),
                    },
                    hide_index=True,
                    use_container_width=True,
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
