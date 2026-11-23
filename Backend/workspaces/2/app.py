import streamlit as st
import plotly.express as px
import pandas as pd
from src.model import fetch_stock_data, prepare_features, train_model

st.set_page_config(page_title="Stock Market Prediction Dashboard", layout="wide")

st.title("📈 Stock Market Prediction & Analysis Dashboard")

st.sidebar.header("Configuration")
ticker = st.sidebar.text_input("Stock Ticker", value="AAPL").upper()
period = st.sidebar.selectbox("Historical Period", ["1y", "2y", "5y"], index=1)

if st.sidebar.button("Run Analysis"):
    with st.spinner(f"Fetching data and training model for {ticker}..."):
        try:
            df_raw = fetch_stock_data(ticker, period=period)
            df = prepare_features(df_raw)
            model, accuracy = train_model(df)
            
            st.success(f"Model trained successfully! Test Accuracy: {accuracy:.2f}")
            
            col1, col2, col3 = st.columns(3)
            col1.metric("Latest Close", f"${df_raw['Close'].iloc[-1]:.2f}")
            col2.metric("Data Points", len(df_raw))
            col3.metric("Model Accuracy", f"{accuracy * 100:.1f}%")
            
            st.subheader(f"Historical Close Price & Moving Averages for {ticker}")
            fig = px.line(df_raw, x=df_raw.index, y=["Close"], title=f"{ticker} Stock Price")
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("Raw Data Preview")
            st.dataframe(df_raw.tail(10))
            
        except Exception as e:
            st.error(f"Error processing {ticker}: {e}")
else:
    st.info("Enter a stock ticker in the sidebar and click 'Run Analysis' to get started.")
