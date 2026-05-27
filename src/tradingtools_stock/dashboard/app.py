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
                
            # Display the condensed dataframe
            st.dataframe(
                df,
                width='stretch',
                hide_index=True
            )
            
    except Exception as e:
        st.error(f"Error loading dashboard data: {e}")
