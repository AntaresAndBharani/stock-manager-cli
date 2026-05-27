import streamlit as st
import pandas as pd
from tradingtools_stock.core.fetcher import get_db_connection
from tradingtools_stock.core.strategies import get_dashboard_data

st.set_page_config(page_title="Trading Tools Dashboard", layout="wide")

st.title("Heikin-Ashi Trends Dashboard")
st.markdown("Analyze the Heikin-Ashi color of the current and previous two periods for 1-Month and 3-Month (Calendar Quarter) views.")

@st.cache_data(ttl=3600)
def load_data():
    conn = get_db_connection()
    try:
        df = get_dashboard_data(conn)
        return df
    finally:
        conn.close()

with st.spinner("Loading Heikin-Ashi data..."):
    try:
        df = load_data()
        
        if df.empty:
            st.warning("No active tickers found or no data available. Please fetch stock data first.")
        else:
            # Refresh button
            if st.button("Refresh Data"):
                load_data.clear()
                st.rerun()
                
            # Color formatter for Daily MAs relative to 200 SMA
            def highlight_mas(row):
                sma200 = row['200 SMA']
                styles = [''] * len(row)
                cols_to_check = ['Price', '21 EMA', '50 SMA', '100 SMA']
                
                for col in cols_to_check:
                    if col in row.index:
                        idx = row.index.get_loc(col)
                        if pd.isna(row[col]) or pd.isna(sma200):
                            continue
                        if row[col] < sma200:
                            styles[idx] = 'color: #ff5555; font-weight: bold;'  # Red
                        else:
                            styles[idx] = 'color: #00ff00; font-weight: bold;'  # Green
                return styles
                
            styled_df = df.style.apply(highlight_mas, axis=1)
                
            # Display the condensed dataframe
            st.dataframe(
                styled_df,
                column_config={
                    "Price": st.column_config.NumberColumn(format="$%.2f"),
                    "21 EMA": st.column_config.NumberColumn(format="$%.2f"),
                    "50 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "100 SMA": st.column_config.NumberColumn(format="$%.2f"),
                    "200 SMA": st.column_config.NumberColumn(format="$%.2f"),
                },
                width='stretch',
                hide_index=True
            )
            
    except Exception as e:
        st.error(f"Error loading dashboard data: {e}")
