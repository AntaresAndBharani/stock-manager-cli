import altair as alt
import pandas as pd
import plotly.express as px
import streamlit as st

from tradingtools_stock.core import trades, valuation
from tradingtools_stock.core.config_store import (
    get_sma_1000_touch_lookback,
    set_sma_1000_touch_lookback,
)
from tradingtools_stock.core.fetcher import (
    create_tables_if_not_exist,
    get_active_tickers,
    get_active_tickers_with_markets,
    get_db_connection,
)
from tradingtools_stock.core.ibkr import (
    fetch_executions,
    fetch_portfolio,
    get_ib_settings,
    is_api_port_open,
    place_market_buys,
)
from tradingtools_stock.core.strategies import (
    fetch_dashboard_cache,
    get_dashboard_data,
    invalidate_dashboard_cache,
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
        # Ensure the schema is current (e.g. the sma_1000 columns) before
        # reading the cache, in case the DB predates a schema migration.
        create_tables_if_not_exist(conn)
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


@st.cache_data(ttl=3600)
def load_active_markets():
    """Map active symbol -> market code, for resolving IBKR contracts."""
    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
        return {
            row["symbol"]: row["market"]
            for row in get_active_tickers_with_markets(conn)
        }
    finally:
        conn.close()


@st.cache_data(ttl=60)
def load_trades(start=None, end=None):
    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
        return trades.fetch_trades(conn, start, end)
    finally:
        conn.close()


def place_buys_and_record(orders):
    """Place market buys via IBKR and persist each as a recorded CLI trade."""
    results = place_market_buys(orders)
    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
        by_symbol = {o["symbol"]: o for o in orders}
        for res in results:
            if res.get("error"):
                continue
            spec = by_symbol.get(res["symbol"], {})
            trades.record_trade(
                conn,
                res["symbol"],
                "BUY",
                res.get("quantity") or None,
                price=spec.get("price"),
                currency=res.get("currency"),
                ib_order_id=res.get("order_id"),
                ib_perm_id=res.get("perm_id"),
                status=res.get("status"),
            )
    finally:
        conn.close()
    return results


@st.cache_data(ttl=60)
def load_bought_this_month():
    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
        return trades.symbols_bought_this_month(conn)
    finally:
        conn.close()


def reconcile_executions_now():
    """Pull IBKR executions and import new Manual fills into the trades table."""
    executions = fetch_executions()
    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
        return trades.reconcile_executions(conn, executions)
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_active_symbols():
    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
        return sorted(get_active_tickers(conn))
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_latest_valuation():
    conn = get_db_connection()
    try:
        create_tables_if_not_exist(conn)
        return valuation.fetch_latest_valuation(conn)
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_valuation_history(symbol):
    conn = get_db_connection()
    try:
        return valuation.fetch_valuation_history(conn, symbol)
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def load_sector_valuation_history(sector):
    conn = get_db_connection()
    try:
        return valuation.fetch_sector_valuation_history(conn, sector)
    finally:
        conn.close()


tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(
    [
        "Dashboard",
        "Backtesting",
        "Sector & Industry Analysis",
        "Valuation",
        "IBKR Portfolio",
        "Admin",
    ]
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

                    if "1000 SMA Touch Days" in row.index:
                        idx = row.index.get_loc("1000 SMA Touch Days")
                        val = row["1000 SMA Touch Days"]
                        if pd.notna(val):
                            styles[idx] = "color: #00ff00; font-weight: bold;"  # Green
                        else:
                            styles[idx] = "color: #ff5555; font-weight: bold;"  # Red

                    return styles

                df_entries = df[df["Signal"] != "⚪ None"]
                df_no_entries = df[df["Signal"] == "⚪ None"]

                mask_1000_sma = df_no_entries["1000 SMA Touch Days"].notna()
                df_no_entries_1k = df_no_entries[mask_1000_sma]
                df_no_entries_other = df_no_entries[~mask_1000_sma]

                col_config = {
                    "Price": st.column_config.NumberColumn(format="$%.2f"),
                    "21 EMA": st.column_config.NumberColumn(format="$%.2f"),
                    "50 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "100 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "200 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "1000 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "1000 SMA Touch Days": st.column_config.NumberColumn(
                        format="%d d ago"
                    ),
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
                df_asof_entries = None
                show_asof = st.toggle("Compare with an earlier date", value=True)
                if show_asof:
                    today = pd.Timestamp.today().normalize()
                    default_asof = (today.replace(day=1) - pd.Timedelta(days=1)).date()
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
                            df_asof = df_asof[df_asof["Sector"].isin(selected_sectors)]
                        if selected_industries:
                            df_asof = df_asof[
                                df_asof["Industry"].isin(selected_industries)
                            ]

                        df_asof_entries = df_asof[df_asof["Signal"] != "⚪ None"]
                        st.subheader(f"Entries as of {as_of} ({len(df_asof_entries)})")
                        if not df_asof_entries.empty:
                            st.dataframe(
                                df_asof_entries.style.apply(highlight_mas, axis=1),
                                column_config=col_config,
                                width="stretch",
                                hide_index=True,
                            )
                        else:
                            st.info("No active entries as of that date.")

                # ---- Average buy ----
                st.divider()
                st.subheader("💶 Average buy")
                st.caption(
                    "Buy a fixed budget of each entry — both current entries "
                    "and those from the 'as of' date above. **Shares** is "
                    "pre-filled from the budget (whole shares, or a partial "
                    "share when one share costs more than the budget) and is "
                    "**editable** — set the exact quantity per row, then tick "
                    "which to buy. Stocks already bought this month are hidden. "
                    "Partial shares need fractional-share permission on the "
                    "account and an IBKR-eligible stock."
                )

                budget = st.number_input(
                    "Budget per stock (stock currency)",
                    min_value=1.0,
                    value=float(trades.DEFAULT_BUDGET),
                    step=10.0,
                    key="buy_budget",
                )
                markets = load_active_markets()
                bought = load_bought_this_month()
                plan = trades.build_buy_plan(
                    df_entries,
                    df_asof_entries,
                    markets,
                    budget=budget,
                    exclude_symbols=bought,
                )

                if bought:
                    st.caption(
                        "Hidden (already bought this month): "
                        + ", ".join(sorted(bought))
                    )

                if plan.empty:
                    st.info("No entries available to buy.")
                else:
                    editor_df = plan.drop(columns=["Est. Cost"]).copy()
                    editor_df.insert(0, "Buy", True)
                    edited = st.data_editor(
                        editor_df,
                        hide_index=True,
                        width="stretch",
                        disabled=[
                            c for c in editor_df.columns
                            if c not in ("Buy", "Shares")
                        ],
                        column_config={
                            "Buy": st.column_config.CheckboxColumn(
                                "Buy", default=True
                            ),
                            "Price": st.column_config.NumberColumn(format="%.2f"),
                            "Shares": st.column_config.NumberColumn(
                                "Shares",
                                min_value=0.0,
                                step=0.1,
                                format="%.4f",
                                help=(
                                    "Number of shares to buy — editable. "
                                    "Fractions allowed for partial positions."
                                ),
                            ),
                        },
                        key="buy_plan_editor",
                    )
                    selected = edited[edited["Buy"] & (edited["Shares"] > 0)].copy()
                    selected["Est. Cost"] = selected["Shares"] * selected["Price"]
                    total_cost = selected["Est. Cost"].fillna(0).sum()
                    st.caption(
                        f"Selected: **{len(selected)}** stocks · "
                        f"≈ **{total_cost:,.2f}** total "
                        "(each in its own currency)."
                    )

                    ib_host, ib_port, _ = get_ib_settings()
                    reachable = is_api_port_open(ib_host, ib_port)
                    live_hint = " (live trading port)" if ib_port == 4001 else ""
                    if not reachable:
                        st.warning(
                            f"IB Gateway / TWS is not reachable on "
                            f"{ib_host}:{ib_port}. Start it and log in, "
                            "then reload."
                        )
                    else:
                        st.caption(
                            f"Orders route to the account on "
                            f"{ib_host}:{ib_port}{live_hint}. IB Gateway's "
                            "'Read-Only API' must be disabled."
                        )

                    confirm = st.checkbox(
                        "I have reviewed the orders above and want to place "
                        "them as market BUY orders.",
                        key="buy_confirm",
                    )
                    if st.button(
                        "Place market buy orders",
                        type="primary",
                        disabled=not (reachable and confirm and not selected.empty),
                        key="buy_place",
                    ):
                        orders = [
                            {
                                "symbol": r["Symbol"],
                                "market": r["Market"],
                                "quantity": float(r["Shares"]),
                                "price": (
                                    float(r["Price"])
                                    if pd.notna(r["Price"])
                                    else None
                                ),
                            }
                            for _, r in selected.iterrows()
                        ]
                        with st.spinner("Placing orders via IBKR..."):
                            try:
                                results = place_buys_and_record(orders)
                                res_df = pd.DataFrame(results)
                                ok = res_df[res_df["error"].isna()]
                                failed = res_df[res_df["error"].notna()]
                                if not ok.empty:
                                    st.success(f"Placed {len(ok)} order(s).")
                                    st.dataframe(
                                        ok[
                                            [
                                                "symbol",
                                                "quantity",
                                                "currency",
                                                "status",
                                                "order_id",
                                            ]
                                        ],
                                        hide_index=True,
                                        width="stretch",
                                    )
                                if not failed.empty:
                                    st.error(f"{len(failed)} order(s) failed:")
                                    st.dataframe(
                                        failed[["symbol", "error"]],
                                        hide_index=True,
                                        width="stretch",
                                    )
                                load_trades.clear()
                                load_portfolio.clear()
                                load_bought_this_month.clear()
                            except Exception as e:
                                st.error(f"Order placement failed: {e}")

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

                # Treemap: tile size = stock count, color = median % vs 200 SMA.
                treemap_df = industry_summary.dropna(subset=["Median"])
                if treemap_df.empty:
                    st.info("No industries with a valid 200 SMA to chart.")
                else:
                    bound = float(treemap_df["Median"].abs().max()) or 1.0
                    fig = px.treemap(
                        treemap_df,
                        path=[px.Constant("All sectors"), "Sector", "Industry"],
                        values="Stocks",
                        color="Median",
                        color_continuous_scale=["#ff5555", "#888888", "#00ff00"],
                        color_continuous_midpoint=0,
                        range_color=[-bound, bound],
                        custom_data=["Median", "Entries"],
                    )
                    fig.update_traces(
                        hovertemplate=(
                            "<b>%{label}</b><br>"
                            "Stocks: %{value}<br>"
                            "Median vs 200 SMA: %{customdata[0]:+.1f}%<br>"
                            "Entries: %{customdata[1]}<extra></extra>"
                        )
                    )
                    fig.update_layout(
                        margin=dict(t=10, l=0, r=0, b=0),
                        coloraxis_colorbar_title="% vs 200",
                    )
                    st.plotly_chart(fig, use_container_width=True)

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

with tab4:  # noqa: SIM117
    st.header("Valuation")
    st.markdown(
        "Sector (or global) valuation index, a table of every stock's "
        "multiples, and a per-stock ~5-year history. Filter by sector below; "
        "with no filter the summary aggregates the whole universe. "
        "Data: `tradingtools-stock fetch valuation` (Yahoo Finance)."
    )

    with st.spinner("Loading valuation data..."):
        try:
            latest = load_latest_valuation()
            active_symbols = load_active_symbols()

            if not active_symbols:
                st.warning("No active tickers found.")
            elif latest.empty:
                st.warning(
                    "No valuation data yet. Populate it with "
                    "`tradingtools-stock fetch valuation`."
                )
            else:
                # ---- Sector filter (no selection -> global aggregation) ----
                sectors = sorted(latest["sector"].dropna().unique())
                sector_choice = (
                    st.selectbox(
                        "Sector filter",
                        options=["All sectors"] + sectors,
                        key="valuation_sector_filter",
                    )
                    or "All sectors"
                )
                if sector_choice == "All sectors":
                    scope = latest
                    scope_label = "All sectors (global)"
                else:
                    scope = latest[latest["sector"] == sector_choice]
                    scope_label = sector_choice

                # ---- Sector / global valuation index ----
                st.subheader(f"{scope_label} — valuation index")
                agg = valuation.aggregate_valuation(scope)
                st.caption(f"Median multiples across {agg['count']} valued stocks.")

                def render_index_tile(col, metric):
                    tile_label = valuation.VALUATION_METRICS[metric]
                    med = agg.get(metric)
                    if med is None:
                        col.metric(tile_label, "N/A")
                        return
                    is_yield = metric == "dividend_yield"
                    col.metric(
                        tile_label,
                        f"{med * 100:.2f}%" if is_yield else f"{med:.1f}",
                    )

                idx_metrics = list(valuation.VALUATION_METRICS)
                idx_top = st.columns(4)
                for i, metric in enumerate(idx_metrics[:4]):
                    render_index_tile(idx_top[i], metric)
                idx_bot = st.columns(4)
                for i, metric in enumerate(idx_metrics[4:]):
                    render_index_tile(idx_bot[i], metric)

                # ---- Stocks table ----
                # Format to strings ("" for missing) so empty cells render
                # blank: st.dataframe prints the literal "None" for a numeric
                # NaN/null regardless of column_config or Styler na_rep.
                st.subheader(f"Stocks ({len(scope)})")
                table = valuation.stocks_table(scope)

                def _fmt_col(series, decimals, scale=1.0):
                    return series.map(
                        lambda v: (
                            "" if pd.isna(v) else f"{float(v) * scale:.{decimals}f}"
                        )
                    )

                disp = pd.DataFrame(
                    {"Ticker": table["symbol"], "Sector": table["sector"]}
                )
                disp["Trailing P/E"] = _fmt_col(table["trailing_pe"], 1)
                disp["Forward P/E"] = _fmt_col(table["forward_pe"], 1)
                disp["P/B"] = _fmt_col(table["pb"], 1)
                disp["P/S"] = _fmt_col(table["ps"], 1)
                disp["PEG"] = _fmt_col(table["peg"], 2)
                disp["EV/EBITDA"] = _fmt_col(table["ev_ebitda"], 1)
                disp["EV/Revenue"] = _fmt_col(table["ev_revenue"], 1)
                disp["Div Yield %"] = _fmt_col(table["dividend_yield"], 2, 100.0)

                # Colour each metric against the index (the summary medians
                # above): below the index -> red, above -> green.
                index_cols = {
                    "Trailing P/E": "trailing_pe",
                    "Forward P/E": "forward_pe",
                    "P/B": "pb",
                    "P/S": "ps",
                    "PEG": "peg",
                    "EV/EBITDA": "ev_ebitda",
                    "EV/Revenue": "ev_revenue",
                    "Div Yield %": "dividend_yield",
                }

                def _cell_color(value, median):
                    if pd.isna(value) or median is None or median <= 0:
                        return ""
                    if value < median:
                        return "color: #ff5555; font-weight: bold;"
                    if value > median:
                        return "color: #00ff00; font-weight: bold;"
                    return ""

                def color_vs_index(frame):
                    styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
                    for disp_col, metric in index_cols.items():
                        med = agg.get(metric)
                        styles[disp_col] = table[metric].map(
                            lambda v, m=med: _cell_color(v, m)
                        )
                    return styles

                st.dataframe(
                    disp.style.apply(color_vs_index, axis=None),
                    hide_index=True,
                    width="stretch",
                )

                # ---- Per-stock detail ----
                st.subheader("Stock detail")
                detail_symbols = sorted(scope["symbol"].unique())
                selected = st.selectbox(
                    "Ticker", options=detail_symbols, key="valuation_ticker"
                )

                sel_rows = scope[scope["symbol"] == selected]
                if sel_rows.empty:
                    st.info("Select a ticker to see its history.")
                else:
                    row = sel_rows.iloc[0]
                    sector = row["sector"]
                    as_of = pd.to_datetime(row["as_of_date"]).date()
                    st.caption(f"Sector: **{sector}** · latest data as of {as_of}")

                    # Current sector median for each metric (drives the badges).
                    sector_meds = {
                        m: valuation.sector_median(latest, sector, m)
                        for m in valuation.VALUATION_METRICS
                    }

                    # ---- A. Current snapshot + B. vs-sector badges ----
                    def render_tile(col, metric):
                        tile_label = valuation.VALUATION_METRICS[metric]
                        raw = pd.to_numeric(row.get(metric), errors="coerce")
                        med = sector_meds.get(metric)
                        is_yield = metric == "dividend_yield"
                        if pd.isna(raw) or raw <= 0:
                            note = "N/A (loss)" if metric == "trailing_pe" else "N/A"
                            col.metric(tile_label, note)
                            return
                        display = f"{raw * 100:.2f}%" if is_yield else f"{raw:.1f}"
                        delta = None
                        help_txt = None
                        if med is not None and med > 0:
                            diff = (raw / med - 1) * 100
                            delta = f"{diff:+.0f}% vs sector"
                            help_txt = (
                                f"Sector median: {med * 100:.2f}%"
                                if is_yield
                                else f"Sector median: {med:.1f}"
                            )
                        # Lower multiple = cheaper -> green when below the sector.
                        color = (
                            "inverse"
                            if metric in valuation.LOWER_IS_CHEAPER
                            else "normal"
                        )
                        col.metric(
                            tile_label,
                            display,
                            delta=delta,
                            delta_color=color,
                            help=help_txt,
                        )

                    metrics_order = list(valuation.VALUATION_METRICS)
                    cols_top = st.columns(4)
                    for i, metric in enumerate(metrics_order[:4]):
                        render_tile(cols_top[i], metric)
                    cols_bot = st.columns(4)
                    for i, metric in enumerate(metrics_order[4:]):
                        render_tile(cols_bot[i], metric)

                    if valuation.sector_sample_size(latest, sector, "trailing_pe") < 3:
                        st.caption(
                            "⚠️ Fewer than 3 valued stocks in this sector — "
                            "read the sector comparison with caution."
                        )

                    # ---- C. Historical chart ----
                    st.subheader("History")
                    metric_key = st.selectbox(
                        "Metric",
                        options=list(valuation.CHARTABLE_METRICS),
                        format_func=lambda k: valuation.CHARTABLE_METRICS[k],
                        key="valuation_metric",
                    )
                    label = valuation.CHARTABLE_METRICS[metric_key]

                    hist = load_valuation_history(selected)
                    series = hist[["as_of_date", metric_key]].copy()
                    series[metric_key] = pd.to_numeric(
                        series[metric_key], errors="coerce"
                    )
                    series = series.dropna()
                    series = series[series[metric_key] > 0]

                    if series.empty:
                        st.info(f"No positive {label} history for {selected}.")
                    else:
                        stats = valuation.compute_stats(
                            series.set_index("as_of_date")[metric_key]
                        )
                        # series is non-empty and positive, so stats is present.
                        assert stats is not None
                        lo, hi = stats["min"], stats["max"]
                        band = pd.DataFrame(
                            {
                                "as_of_date": [
                                    series["as_of_date"].min(),
                                    series["as_of_date"].max(),
                                ],
                                "lo": [lo, lo],
                                "hi": [hi, hi],
                            }
                        )
                        band_layer = (
                            alt.Chart(band)
                            .mark_area(opacity=0.12, color="#888888")
                            .encode(x="as_of_date:T", y="lo:Q", y2="hi:Q")
                        )
                        line = (
                            alt.Chart(series)
                            .mark_line(color="#4c9be8")
                            .encode(
                                x=alt.X("as_of_date:T", title=None),
                                y=alt.Y(f"{metric_key}:Q", title=label),
                                tooltip=[
                                    alt.Tooltip("as_of_date:T", title="Quarter"),
                                    alt.Tooltip(
                                        f"{metric_key}:Q", title=label, format=".2f"
                                    ),
                                ],
                            )
                        )
                        mean_rule = (
                            alt.Chart(pd.DataFrame({"y": [stats["mean"]]}))
                            .mark_rule(color="#cccccc", strokeDash=[4, 4])
                            .encode(y="y:Q")
                        )
                        current_point = (
                            alt.Chart(series.iloc[[-1]])
                            .mark_point(color="#4c9be8", size=90, filled=True)
                            .encode(x="as_of_date:T", y=f"{metric_key}:Q")
                        )
                        layers = band_layer + line + mean_rule + current_point

                        sector_hist = load_sector_valuation_history(sector)
                        sector_series = valuation.compute_sector_median_series(
                            sector_hist, metric_key
                        )
                        if not sector_series.empty:
                            sector_df = sector_series.reset_index()
                            sector_df.columns = ["as_of_date", "sector_med"]
                            layers = layers + (
                                alt.Chart(sector_df)
                                .mark_line(color="#e8a14c", strokeDash=[2, 2])
                                .encode(
                                    x="as_of_date:T",
                                    y="sector_med:Q",
                                    tooltip=[
                                        alt.Tooltip("as_of_date:T", title="Quarter"),
                                        alt.Tooltip(
                                            "sector_med:Q",
                                            title=f"{sector} median",
                                            format=".2f",
                                        ),
                                    ],
                                )
                            )

                        st.altair_chart(layers, use_container_width=True)
                        legend = (
                            f"Blue = {selected} · grey band = min–max · "
                            "dashed grey = mean"
                        )
                        if not sector_series.empty:
                            legend += f" · orange = {sector} median"
                        st.caption(legend)

                        # ---- D. Summary stats ----
                        s1, s2, s3, s4, s5 = st.columns(5)
                        s1.metric("Current", f"{stats['current']:.1f}")
                        s2.metric("Mean", f"{stats['mean']:.1f}")
                        s3.metric("Min", f"{stats['min']:.1f}")
                        s4.metric("Max", f"{stats['max']:.1f}")
                        s5.metric("Percentile", f"{stats['percentile']:.0f}th")
                        st.caption(
                            f"Current {label} sits at the "
                            f"{stats['percentile']:.0f}th percentile of its "
                            f"{stats['count']}-quarter history "
                            "(0th = cheapest, 100th = most expensive)."
                        )
        except Exception as e:
            st.error(f"Error loading valuation data: {e}")

with tab5:
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

                # ---- Trades ----
                st.divider()
                st.subheader("Trades")
                st.caption(
                    "Recorded CLI buys plus reconciled IBKR executions. Trades "
                    "placed by this tool show as **CLI**; anything else is "
                    "**Manual**. Reconcile to import manual buys into the local "
                    "history (so they also count towards 'bought this month')."
                )

                rcol1, rcol2 = st.columns([1, 3])
                with rcol1:
                    if st.button("Reconcile IBKR executions", key="trades_reconcile"):
                        with st.spinner("Importing executions from IBKR..."):
                            try:
                                n = reconcile_executions_now()
                                load_trades.clear()
                                load_bought_this_month.clear()
                                st.success(
                                    f"Reconciled {n} new manual execution(s)."
                                    if n
                                    else "No new manual executions to import."
                                )
                            except Exception as rec_err:  # noqa: BLE001
                                st.error(f"Reconcile failed: {rec_err}")
                with rcol2:
                    include_live = st.toggle(
                        "Also show un-reconciled live executions",
                        value=False,
                        key="trades_include_live",
                        help=(
                            "Pulls fills straight from IBKR for the very recent "
                            "ones not yet reconciled into local history."
                        ),
                    )

                today = pd.Timestamp.today().normalize().date()
                default_from = (pd.Timestamp.today() - pd.Timedelta(days=30)).date()
                tcol1, tcol2 = st.columns(2)
                with tcol1:
                    t_from = st.date_input(
                        "From", value=default_from, max_value=today, key="trades_from"
                    )
                with tcol2:
                    t_to = st.date_input(
                        "To", value=today, max_value=today, key="trades_to"
                    )

                cols = [
                    "Time",
                    "Symbol",
                    "Action",
                    "Quantity",
                    "Price",
                    "Currency",
                    "Source",
                ]
                parts = []
                rec = load_trades(t_from, t_to)
                known_exec_ids = (
                    set(rec["Exec Id"].dropna()) if not rec.empty else set()
                )
                if not rec.empty:
                    parts.append(
                        rec.rename(columns={"Placed At": "Time"}).reindex(columns=cols)
                    )
                if include_live:
                    try:
                        ex = fetch_executions()
                        if not ex.empty:
                            ex = ex[
                                (ex["Time"].dt.date >= t_from)
                                & (ex["Time"].dt.date <= t_to)
                            ]
                            # Show only Manual fills not already in local history
                            # (CLI buys are recorded at placement time).
                            live = ex[
                                (ex["Source"] == "Manual")
                                & ~ex["Exec Id"].isin(known_exec_ids)
                            ]
                            parts.append(live.reindex(columns=cols))
                    except Exception as ex_err:  # noqa: BLE001
                        st.warning(f"Could not fetch live executions: {ex_err}")

                if parts:
                    all_trades = (
                        pd.concat(parts, ignore_index=True)
                        .sort_values("Time", ascending=False)
                        .reset_index(drop=True)
                    )
                    st.dataframe(
                        all_trades,
                        column_config={
                            "Time": st.column_config.DatetimeColumn(
                                format="YYYY-MM-DD HH:mm"
                            ),
                            "Price": st.column_config.NumberColumn(format="%.2f"),
                        },
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    st.info("No trades in the selected date range.")

            except Exception as e:
                st.error(f"Error fetching IBKR portfolio: {e}")
                st.info(
                    "Make sure you are logged into IB Gateway and the API is "
                    "enabled (Configure > Settings > API > Settings)."
                )

with tab6:
    st.subheader("Admin Settings")
    st.caption("These settings are stored in the database and persist across restarts.")

    try:
        conn = get_db_connection()
        try:
            current_lookback = get_sma_1000_touch_lookback(conn)
        finally:
            conn.close()

        new_lookback = st.number_input(
            "1000 SMA Touch Days — lookback window (trading days)",
            min_value=1,
            max_value=250,
            value=int(current_lookback),
            step=1,
            help=(
                "How many of the most recent trading days to scan for a price "
                "touch within ±5% of the 1000-day SMA. Used by the "
                "'1000 SMA Touch Days' column and the '1000 SMA Strategy' table."
            ),
        )

        if st.button("Save settings"):
            conn = get_db_connection()
            try:
                set_sma_1000_touch_lookback(conn, int(new_lookback))
                # Force a full recompute so the new lookback is reflected
                # immediately rather than only when fresh price data arrives.
                invalidate_dashboard_cache(conn)
            finally:
                conn.close()
            load_data.clear()
            st.success(
                f"Saved. 1000 SMA touch lookback set to {int(new_lookback)} "
                "days. The dashboard will recompute on the next load."
            )
    except Exception as e:
        st.error(f"Error loading admin settings: {e}")
