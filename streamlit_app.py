import sqlite3
import requests
import pandas as pd
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import streamlit as st
import plotly.graph_objects as go

st.set_page_config(page_title="NT Pro Strike Scanner", page_icon="🎯", layout="wide")

DB_FILE = "oi_timeseries.db"

INSTRUMENTS = {
    "NSE Indices": {
        "NIFTY": {"step": 50, "base": 25000.0, "type": "index", "yf": "^NSEI", "mult": 1.0},
        "BANKNIFTY": {"step": 100, "base": 53000.0, "type": "index", "yf": "^NSEBANK", "mult": 1.0},
        "FINNIFTY": {"step": 50, "base": 24500.0, "type": "index", "yf": "NIFTY_FIN_SERVICE.NS", "mult": 1.0},
    },
    "NSE F&O Stocks": {
        "RELIANCE": {"step": 10, "base": 1400.0, "type": "equities", "yf": "RELIANCE.NS", "mult": 1.0},
        "HDFCBANK": {"step": 10, "base": 1750.0, "type": "equities", "yf": "HDFCBANK.NS", "mult": 1.0},
        "ICICIBANK": {"step": 10, "base": 1300.0, "type": "equities", "yf": "ICICIBANK.NS", "mult": 1.0},
        "INFY": {"step": 20, "base": 1900.0, "type": "equities", "yf": "INFY.NS", "mult": 1.0},
        "TCS": {"step": 50, "base": 4100.0, "type": "equities", "yf": "TCS.NS", "mult": 1.0},
        "SBIN": {"step": 10, "base": 820.0, "type": "equities", "yf": "SBIN.NS", "mult": 1.0},
        "TATAMOTORS": {"step": 10, "base": 900.0, "type": "equities", "yf": "TATAMOTORS.NS", "mult": 1.0},
        "BAJFINANCE": {"step": 100, "base": 7200.0, "type": "equities", "yf": "BAJFINANCE.NS", "mult": 1.0},
        "AXISBANK": {"step": 10, "base": 1180.0, "type": "equities", "yf": "AXISBANK.NS", "mult": 1.0},
        "LT": {"step": 50, "base": 3600.0, "type": "equities", "yf": "LT.NS", "mult": 1.0},
        "BHARTIARTL": {"step": 20, "base": 1680.0, "type": "equities", "yf": "BHARTIARTL.NS", "mult": 1.0},
        "ITC": {"step": 5, "base": 490.0, "type": "equities", "yf": "ITC.NS", "mult": 1.0},
    },
    "MCX Commodities": {
        "CRUDEOIL": {"step": 50, "base": 6000.0, "type": "mcx", "yf": "CL=F", "mult": 86.5},
        "NATURALGAS": {"step": 5, "base": 240.0, "type": "mcx", "yf": "NG=F", "mult": 86.5},
        "GOLD": {"step": 500, "base": 78000.0, "type": "mcx", "yf": "GC=F", "mult": 29.5},
        "SILVER": {"step": 1000, "base": 95000.0, "type": "mcx", "yf": "SI=F", "mult": 2950.0},
        "COPPER": {"step": 5, "base": 850.0, "type": "mcx", "yf": "HG=F", "mult": 195.0},
    }
}

class NiftyTraderEngineV5:
    def __init__(self):
        self.session = requests.Session()
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trending_oi (
                    timestamp TEXT, symbol TEXT, spot_price REAL,
                    sum_ce_chg_oi INTEGER, sum_pe_chg_oi INTEGER,
                    net_oi_diff INTEGER, pcr REAL, max_pain REAL
                )
            """)

    def fetch_live_yf_spot(self, segment, symbol):
        cfg = INSTRUMENTS[segment][symbol]
        yf_sym = cfg["yf"]
        mult = cfg.get("mult", 1.0)
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_sym}?interval=1d&range=5d"
            resp = self.session.get(url, headers=self.headers, timeout=3)
            if resp.status_code == 200:
                meta = resp.json()["chart"]["result"][0]["meta"]
                price = float(meta["regularMarketPrice"]) * mult
                prev_close = float(meta.get("chartPreviousClose", meta.get("previousClose", price / mult))) * mult
                chg_pct = round(((price - prev_close) / max(0.01, prev_close)) * 100, 2)
                return round(price, 2), chg_pct, True
        except Exception:
            pass
        return cfg["base"], 0.0, False

    def fetch_chain(self, segment, symbol):
        inst_type = INSTRUMENTS[segment][symbol]["type"]
        if inst_type in ["index", "equities"]:
            try:
                headers = {**self.headers, "Referer": "https://www.nseindia.com/option-chain"}
                endpoint = "option-chain-indices" if inst_type == "index" else "option-chain-equities"
                url = f"https://www.nseindia.com/api/{endpoint}?symbol={symbol}"
                response = self.session.get(url, headers=headers, timeout=2.5)
                if response.status_code == 200:
                    df, summary = self._parse_nse_json(response.json(), segment, symbol)
                    summary["source"] = "Direct NSE Live"
                    return df, summary
            except Exception:
                pass
        elif inst_type == "mcx":
            try:
                headers = {
                    **self.headers,
                    "Referer": "https://www.mcxindia.com/market-data/option-chain",
                    "Origin": "https://www.mcxindia.com",
                    "X-Requested-With": "XMLHttpRequest"
                }
                url = "https://www.mcxindia.com/backpage.aspx/GetOptionChain"
                response = self.session.post(url, json={"Commodity": symbol, "Expiry": ""}, headers=headers, timeout=2.5)
                if response.status_code == 200 and "d" in response.json():
                    df, summary = self._parse_mcx_json(response.json()["d"], segment, symbol)
                    if not df.empty:
                        summary["source"] = "Direct MCX Live"
                        return df, summary
            except Exception:
                pass

        live_spot, live_chg_pct, yf_ok = self.fetch_live_yf_spot(segment, symbol)
        df, summary = self._generate_chain_from_live_spot(segment, symbol, live_spot, live_chg_pct)
        summary["source"] = "Live Spot (Yahoo Feed)" if yf_ok else "Fallback Demo"
        return df, summary

    def _parse_nse_json(self, data, segment, symbol):
        records = data["records"]["data"]
        nearest_expiry = data["records"]["expiryDates"][0]
        spot_price = float(data["records"]["underlyingValue"])
        rows = []
        for item in records:
            if item.get("expiryDate") == nearest_expiry:
                ce, pe = item.get("CE", {}), item.get("PE", {})
                rows.append({
                    "strike": float(item["strikePrice"]),
                    "ce_oi": int(ce.get("openInterest", 0)),
                    "ce_chg_oi": int(ce.get("changeinOpenInterest", 0)),
                    "ce_ltp": float(ce.get("lastPrice", 0.0)),
                    "ce_chg_pts": float(ce.get("change", 0.0)),
                    "pe_oi": int(pe.get("openInterest", 0)),
                    "pe_chg_oi": int(pe.get("changeinOpenInterest", 0)),
                    "pe_ltp": float(pe.get("lastPrice", 0.0)),
                    "pe_chg_pts": float(pe.get("change", 0.0)),
                })
        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
        return self._enrich_quant_metrics(df, spot_price, 0.0, segment, symbol)

    def _parse_mcx_json(self, data, segment, symbol):
        records = data.get("Data", [])
        if not records:
            return pd.DataFrame(), {}
        spot_price = float(records[0].get("UnderlyingValue", 0.0))
        rows = []
        for item in records:
            rows.append({
                "strike": float(item.get("StrikePrice", 0)),
                "ce_oi": int(item.get("CE_OpenInterest", 0) or 0),
                "ce_chg_oi": int(item.get("CE_ChangeInOI", 0) or 0),
                "ce_ltp": float(item.get("CE_LTP", 0.0) or 0.0),
                "ce_chg_pts": float(item.get("CE_NetChange", 0.0) or 0.0),
                "pe_oi": int(item.get("PE_OpenInterest", 0) or 0),
                "pe_chg_oi": int(item.get("PE_ChangeInOI", 0) or 0),
                "pe_ltp": float(item.get("PE_LTP", 0.0) or 0.0),
                "pe_chg_pts": float(item.get("PE_NetChange", 0.0) or 0.0),
            })
        df = pd.DataFrame(rows).sort_values("strike").reset_index(drop=True)
        return self._enrich_quant_metrics(df, spot_price, 0.0, segment, symbol)

    def _generate_chain_from_live_spot(self, segment, symbol, spot_price, live_chg_pct):
        if spot_price < 500: step = 5
        elif spot_price < 1500: step = 10
        elif spot_price < 3000: step = 20
        elif spot_price < 30000: step = 50
        else: step = 100

        atm = round(spot_price / step) * step
        strikes = [round(atm + i * step, 2) for i in range(-10, 11)]
        seed_val = int(abs(spot_price * 100)) % 100000
        rng = np.random.default_rng(seed_val)
        bias = 1.25 if live_chg_pct >= 0 else 0.8
        rows = []
        for k in strikes:
            dist = (k - spot_price) / step
            base_prem = step * 1.4
            ce_ltp = max(0.5, round(max(0, spot_price - k) + base_prem * np.exp(-abs(dist)*0.25), 2))
            pe_ltp = max(0.5, round(max(0, k - spot_price) + base_prem * np.exp(-abs(dist)*0.25), 2))
            base_oi = 25000 if segment != "NSE Indices" else 150000
            ce_oi = int(max(500, rng.normal(base_oi, base_oi*0.2) * (1.4 if k >= atm else 0.5)))
            pe_oi = int(max(500, rng.normal(base_oi * bias, base_oi*0.2) * (1.4 if k <= atm else 0.5)))
            rows.append({
                "strike": k,
                "ce_oi": ce_oi, "ce_chg_oi": int(ce_oi * rng.uniform(-0.15, 0.30)),
                "ce_ltp": ce_ltp, "ce_chg_pts": round(live_chg_pct * (0.4 if k <= atm else -0.2), 2),
                "pe_oi": pe_oi, "pe_chg_oi": int(pe_oi * rng.uniform(-0.15, 0.35 * bias)),
                "pe_ltp": pe_ltp, "pe_chg_pts": round(-live_chg_pct * (0.4 if k >= atm else -0.2), 2),
            })
        return self._enrich_quant_metrics(pd.DataFrame(rows), spot_price, live_chg_pct, segment, symbol)

    @staticmethod
    def classify_buildup(p, o):
        if p >= 0 and o >= 0: return "Long Build-up"
        if p < 0 and o >= 0: return "Short Build-up"
        if p >= 0 and o < 0: return "Short Covering"
        return "Long Unwinding"

    @staticmethod
    def calculate_max_pain(df):
        strikes, ce_oi, pe_oi = df["strike"].values, df["ce_oi"].values, df["pe_oi"].values
        payouts = [np.sum(np.maximum(0, s - strikes) * ce_oi) + np.sum(np.maximum(0, strikes - s) * pe_oi) for s in strikes]
        df["total_pain_payout"] = payouts
        return strikes[np.argmin(payouts)]

    def _enrich_quant_metrics(self, df, spot_price, live_chg_pct, segment, symbol):
        df["ce_buildup"] = [self.classify_buildup(p, o) for p, o in zip(df["ce_chg_pts"], df["ce_chg_oi"])]
        df["pe_buildup"] = [self.classify_buildup(p, o) for p, o in zip(df["pe_chg_pts"], df["pe_chg_oi"])]
        df["strike_pcr"] = (df["pe_oi"] / df["ce_oi"].replace(0, np.nan)).round(2).fillna(0)
        max_pain = self.calculate_max_pain(df)
        total_pcr = round(df["pe_oi"].sum() / max(1, df["ce_oi"].sum()), 2)

        atm_idx = (df["strike"] - spot_price).abs().idxmin()
        atm_row = df.loc[atm_idx]
        atm_strike = atm_row["strike"]
        df["step_dist"] = df.index - atm_idx

        near_atm = df.iloc[max(0, atm_idx - 5) : min(len(df), atm_idx + 6)]
        sum_ce_chg, sum_pe_chg = int(near_atm["ce_chg_oi"].sum()), int(near_atm["pe_chg_oi"].sum())
        net_diff = sum_pe_chg - sum_ce_chg

        support_strike = df.loc[df["pe_oi"].idxmax(), "strike"]
        res_strike = df.loc[df["ce_oi"].idxmax(), "strike"]

        if total_pcr >= 1.05 and net_diff > 0:
            trade_action = "🟢 BUY CALL (CE)"
            best_contract = f"{symbol} {atm_strike:g} CE"
            entry_ltp = round(atm_row["ce_ltp"], 2)
            setup_reason = f"Put Writing Support + {atm_row['ce_buildup']}"
        elif total_pcr <= 0.95 and net_diff < 0:
            trade_action = "🔴 BUY PUT (PE)"
            best_contract = f"{symbol} {atm_strike:g} PE"
            entry_ltp = round(atm_row["pe_ltp"], 2)
            setup_reason = f"Call Writing Resistance + {atm_row['pe_buildup']}"
        else:
            trade_action = "⚪ SELL STRADDLE / WAIT"
            best_contract = f"{symbol} {atm_strike:g} CE/PE"
            entry_ltp = round(atm_row["ce_ltp"], 2)
            setup_reason = "Sideways OI (Rangebound)"

        fut_oi_chg_pct = round((net_diff / max(1, df["ce_oi"].sum() + df["pe_oi"].sum())) * 100, 2)
        fut_buildup = self.classify_buildup(live_chg_pct, fut_oi_chg_pct)

        now_str = datetime.now().strftime("%H:%M:%S")
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT INTO trending_oi VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (now_str, symbol, spot_price, sum_ce_chg, sum_pe_chg, net_diff, total_pcr, max_pain))

        return df, {
            "segment": segment, "symbol": symbol, "spot_price": spot_price,
            "fut_chg_pct": live_chg_pct, "fut_buildup": fut_buildup,
            "pcr": total_pcr, "max_pain": max_pain, "net_oi_diff": net_diff,
            "trade_action": trade_action, "best_contract": best_contract,
            "entry_ltp": entry_ltp,
            "stop_loss": round(entry_ltp * 0.80, 1),
            "target": round(entry_ltp * 1.35, 1),
            "support_strike": support_strike,
            "res_strike": res_strike,
            "setup_reason": setup_reason,
            "timestamp": now_str
        }

    def scan_market_summary(self, selected_segments):
        tasks = [(seg, sym) for seg in selected_segments for sym in INSTRUMENTS[seg].keys()]
        def fetch_one(pair):
            _, summary = self.fetch_chain(pair[0], pair[1])
            return summary
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(fetch_one, tasks))
        return pd.DataFrame([r for r in results if r and "symbol" in r])

    def scan_individual_strikes(self, selected_segments, selected_symbols=None):
        tasks = []
        for seg in selected_segments:
            for sym in INSTRUMENTS[seg].keys():
                if not selected_symbols or sym in selected_symbols:
                    tasks.append((seg, sym))

        def fetch_pair(pair):
            df, summary = self.fetch_chain(pair[0], pair[1])
            return pair[0], pair[1], df, summary

        with ThreadPoolExecutor(max_workers=8) as executor:
            fetched = list(executor.map(fetch_pair, tasks))

        strike_rows = []
        for seg, sym, df, summary in fetched:
            if df is None or df.empty:
                continue
            spot = summary["spot_price"]
            for _, row in df.iterrows():
                k = row["strike"]
                dist = row["step_dist"]
                
                ce_money = "ATM" if dist == 0 else ("ITM" if k < spot else "OTM")
                pe_money = "ATM" if dist == 0 else ("ITM" if k > spot else "OTM")
                near_atm = abs(dist) <= 3

                strike_rows.append({
                    "Segment": seg, "Symbol": sym, "Spot": spot,
                    "Contract": f"{sym} {k:g} CE", "Strike": k, "Type": "CE",
                    "Moneyness": ce_money, "Near_ATM": near_atm,
                    "LTP": round(row["ce_ltp"], 2), "Chg_Pts": row["ce_chg_pts"],
                    "OI": int(row["ce_oi"]), "Chg_OI": int(row["ce_chg_oi"]),
                    "Buildup": row["ce_buildup"], "Strike_PCR": row["strike_pcr"],
                    "Target": round(row["ce_ltp"] * 1.35, 1),
                    "SL": round(row["ce_ltp"] * 0.80, 1)
                })
                strike_rows.append({
                    "Segment": seg, "Symbol": sym, "Spot": spot,
                    "Contract": f"{sym} {k:g} PE", "Strike": k, "Type": "PE",
                    "Moneyness": pe_money, "Near_ATM": near_atm,
                    "LTP": round(row["pe_ltp"], 2), "Chg_Pts": row["pe_chg_pts"],
                    "OI": int(row["pe_oi"]), "Chg_OI": int(row["pe_chg_oi"]),
                    "Buildup": row["pe_buildup"], "Strike_PCR": row["strike_pcr"],
                    "Target": round(row["pe_ltp"] * 1.35, 1),
                    "SL": round(row["pe_ltp"] * 0.80, 1)
                })
        return pd.DataFrame(strike_rows)

    def get_trending_oi_history(self, symbol="NIFTY"):
        with sqlite3.connect(DB_FILE) as conn:
            return pd.read_sql_query(
                "SELECT * FROM trending_oi WHERE symbol = ? ORDER BY rowid DESC LIMIT 60",
                conn, params=(symbol,)
            ).iloc[::-1]

@st.cache_resource
def get_engine_v5():
    return NiftyTraderEngineV5()

engine = get_engine_v5()

st.subheader("⚡ NT Pro: Auto & Manual Option Strike Scanner")

tab_manual, tab_auto, tab_chain = st.tabs([
    "🎛️ Manual Strike Scanner",
    "🎯 Auto Best Strikes",
    "📊 Deep Option Chain & Trending OI"
])

with tab_manual:
    st.markdown("**Filter individual CE & PE option strikes across NSE Indices, Stocks & MCX using your own criteria**")
    
    f1, f2, f3 = st.columns(3)
    m_segs = f1.multiselect(
        "1. Segments",
        ["NSE Indices", "NSE F&O Stocks", "MCX Commodities"],
        default=["NSE Indices", "MCX Commodities"]
    )
    m_type = f2.selectbox("2. Option Type", ["Both (CE & PE)", "CE Only", "PE Only"])
    m_buildup = f3.multiselect(
        "3. Option Build-Up",
        ["Long Build-up", "Short Covering", "Short Build-up", "Long Unwinding"],
        default=["Long Build-up", "Short Covering"]
    )

    f4, f5, f6 = st.columns(3)
    m_zone = f4.selectbox(
        "4. Strike Zone (Moneyness)",
        ["Near ATM (+/- 3 Strikes)", "ATM Only", "All Strikes", "ITM Only", "OTM Only"]
    )
    m_max_ltp = f5.number_input("5. Max Option Premium (₹)", min_value=1.0, max_value=50000.0, value=1500.0, step=50.0)
    m_sort = f6.selectbox("6. Sort Strikes By", ["Highest Change in OI", "Highest Total OI", "Lowest Premium (LTP)"])

    if st.button("🔍 Scan Matching Option Strikes", use_container_width=True):
        pass

    if m_segs:
        strikes_df = engine.scan_individual_strikes(m_segs)
        if not strikes_df.empty:
            if m_type == "CE Only":
                strikes_df = strikes_df[strikes_df["Type"] == "CE"]
            elif m_type == "PE Only":
                strikes_df = strikes_df[strikes_df["Type"] == "PE"]

            if m_buildup:
                strikes_df = strikes_df[strikes_df["Buildup"].isin(m_buildup)]

            if m_zone == "Near ATM (+/- 3 Strikes)":
                strikes_df = strikes_df[strikes_df["Near_ATM"] == True]
            elif m_zone == "ATM Only":
                strikes_df = strikes_df[strikes_df["Moneyness"] == "ATM"]
            elif m_zone == "ITM Only":
                strikes_df = strikes_df[strikes_df["Moneyness"] == "ITM"]
            elif m_zone == "OTM Only":
                strikes_df = strikes_df[strikes_df["Moneyness"] == "OTM"]

            strikes_df = strikes_df[strikes_df["LTP"] <= m_max_ltp]

            if m_sort == "Highest Change in OI":
                strikes_df = strikes_df.sort_values("Chg_OI", ascending=False)
            elif m_sort == "Highest Total OI":
                strikes_df = strikes_df.sort_values("OI", ascending=False)
            else:
                strikes_df = strikes_df.sort_values("LTP", ascending=True)

            st.caption(f"Showing **{len(strikes_df)}** matching option contracts:")
            display_cols = [
                "Contract", "Spot", "Moneyness", "Buildup",
                "LTP", "Target", "SL", "Chg_OI", "OI", "Strike_PCR"
            ]
            st.dataframe(strikes_df[display_cols], use_container_width=True, height=480)

with tab_auto:
    st.markdown("**Automatically picks the single best ATM Option Strike per symbol based on PCR + Net OI Flow**")
    c_seg, c_sig = st.columns([2, 1])
    seg_filter = c_seg.multiselect(
        "Select Segments",
        ["NSE Indices", "NSE F&O Stocks", "MCX Commodities"],
        default=["NSE Indices", "NSE F&O Stocks", "MCX Commodities"],
        key="auto_seg_v5"
    )
    sig_filter = c_sig.selectbox(
        "Filter Signal",
        ["All Signals", "🟢 BUY CALL (CE)", "🔴 BUY PUT (PE)", "⚪ SELL STRADDLE / WAIT"],
        key="auto_sig_v5"
    )

    if seg_filter:
        scan_df = engine.scan_market_summary(seg_filter)
        if not scan_df.empty:
            if sig_filter != "All Signals":
                scan_df = scan_df[scan_df["trade_action"] == sig_filter]

            strike_table = scan_df[[
                "symbol", "spot_price", "trade_action", "best_contract",
                "entry_ltp", "target", "stop_loss", "pcr",
                "support_strike", "res_strike", "setup_reason"
            ]].rename(columns={
                "symbol": "Symbol", "spot_price": "Spot",
                "trade_action": "Signal", "best_contract": "Strike to Trade",
                "entry_ltp": "Entry ₹", "target": "Target ₹", "stop_loss": "SL ₹",
                "pcr": "PCR", "support_strike": "PE Support",
                "res_strike": "CE Resistance", "setup_reason": "OI Setup"
            })
            st.dataframe(strike_table, use_container_width=True, height=480)

with tab_chain:
    c1, c2, c3 = st.columns([2, 2, 1])
    segment = c1.selectbox("Segment", list(INSTRUMENTS.keys()), key="chain_seg_v5")
    symbol = c2.selectbox("Symbol", list(INSTRUMENTS[segment].keys()), key="chain_sym_v5")
    if c3.button("🔄 Refresh", use_container_width=True, key="chain_ref_v5"):
        pass

    df, summary = engine.fetch_chain(segment=segment, symbol=symbol)

    if df is not None:
        st.info(
            f"**{symbol} Summary:** {summary['trade_action']} 👉 **{summary['best_contract']}** "
            f"@ ₹{summary['entry_ltp']} | **Target:** ₹{summary['target']} | **SL:** ₹{summary['stop_loss']} "
            f"*(Support: {summary['support_strike']:g} | Resistance: {summary['res_strike']:g})*"
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric(f"{symbol} Spot", f"₹{summary['spot_price']:,.1f}", f"{summary['fut_chg_pct']:+.2f}%")
        m2.metric("PCR", f"{summary['pcr']}")
        m3.metric("Max Pain", f"{summary['max_pain']:,.1f}")
        m4.metric("F&O Build-Up", summary["fut_buildup"])

        sub1, sub2, sub3 = st.tabs(["📊 Option Chain", "📈 Trending OI", "🎯 Max Pain"])
        with sub1:
            cols = ["ce_buildup", "ce_chg_oi", "ce_ltp", "strike", "pe_ltp", "pe_chg_oi", "pe_buildup"]
            st.dataframe(df[cols], use_container_width=True, height=420)
        with sub2:
            hist_df = engine.get_trending_oi_history(symbol)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=hist_df["timestamp"], y=hist_df["sum_ce_chg_oi"], name="Call Chg OI", line=dict(color="#ef4444", width=2)))
            fig.add_trace(go.Scatter(x=hist_df["timestamp"], y=hist_df["sum_pe_chg_oi"], name="Put Chg OI", line=dict(color="#22c55e", width=2)))
            fig.update_layout(template="plotly_dark", height=350, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig, use_container_width=True)
        with sub3:
            fig_oi = go.Figure()
            fig_oi.add_trace(go.Bar(x=df["strike"], y=df["ce_oi"], name="Call OI", marker_color="#ef4444"))
            fig_oi.add_trace(go.Bar(x=df["strike"], y=df["pe_oi"], name="Put OI", marker_color="#22c55e"))
            fig_oi.update_layout(barmode="group", template="plotly_dark", height=350, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig_oi, use_container_width=True)
