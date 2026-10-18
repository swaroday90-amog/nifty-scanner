import streamlit as st
import pandas as pd
import numpy as np

st.set_page_config(page_title="NT Pro Scanner", page_icon="⚡", layout="wide")

st.subheader("⚡ NT Pro Mobile Scanner")

data = {
    "Symbol": ["NIFTY", "BANKNIFTY", "RELIANCE", "CRUDEOIL"],
    "Spot": [25000.0, 53000.0, 1400.0, 6000.0],
    "PCR": [1.12, 0.98, 1.25, 1.05],
    "Signal": ["Bullish", "Bearish", "Bullish", "Neutral"]
}
df = pd.DataFrame(data)

c1, c2 = st.columns(2)
symbol = c1.selectbox("Select Symbol", df["Symbol"].tolist())
if c2.button("🔄 Refresh", use_container_width=True):
    pass

st.dataframe(df, use_container_width=True)
st.success("App is running successfully!")
