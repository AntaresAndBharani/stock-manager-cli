import pandas as pd
import numpy as np
import warnings

def calculate_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate Heikin-Ashi candles for a dataframe with Open, High, Low, Close columns.
    """
    ha_df = df.copy()
    
    # HA Close = (Open + High + Low + Close) / 4
    ha_df['HA_Close'] = (df['Open'] + df['High'] + df['Low'] + df['Close']) / 4
    
    # Initialize HA Open with the first real Open and Close
    ha_open = np.zeros(len(df))
    ha_open[0] = (df['Open'].iloc[0] + df['Close'].iloc[0]) / 2
    
    for i in range(1, len(df)):
        ha_open[i] = (ha_open[i-1] + ha_df['HA_Close'].iloc[i-1]) / 2
        
    ha_df['HA_Open'] = ha_open
    
    # HA High = max(High, HA Open, HA Close)
    ha_df['HA_High'] = ha_df[['High', 'HA_Open', 'HA_Close']].max(axis=1)
    
    # HA Low = min(Low, HA Open, HA Close)
    ha_df['HA_Low'] = ha_df[['Low', 'HA_Open', 'HA_Close']].min(axis=1)
    
    # Color
    ha_df['HA_Color'] = np.where(ha_df['HA_Close'] >= ha_df['HA_Open'], 'Green', 'Red')
    
    return ha_df

def fetch_daily_data(conn, symbol: str) -> pd.DataFrame:
    """Fetch raw daily data from the database."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        query = "SELECT date, open, high, low, close FROM stock_prices WHERE symbol = %s ORDER BY date"
        df = pd.read_sql_query(query, conn, params=(symbol,), parse_dates=['date'])
    if not df.empty:
        df.set_index('date', inplace=True)
    return df

def resample_and_calculate_ha(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Resample daily dataframe and calculate Heikin-Ashi."""
    if df.empty:
        return df
        
    try:
        resampled = df.resample(timeframe).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        }).dropna()
    except ValueError:
        # Fallback to older pandas alias
        alt_tf = timeframe.replace('E', '')
        resampled = df.resample(alt_tf).agg({
            'open': 'first',
            'high': 'max',
            'low': 'min',
            'close': 'last'
        }).dropna()
    
    resampled.rename(columns={'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close'}, inplace=True)
    return calculate_heikin_ashi(resampled)

def get_dashboard_data(conn) -> pd.DataFrame:
    """
    Get the dashboard data for all active tickers.
    """
    # Get active tickers
    from tradingtools_stock.core.fetcher import get_active_tickers
    tickers = get_active_tickers(conn)
    
    results = []
    for ticker in tickers:
        try:
            df_daily = fetch_daily_data(conn, ticker)
            if df_daily.empty:
                continue
                
            # Daily Indicators
            price = df_daily['close'].iloc[-1]
            ema21 = df_daily['close'].ewm(span=21, adjust=False).mean().iloc[-1]
            sma50 = df_daily['close'].rolling(window=50).mean().iloc[-1]
            sma100 = df_daily['close'].rolling(window=100).mean().iloc[-1]
            sma200 = df_daily['close'].rolling(window=200).mean().iloc[-1]
            
            df_1m = resample_and_calculate_ha(df_daily, 'ME')
            df_3m = resample_and_calculate_ha(df_daily, 'QE')
            
            def get_last_3_colors(df):
                colors = df['HA_Color'].tolist()
                return (colors[-3:] if len(colors) >= 3 else [None] * (3 - len(colors)) + colors)
                
            colors_1m = get_last_3_colors(df_1m) if not df_1m.empty else [None, None, None]
            colors_3m = get_last_3_colors(df_3m) if not df_3m.empty else [None, None, None]
            
            def format_trend(colors):
                emojis = []
                for c in colors:
                    if c == 'Green':
                        emojis.append('🟩')
                    elif c == 'Red':
                        emojis.append('🟥')
                    else:
                        emojis.append('⬜')
                return ' '.join(emojis)
                
            # Signal logic
            # Entry Trigger: 1M T-1 is Red AND 1M Current is Green AND Daily Momentum Filter
            daily_momentum_ok = (price > sma200) and (ema21 > sma200) and (sma50 > sma200) and (sma100 > sma200)
            
            signal = "⚪ None"
            if colors_1m[1] == 'Red' and colors_1m[2] == 'Green' and daily_momentum_ok:
                if colors_3m[1] == 'Red':
                    signal = "🟡 Weak Entry"
                else:
                    signal = "🟢 Strong Entry"
            
            results.append({
                'Ticker': ticker,
                'Signal': signal,
                '1M Trend': format_trend(colors_1m),
                '3M Trend': format_trend(colors_3m),
                'Price': price,
                '21 EMA': ema21,
                '50 SMA': sma50,
                '100 SMA': sma100,
                '200 SMA': sma200,
            })
        except Exception as e:
            print(f"Error processing {ticker}: {e}")
            
    df_results = pd.DataFrame(results)
    if not df_results.empty:
        # Sort so Strong Entry > Weak Entry > None
        signal_order = {"🟢 Strong Entry": 0, "🟡 Weak Entry": 1, "⚪ None": 2}
        df_results['_sort'] = df_results['Signal'].map(signal_order)
        df_results = df_results.sort_values(['_sort', 'Ticker']).drop(columns=['_sort']).reset_index(drop=True)
        
    return df_results
