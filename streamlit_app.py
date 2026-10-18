import sqlite3
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import streamlit as st
import plotly.graph_objects as go

st.set_page_config(page_title="NT Pro Scanner", page_icon="⚡", layout="wide")

DB_FILE = "oi_timeseries.db"

INSTRUMENTS = {
    "NSE Indices": {
        "NIFTY": {"step": 50, "base": 25000.0, "type": "index", "yf": "^NSEI", "mult": 1.0},
        "BANKNIFTY": {"step": 100, "base": 53000.0, "type": "index", "yf": "^NSEBANK", "mult": 1.0},
    },
    "NSE F&O Stocks": {
        "RELIANCE": {"step": 10, "base": 1400.0, "type": "equities", "yf": "RELIANCE.NS", "mult": 1.0},
        "HDFCBANK": {"step": 10, "base": 1750.0, "type": "equities", "yf": "HDFCBANK.NS", "mult": 1.0},
    },
    "MCX Commodities": {
        "CRUDEOIL": {"step": 50, "base": 6000.0, "type": "mcx", "yf": "CL=F", "mult": 86.5},
        "NATURALGAS": {"step": 5, "base": 240.0, "type": "mcx", "yf": "NG=F", "mult": 86.5},
    }
}

class NiftyTraderEngineV5:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {"User-Agent": "Mozilla/5.0"}
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS trending_oi (timestamp TEXT, symbol TEXT, spot_price REAL, sum_ce_chg_oi INTEGER, sum_pe_chg_oi INTEGER, net_oi_diff INTEGER, pcr REAL, max_pain REAL)")

    def fetch_chain(self, segment, symbol):
        cfg = INSTRUMENTS[segment][symbol]
        spot = cfg["base"] + np.random.normal(0, cfg["step"] * 0.2)
        step = cfg["step"]
        atm = round(spot / step) * step
        strikes = [round(atm + i * step, 2) for i in range(-5, 6)]
        
        rows = []
        for idx, k in enumerate(strikes):
            ce_oi, pe_oi = 150000, 160000
            rows.append({
                "strike": k, "step_dist": idx - 5,
                "ce_oi": ce_oi, "ce_chg_oi": 5000, "ce_ltp": max(1.0, round(spot - k + 100, 2)), "ce_chg_pts": 5.0, "ce_buildup": "Long Build-up",
                "pe_oi": pe_oi, "pe_chg_oi": 6000, "pe_ltp": max(1.0, round(k - spot + 100, 2)), "pe_chg_pts": -3.0, "pe_buildup": "Short Covering",
                "strike_pcr": 1.06
            })
        df = pd.DataFrame(rows)
        pcr = 1.08
        return df, {
            "segment": segment, "symbol": symbol, "spot_price": round(spot, 2),
            "fut_chg_pct": 0.45, "fut_buildup": "Long Build-up", "pcr": pcr,
            "max_pain": atm, "net_oi_diff": 15000,
            "trade_action": "🟢 BUY CALL (CE)", "best_contract": f"{symbol} {atm} CE",
            "entry_ltp": 125.0, "target": 168.0, "stop_loss": 100.0,
            "support_strike": atm - step, "res_strike": atm + step, "setup_reason": "Put Support"
        }

    def scan_market_summary(self, selected_segments):
        res = []
        for seg in selected_segments:
            for sym in INSTRUMENTS[seg].keys():
                _, summary = self.fetch_chain(seg, sym)
                res.append(summary)
        return pd.DataFrame(res)

    def scan_individual_strikes(self, selected_segments):
        rows = []
        for seg in selected_segments:
            for sym in INSTRUMENTS[seg].keys():
                df, summary = self.fetch_chain(seg, sym)
                spot = summary["spot_price"]
                for _, r in df.iterrows():
                    rows.append({
                        "Symbol": sym, "Contract": f"{sym} {r['strike']} CE", "Type": "CE",
                        "Moneyness": "ATM" if r['step_dist']==0 else "ITM", "Near_ATM": abs(r['step_dist'])<=2,
                        "LTP": r['ce_ltp'], "OI": r['ce_oi'], "Buildup": r['ce_buildup'], "Target": r['ce_ltp']*1.35, "SL": r['ce_ltp']*0.8
                    })
                    rows.append({
                        "Symbol": sym, "Contract": f"{sym} {r['strike']} PE", "Type": "PE",
                        "Moneyness": "ATM" if r['step_dist']==0 else "OTM", "Near_ATM": abs(r['step_dist'])<=2,
                        "LTP": r['pe_ltp'], "OI": r['pe_oi'], "Buildup": r['pe_buildup'], "Target": r['pe_ltp']*1.35, "SL": r['pe_ltp']*0.8
                    })
        return pd.DataFrame(rows)

@st.cache_resource
def get_engine():
    return NiftyTraderEngineV5()

engine = get_engine()

st.subheader("⚡ NT Pro: Option Strike Scanner")

tab_manual, tab_auto, tab_chain = st.tabs(["🎛️ Manual Strike Scanner", "🎯 Auto Best Strikes", "📊 Option Chain"])

with tab_manual:
    m_segs = st.multiselect("Segments", list(INSTRUMENTS.keys()), default=["NSE Indices"])
    if st.button("🔍 Scan Strikes", use_container_width=True):
        pass
    if m_segs:
        df_s = engine.scan_individual_strikes(m_segs)
        st.dataframe(df_s, use_container_width=True, height=400)

with tab_auto:
    seg_f = st.multiselect("Segments to Scan", list(INSTRUMENTS.keys()), default=["NSE Indices"], key="auto_seg")
    if st.button("🚀 Run Auto Scan", use_container_width=True):
        pass
    if seg_f:
        df_a = engine.scan_market_summary(seg_f)
        st.dataframe(df_a[["symbol", "spot_price", "trade_action", "best_contract", "entry_ltp", "target", "stop_loss", "pcr"]], use_container_width=True, height=400)

with tab_chain:
    c1, c2 = st.columns(2)
    seg = c1.selectbox("Segment", list(INSTRUMENTS.keys()), key="chain_seg")
    sym = c2.selectbox("Symbol", list(INSTRUMENTS[seg].keys()), key="chain_sym")
    df_c, sum_c = engine.fetch_chain(seg, sym)
    st.metric(f"{sym} Spot", f"₹{sum_c['spot_price']:,.1f}", sum_c['trade_action'])
    st.dataframe(df_c, use_container_width=True, height=400)
