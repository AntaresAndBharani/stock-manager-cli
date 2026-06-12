import warnings

import numpy as np
import pandas as pd


def calculate_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate Heikin-Ashi candles for a dataframe with Open, High, Low, Close columns.
    """
    ha_df = df.copy()

    # HA Close = (Open + High + Low + Close) / 4
    ha_df["HA_Close"] = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4

    # Initialize HA Open with the first real Open and Close
    ha_open = np.zeros(len(df))
    ha_open[0] = (df["Open"].iloc[0] + df["Close"].iloc[0]) / 2

    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i - 1] + ha_df["HA_Close"].iloc[i - 1]) / 2

    ha_df["HA_Open"] = ha_open

    # HA High = max(High, HA Open, HA Close)
    ha_df["HA_High"] = ha_df[["High", "HA_Open", "HA_Close"]].max(axis=1)

    # HA Low = min(Low, HA Open, HA Close)
    ha_df["HA_Low"] = ha_df[["Low", "HA_Open", "HA_Close"]].min(axis=1)

    # Color
    ha_df["HA_Color"] = np.where(ha_df["HA_Close"] >= ha_df["HA_Open"], "Green", "Red")

    return ha_df


def fetch_daily_data(conn, symbol: str) -> pd.DataFrame:
    """Fetch raw daily data from the database."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        query = "SELECT date, open, high, low, close FROM stock_prices WHERE symbol = %s ORDER BY date"
        df = pd.read_sql_query(query, conn, params=(symbol,), parse_dates=["date"])
    if not df.empty:
        df.set_index("date", inplace=True)
    return df


def resample_and_calculate_ha(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample daily dataframe and calculate Heikin-Ashi."""
    if df.empty:
        return df

    try:
        resampled = (
            df.resample(timeframe)
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )
    except ValueError:
        # Fallback to older pandas alias
        alt_tf = timeframe.replace("E", "")
        resampled = (
            df.resample(alt_tf)
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )

    resampled.rename(
        columns={"open": "Open", "high": "High", "low": "Low", "close": "Close"},
        inplace=True,
    )
    return calculate_heikin_ashi(resampled)


def get_dashboard_data(conn, tickers_to_process=None, as_of_date=None) -> pd.DataFrame:
    """
    Get the dashboard data for given tickers, or all active if None.

    If ``as_of_date`` is provided, daily data is truncated to that date
    (inclusive) before any indicators are calculated, so the returned signals
    reflect what the dashboard would have shown as of that historical date.
    """
    # Get active tickers
    if tickers_to_process is None:
        from tradingtools_stock.core.fetcher import get_active_tickers

        tickers = get_active_tickers(conn)
    else:
        tickers = tickers_to_process

    as_of_ts = pd.Timestamp(as_of_date) if as_of_date is not None else None

    results = []
    for ticker in tickers:
        try:
            df_daily = fetch_daily_data(conn, ticker)
            if as_of_ts is not None and not df_daily.empty:
                df_daily = df_daily[df_daily.index <= as_of_ts]
            if df_daily.empty:
                continue

            # Daily Indicators
            price = df_daily["close"].iloc[-1]
            ema21 = df_daily["close"].ewm(span=21, adjust=False).mean().iloc[-1]
            sma50 = df_daily["close"].rolling(window=50).mean().iloc[-1]
            sma100 = df_daily["close"].rolling(window=100).mean().iloc[-1]
            sma200 = df_daily["close"].rolling(window=200).mean().iloc[-1]

            df_daily["sma1000"] = df_daily["close"].rolling(window=1000).mean()
            sma1000 = df_daily["sma1000"].iloc[-1] if len(df_daily) >= 1000 else np.nan

            touched_1k = False
            days_ago = -1
            if len(df_daily) >= 10:
                last_10 = df_daily.iloc[-10:]
                for i in range(10):
                    idx = 9 - i
                    row = last_10.iloc[idx]
                    current_sma1000 = row["sma1000"]
                    if pd.notna(current_sma1000) and (
                        row["low"] <= current_sma1000 * 1.05
                        and row["high"] >= current_sma1000 * 0.95
                    ):
                        touched_1k = True
                        days_ago = i
                        break

            if touched_1k:
                sma1k_str = f"${sma1000:.2f} ({days_ago}d ago)"
            else:
                sma1k_str = f"${sma1000:.2f}" if pd.notna(sma1000) else "N/A"

            df_1m = resample_and_calculate_ha(df_daily, "ME")
            df_3m = resample_and_calculate_ha(df_daily, "QE")

            def get_last_3_colors(df):
                colors = df["HA_Color"].tolist()
                return (
                    colors[-3:]
                    if len(colors) >= 3
                    else [None] * (3 - len(colors)) + colors
                )

            colors_1m = (
                get_last_3_colors(df_1m) if not df_1m.empty else [None, None, None]
            )
            colors_3m = (
                get_last_3_colors(df_3m) if not df_3m.empty else [None, None, None]
            )

            def format_trend(colors):
                emojis = []
                for c in colors:
                    if c == "Green":
                        emojis.append("🟩")
                    elif c == "Red":
                        emojis.append("🟥")
                    else:
                        emojis.append("⬜")
                return " ".join(emojis)

            # Signal logic
            # Entry Trigger: 1M T-1 is Red AND 1M Current is Green AND Daily Momentum Filter
            daily_momentum_ok = (
                (price > sma200)
                and (ema21 > sma200)
                and (sma50 > sma200)
                and (sma100 > sma200)
            )

            signal = "⚪ None"
            if colors_1m[1] == "Red" and colors_1m[2] == "Green" and daily_momentum_ok:
                signal = "🟡 Weak Entry" if colors_3m[1] == "Red" else "🟢 Strong Entry"

            calc_date = df_daily.index[-1].date()

            results.append(
                {
                    "Ticker": ticker,
                    "Signal": signal,
                    "1M Trend": format_trend(colors_1m),
                    "3M Trend": format_trend(colors_3m),
                    "Price": price,
                    "21 EMA": ema21,
                    "50 SMA": sma50,
                    "100 SMA": sma100,
                    "200 SMA": sma200,
                    "1000 SMA Touch": sma1k_str,
                    "calc_date": calc_date,
                }
            )
        except Exception as e:
            print(f"Error processing {ticker}: {e}")

    df_results = pd.DataFrame(results)
    if not df_results.empty:
        # Sort so Strong Entry > Weak Entry > None
        signal_order = {"🟢 Strong Entry": 0, "🟡 Weak Entry": 1, "⚪ None": 2}
        df_results["_sort"] = df_results["Signal"].map(signal_order)
        df_results = (
            df_results.sort_values(["_sort", "Ticker"])
            .drop(columns=["_sort"])
            .reset_index(drop=True)
        )

    return df_results


def refresh_dashboard_cache(conn):
    """
    Check the dashboard_cache against stock_prices max dates.
    Recalculate for stale tickers and upsert into dashboard_cache.
    """
    from psycopg2.extras import execute_values

    from tradingtools_stock.core.fetcher import get_active_tickers

    active_tickers = get_active_tickers(conn)
    if not active_tickers:
        return

    stale_tickers = []
    with conn.cursor() as cur:
        # Get max date for each active ticker from stock_prices
        cur.execute(
            """
            SELECT symbol, MAX(date) 
            FROM stock_prices 
            WHERE symbol = ANY(%s) 
            GROUP BY symbol
            """,
            (active_tickers,),
        )
        max_dates = {row[0]: row[1] for row in cur.fetchall()}

        # Get current calculation dates from cache
        cur.execute(
            """
            SELECT symbol, calculation_date 
            FROM dashboard_cache 
            WHERE symbol = ANY(%s)
            """,
            (active_tickers,),
        )
        cache_dates = {row[0]: row[1] for row in cur.fetchall()}

    for ticker in active_tickers:
        max_dt = max_dates.get(ticker)
        if not max_dt:
            continue  # No price data for this ticker

        cache_dt = cache_dates.get(ticker)
        # If cache is missing or older than the latest price data, it's stale
        if not cache_dt or cache_dt < max_dt:
            stale_tickers.append(ticker)

    if stale_tickers:
        print(f"Refreshing dashboard cache for {len(stale_tickers)} tickers...")
        df_new = get_dashboard_data(conn, stale_tickers)

        if not df_new.empty:
            with conn.cursor() as cur:
                records = []
                for _, row in df_new.iterrows():
                    record = (
                        row["Ticker"],
                        row.get("calc_date"),
                        row["Signal"],
                        row["1M Trend"],
                        row["3M Trend"],
                        float(row["Price"]) if pd.notna(row["Price"]) else None,
                        float(row["21 EMA"]) if pd.notna(row["21 EMA"]) else None,
                        float(row["50 SMA"]) if pd.notna(row["50 SMA"]) else None,
                        float(row["100 SMA"]) if pd.notna(row["100 SMA"]) else None,
                        float(row["200 SMA"]) if pd.notna(row["200 SMA"]) else None,
                        row["1000 SMA Touch"],
                    )
                    records.append(record)

                insert_sql = """
                    INSERT INTO dashboard_cache 
                        (symbol, calculation_date, signal, trend_1m, trend_3m, price, 
                         ema_21, sma_50, sma_100, sma_200, sma_1000_touch)
                    VALUES %s
                    ON CONFLICT (symbol) 
                    DO UPDATE SET
                        calculation_date = EXCLUDED.calculation_date,
                        signal = EXCLUDED.signal,
                        trend_1m = EXCLUDED.trend_1m,
                        trend_3m = EXCLUDED.trend_3m,
                        price = EXCLUDED.price,
                        ema_21 = EXCLUDED.ema_21,
                        sma_50 = EXCLUDED.sma_50,
                        sma_100 = EXCLUDED.sma_100,
                        sma_200 = EXCLUDED.sma_200,
                        sma_1000_touch = EXCLUDED.sma_1000_touch
                """
                execute_values(cur, insert_sql, records)
                conn.commit()


def fetch_dashboard_cache(conn) -> pd.DataFrame:
    """
    Fetch the dashboard data directly from the cache table.
    """
    from tradingtools_stock.core.fetcher import get_active_tickers

    active_tickers = get_active_tickers(conn)
    if not active_tickers:
        return pd.DataFrame()

    query = """
        SELECT 
            c.symbol as "Ticker",
            COALESCE(t.sector, 'Unknown') as "Sector",
            COALESCE(t.industry, 'Unknown') as "Industry",
            c.signal as "Signal",
            c.trend_1m as "1M Trend",
            c.trend_3m as "3M Trend",
            c.price as "Price",
            c.ema_21 as "21 EMA",
            c.sma_50 as "50 SMA",
            c.sma_100 as "100 SMA",
            c.sma_200 as "200 SMA",
            c.sma_1000_touch as "1000 SMA Touch"
        FROM dashboard_cache c
        JOIN tickers t ON c.symbol = t.symbol
        WHERE c.symbol = ANY(%s)
    """

    df = pd.read_sql_query(query, conn, params=(active_tickers,))

    if not df.empty:
        # Sort so Strong Entry > Weak Entry > None
        signal_order = {"🟢 Strong Entry": 0, "🟡 Weak Entry": 1, "⚪ None": 2}
        df["_sort"] = df["Signal"].map(signal_order)
        df = (
            df.sort_values(["_sort", "Ticker"])
            .drop(columns=["_sort"])
            .reset_index(drop=True)
        )

    return df


def calculate_forming_ha_colors(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Calculate historical forming Heikin-Ashi colors for each day."""
    period_str = "M" if timeframe == "ME" else "Q"

    # Static closed HA candles
    df_tf = resample_and_calculate_ha(df, timeframe)
    if df_tf.empty:
        return pd.DataFrame(
            index=df.index, columns=["Prev_HA_Color", "Forming_HA_Color"]
        )

    df_tf["Period"] = df_tf.index.to_period(period_str)

    df_eval = df.copy()
    df_eval["Period"] = df_eval.index.to_period(period_str)

    # Shift to get previous period's HA values
    df_tf_prev = df_tf[["Period", "HA_Color", "HA_Open", "HA_Close"]].copy()
    df_tf_prev["Prev_HA_Color"] = df_tf_prev["HA_Color"].shift(1)
    df_tf_prev["Prev_HA_Open"] = df_tf_prev["HA_Open"].shift(1)
    df_tf_prev["Prev_HA_Close"] = df_tf_prev["HA_Close"].shift(1)
    df_tf_prev["Current_HA_Open"] = (
        df_tf_prev["Prev_HA_Open"] + df_tf_prev["Prev_HA_Close"]
    ) / 2

    df_tf_prev = df_tf_prev.drop_duplicates(subset=["Period"])

    # Merge back to daily
    index_name = df_eval.index.name or "date"
    df_eval = (
        df_eval.reset_index()
        .merge(
            df_tf_prev[["Period", "Prev_HA_Color", "Current_HA_Open"]],
            on="Period",
            how="left",
        )
        .set_index(index_name)
    )

    # Calculate forming close: (Open + High + Low + Close) / 4
    # using cumulative values for the period
    df_eval["TF_Open"] = df_eval.groupby("Period")["open"].transform("first")
    df_eval["TF_High"] = df_eval.groupby("Period")["high"].cummax()
    df_eval["TF_Low"] = df_eval.groupby("Period")["low"].cummin()

    df_eval["Forming_HA_Close"] = (
        df_eval["TF_Open"] + df_eval["TF_High"] + df_eval["TF_Low"] + df_eval["close"]
    ) / 4
    df_eval["Forming_HA_Color"] = np.where(
        df_eval["Forming_HA_Close"] >= df_eval["Current_HA_Open"], "Green", "Red"
    )

    return df_eval[["Prev_HA_Color", "Forming_HA_Color"]]


def run_backtest(conn, div_pct: float = 2.0) -> dict:
    """Run backtest for all tickers."""
    import math

    from tradingtools_stock.core.fetcher import get_active_tickers

    tickers = get_active_tickers(conn)

    all_trades = []

    for ticker in tickers:
        try:
            df = fetch_daily_data(conn, ticker)
            if df.empty or len(df) < 200:
                continue

            price = df["close"]
            ema21 = price.ewm(span=21, adjust=False).mean()
            sma50 = price.rolling(window=50).mean()
            sma100 = price.rolling(window=100).mean()
            sma200 = price.rolling(window=200).mean()

            daily_momentum_ok = (
                (price > sma200)
                & (ema21 > sma200)
                & (sma50 > sma200)
                & (sma100 > sma200)
            )

            ha_1m = calculate_forming_ha_colors(df, "ME")

            condition = (
                (ha_1m["Prev_HA_Color"] == "Red")
                & (ha_1m["Forming_HA_Color"] == "Green")
                & daily_momentum_ok
            )
            trigger = condition & ~condition.shift(1, fill_value=False)

            entries = df[trigger].copy()
            current_price = price.iloc[-1]
            current_date = df.index[-1]

            for date, row in entries.iterrows():
                entry_price = row["close"]

                holder_return = (current_price - entry_price) / entry_price * 100
                holder_days = max(1, (current_date - date).days)

                # Quarterly Compounded DRIP
                full_quarters = math.floor(holder_days / 91.25)
                compounded_shares = (1 + (div_pct / 400)) ** full_quarters
                drip_value = compounded_shares * current_price
                drip_return = (drip_value - entry_price) / entry_price * 100

                all_trades.append(
                    {
                        "Ticker": ticker,
                        "Entry Date": date.strftime("%Y-%m-%d"),
                        "Entry Price": entry_price,
                        "Days Held": holder_days,
                        "No_Divs Return %": holder_return,
                        "DRIP Return %": drip_return,
                    }
                )
        except Exception as e:
            print(f"Error backtesting {ticker}: {e}")

    df_trades = pd.DataFrame(all_trades)

    if df_trades.empty:
        return {
            "trades": df_trades,
            "summary": pd.DataFrame(),
            "avg_trades_per_year": 0,
            "avg_trades_per_week": 0,
        }

    df_trades["Year"] = pd.to_datetime(df_trades["Entry Date"]).dt.year.astype(str)
    avg_trades_per_year = df_trades.groupby("Year").size().mean()

    df_trades["Week"] = (
        pd.to_datetime(df_trades["Entry Date"]).dt.to_period("W").astype(str)
    )
    avg_trades_per_week = df_trades.groupby("Week").size().mean()

    summary = (
        df_trades.groupby("Ticker")
        .agg(
            Trades=("Ticker", "count"),
            Avg_Days_Held=("Days Held", "mean"),
            No_Divs_Avg_Return_Pct=("No_Divs Return %", "mean"),
            DRIP_Avg_Return_Pct=("DRIP Return %", "mean"),
        )
        .reset_index()
    )

    df_trades["No_Divs_Win"] = df_trades["No_Divs Return %"] > 0
    df_trades["DRIP_Win"] = df_trades["DRIP Return %"] > 0

    win_rates = (
        df_trades.groupby("Ticker")
        .agg(
            No_Divs_Win_Rate=("No_Divs_Win", "mean"), DRIP_Win_Rate=("DRIP_Win", "mean")
        )
        .reset_index()
    )
    win_rates["No_Divs_Win_Rate"] *= 100
    win_rates["DRIP_Win_Rate"] *= 100

    summary = pd.merge(summary, win_rates, on="Ticker")

    summary["Invested ($)"] = summary["Trades"] * 1.0
    summary["No_Divs Current Value ($)"] = summary["Invested ($)"] * (
        1 + summary["No_Divs_Avg_Return_Pct"] / 100
    )
    summary["DRIP Current Value ($)"] = summary["Invested ($)"] * (
        1 + summary["DRIP_Avg_Return_Pct"] / 100
    )

    return {
        "trades": df_trades,
        "summary": summary,
        "avg_trades_per_year": avg_trades_per_year,
        "avg_trades_per_week": avg_trades_per_week,
    }
