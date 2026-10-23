import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
import plotly.express as px
from FinMind.data import DataLoader
import os

st.set_page_config(page_title="台美股全籌碼事件量化回測系統", layout="wide")
st.title("🏛️ 台美股【大盤事件 × 全市場 1800 檔籌碼/資券/借券/分點】量化回測平台")

DB_PATH = "tw_quant_master.db"

# ==================== 側邊欄：1. 市場與大盤觸發條件 ====================
st.sidebar.header("🌍 1. 市場與大盤指數設定")
market_mode = st.sidebar.radio("選擇回測模式", [
    "🇹🇼 台股全市場 1800+ 檔 (含法人/資券/借券/分點)", 
    "🇺🇸 美股核心標的與大盤 (Yahoo Finance)"
])

if "🇹🇼" in market_mode:
    index_choice = st.sidebar.selectbox("選擇台股大盤基準", ["加權指數 (^TWII)", "櫃買指數 (^TWOII)"])
    index_ticker = "^TWII" if "TWII" in index_choice else "^TWOII"
else:
    index_choice = st.sidebar.selectbox("選擇美股大盤基準", ["S&P 500 (^GSPC)", "Nasdaq (^IXIC)", "費城半導體 (^SOX)"])
    index_ticker = index_choice.split("(")[1].replace(")", "")

start_year = st.sidebar.slider("回測起始年份", 2008, 2026, 2020)
index_drop_thresh = st.sidebar.slider("大盤單日跌幅觸發門檻 (%)", -8.0, -1.0, -3.0, 0.5)

# ==================== 側邊欄：2. 價量與分群條件 ====================
st.sidebar.header("📈 2. 價量與強弱分群設定")
min_volume_lots = st.sidebar.number_input("當日最低成交量門檻 (張)", min_value=0, value=1000, step=500)
strong_thresh = st.sidebar.slider("【抗跌組】當日漲跌幅 (%) ≥", -2.0, 5.0, 0.0, 0.5)
weak_thresh = st.sidebar.slider("【重挫組】當日漲跌幅 (%) ≤", -9.5, -3.0, -7.0, 0.5)

# ==================== 側邊欄：3. 台股專屬進階籌碼濾網 ====================
st.sidebar.header("🦅 3. 進階籌碼濾網 (法人/資券/借券/分點)")
enable_chip_filter = st.sidebar.checkbox("啟用進階籌碼過濾條件 (套用至重挫跌深組)", value=False)

with st.sidebar.expander("📌 三大法人買賣超條件 (張)", expanded=enable_chip_filter):
    filter_it_buy = st.checkbox("限制【投信】當日買賣超 ≥ (張)", value=False)
    it_min_lots = st.number_input("投信最低買超張數", value=50, step=50)
    filter_fi_buy = st.checkbox("限制【外資】當日買賣超 ≥ (張)", value=False)
    fi_min_lots = st.number_input("外資最低買超張數", value=100, step=100)

with st.sidebar.expander("📌 融資融券斷頭/軋空條件 (張)", expanded=enable_chip_filter):
    filter_margin_drop = st.checkbox("限制【融資】當日減肥 ≤ (張) [抓散戶斷頭]", value=False)
    margin_max_change = st.number_input("融資當日增減上限 (負數代表減少)", value=-200, step=100)
    filter_short_change = st.checkbox("限制【融券】當日增減 ≥ (張)", value=False)
    short_min_change = st.number_input("融券當日增減下限", value=0, step=50)

with st.sidebar.expander("📌 借券買賣與借券餘額條件 (張)", expanded=enable_chip_filter):
    filter_sbl_net = st.checkbox("限制【借券賣出淨增減】≤ (張) [空頭不追空/還券]", value=False)
    sbl_max_net = st.number_input("借券賣出淨增減上限 (負數代表淨還券)", value=0, step=50)
    filter_sbl_bal = st.checkbox("限制【借券賣出總餘額】≤ (張)", value=False)
    sbl_max_bal = st.number_input("借券賣出餘額上限 (張)", value=20000, step=1000)

with st.sidebar.expander("📌 券商分點主力吸籌條件", expanded=enable_chip_filter):
    target_brokers = st.text_input("指定買超主力分點關鍵字 (逗號分隔)", value="摩根士丹利, 凱基-台北, 美林, 高盛")
    min_top5_conc = st.slider("大跌日【前5大買超分點佔成交量比 (%)】≥", 0.0, 50.0, 15.0, 2.5)
    finmind_token = st.text_input("FinMind API Token (即時調閱分點明細用)", type="password")

# ==================== 數據載入與整合函數 ====================
@st.cache_data(ttl=1800)
def get_index_data(idx_ticker, start_yr):
    df = yf.download(idx_ticker, start=f"{start_yr}-01-01", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df["idx_ret"] = df["Close"].pct_change() * 100
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    return df

idx_df = get_index_data(index_ticker, start_year)
event_dates = idx_df[idx_df["idx_ret"] <= index_drop_thresh].index.tolist()

def load_tw_full_db():
    """從本地 SQLite 載入全台股 1800 檔價量與籌碼大表"""
    if not os.path.exists(DB_PATH):
        return None, None
    conn = sqlite3.connect(DB_PATH)
    try:
        stock_info = pd.read_sql("SELECT stock_id, stock_name, industry_category, type FROM stock_info", conn)
        query = """
        SELECT 
            p.date, p.stock_id, p.open, p.close, p.spread, p.volume_lots,
            COALESCE(i.Foreign_Investor, 0) AS fi_net,
            COALESCE(i.Investment_Trust, 0) AS it_net,
            COALESCE(i.Dealer_Total, 0) AS dl_net,
            COALESCE(m.MarginPurchaseTodayBalance, 0) AS margin_bal,
            COALESCE(m.margin_net_change, 0) AS margin_chg,
            COALESCE(m.ShortSaleTodayBalance, 0) AS short_bal,
            COALESCE(m.short_net_change, 0) AS short_chg,
            COALESCE(s.sbl_short_sale_lots, 0) AS sbl_sell,
            COALESCE(s.sbl_returns_lots, 0) AS sbl_ret,
            COALESCE(s.sbl_net_change_lots, 0) AS sbl_net_chg,
            COALESCE(s.sbl_balance_lots, 0) AS sbl_bal
        FROM daily_price p
        LEFT JOIN institutional i ON p.date = i.date AND p.stock_id = i.stock_id
        LEFT JOIN margin_short m ON p.date = m.date AND p.stock_id = m.stock_id
        LEFT JOIN sbl_data s ON p.date = s.date AND p.stock_id = s.stock_id
        """
        df_all = pd.read_sql(query, conn)
        conn.close()
        return df_all, stock_info
    except Exception:
        conn.close()
        return None, None

# ==================== 主畫面三大核心分頁 ====================
tab1, tab2, tab3, tab4 = st.tabs([
    "🔥 分頁一：全市場群組回測 (含籌碼濾網)",
    "📋 分頁二：大跌日 1800 檔選股清單 (法人/資券/借券明細)",
    "🏦 分頁三：券商分點主力抄底雷達",
    "🕷️ 分頁四：資料庫狀態與一鍵更新控制台"
])

df_tw, info_tw = load_tw_full_db()

with tab1:
    st.subheader(f"📊 【{index_choice}】單日大跌 ≤ {index_drop_thresh}% 回測結果")
    
    if "🇹🇼" in market_mode and df_tw is not None and not df_tw.empty:
        # 計算個股單日漲跌幅與未來持有報酬
        df_tw = df_tw.sort_values(["stock_id", "date"])
        df_tw["prev_close"] = df_tw.groupby("stock_id")["close"].shift(1)
        df_tw["day_ret"] = (df_tw["close"] - df_tw["prev_close"]) / df_tw["prev_close"] * 100
        
        # 隔日 (T+1) 開盤價買進
        df_tw["entry_open"] = df_tw.groupby("stock_id")["open"].shift(-1)
        for h in [5, 20, 40]:
            df_tw[f"exit_{h}d"] = df_tw.groupby("stock_id")["close"].shift(-(1 + h))
            df_tw[f"ret_{h}d"] = (df_tw[f"exit_{h}d"] - df_tw["entry_open"]) / df_tw["entry_open"] * 100

        # 篩選大盤大跌事件日且成交量達標的個股
        df_events = df_tw[(df_tw["date"].isin(event_dates)) & (df_tw["volume_lots"] >= min_volume_lots)].copy()

        # 建立抗跌組與重挫組遮罩
        cond_strong = df_events["day_ret"] >= strong_thresh
        cond_weak = df_events["day_ret"] <= weak_thresh

        # 若啟用進階籌碼濾網，套用到重挫跌深組
        if enable_chip_filter:
            if filter_it_buy:
                cond_weak = cond_weak & (df_events["it_net"] >= it_min_lots)
            if filter_fi_buy:
                cond_weak = cond_weak & (df_events["fi_net"] >= fi_min_lots)
            if filter_margin_drop:
                cond_weak = cond_weak & (df_events["margin_chg"] <= margin_max_change)
            if filter_short_change:
                cond_weak = cond_weak & (df_events["short_chg"] >= short_min_change)
            if filter_sbl_net:
                cond_weak = cond_weak & (df_events["sbl_net_chg"] <= sbl_max_net)
            if filter_sbl_bal:
                cond_weak = cond_weak & (df_events["sbl_bal"] <= sbl_max_bal)

        summary_list = []
        for label, h in [("抱 1 週 (5天)", 5), ("抱 1 個月 (20天)", 20), ("抱 2 個月 (40天)", 40)]:
            g_strong = df_events[cond_strong].groupby("date")[f"ret_{h}d"].mean()
            g_weak = df_events[cond_weak].groupby("date")[f"ret_{h}d"].mean()
            comp = pd.concat([g_strong.rename("strong"), g_weak.rename("weak")], axis=1).dropna()
            
            n_days = len(comp)
            weak_win_days = (comp["weak"] > comp["strong"]).sum() if n_days > 0 else 0
            summary_list.append({
                "持有週期": label,
                "有效對決大跌日數": n_days,
                "抗跌組平均報酬 (%)": round(comp["strong"].mean(), 2) if n_days else 0,
                "重挫組(含籌碼濾網)平均報酬 (%)": round(comp["weak"].mean(), 2) if n_days else 0,
                "重挫組超額勝出 (百分點)": round((comp["weak"] - comp["strong"]).mean(), 2) if n_days else 0,
                "重挫組勝率 (%)": round(weak_win_days / n_days * 100, 1) if n_days else 0
            })
        
        st.dataframe(pd.DataFrame(summary_list), use_container_width=True)
    else:
        st.info("💡 本地資料庫尚未建立或目前切換為美股模式。請至【分頁四】點擊一鍵初始化全台股 1,800 檔資料庫，或先執行 `python db_updater.py`。")

with tab2:
    st.subheader("📋 大跌日個股完整籌碼透視表 (成交量 / 三大法人 / 資券 / 借券)")
    if df_tw is not None and not df_tw.empty:
        avail_event_dates = sorted(df_tw[df_tw["date"].isin(event_dates)]["date"].unique(), reverse=True)
        if avail_event_dates:
            sel_dt = st.selectbox("選擇歷史大跌事件日", avail_event_dates)
            day_df = df_tw[(df_tw["date"] == sel_dt) & (df_tw["volume_lots"] >= min_volume_lots)].merge(
                info_tw, on="stock_id", how="left"
            )
            
            group_view = st.radio("篩選檢視群組", [
                f"🟢 重挫跌深組 (≤ {weak_thresh}%，套用左側籌碼濾網)",
                f"🔴 逆勢抗跌組 (≥ {strong_thresh}%)",
                "🌐 當日全市場達標個股"
            ], horizontal=True)
            
            if "重挫" in group_view:
                sub_df = day_df[day_df["day_ret"] <= weak_thresh]
                if enable_chip_filter:
                    if filter_it_buy: sub_df = sub_df[sub_df["it_net"] >= it_min_lots]
                    if filter_fi_buy: sub_df = sub_df[sub_df["fi_net"] >= fi_min_lots]
                    if filter_margin_drop: sub_df = sub_df[sub_df["margin_chg"] <= margin_max_change]
                    if filter_short_change: sub_df = sub_df[sub_df["short_chg"] >= short_min_change]
                    if filter_sbl_net: sub_df = sub_df[sub_df["sbl_net_chg"] <= sbl_max_net]
                    if filter_sbl_bal: sub_df = sub_df[sub_df["sbl_bal"] <= sbl_max_bal]
            elif "抗跌" in group_view:
                sub_df = day_df[day_df["day_ret"] >= strong_thresh]
            else:
                sub_df = day_df

            display_cols = {
                "stock_id": "代碼", "stock_name": "名稱", "industry_category": "產業",
                "close": "收盤價", "day_ret": "當日漲跌(%)", "volume_lots": "成交量(張)",
                "fi_net": "外資買賣超(張)", "it_net": "投信買賣超(張)", "dl_net": "自營商(張)",
                "margin_chg": "融資增減(張)", "margin_bal": "融資餘額(張)",
                "short_chg": "融券增減(張)", "sbl_net_chg": "借券賣出淨增減(張)", "sbl_bal": "借券餘額(張)",
                "ret_5d": "抱1週報酬(%)", "ret_20d": "抱1月報酬(%)"
            }
            avail_cols = [c for c in display_cols.keys() if c in sub_df.columns]
            st.dataframe(
                sub_df[avail_cols].rename(columns=display_cols).sort_values("當日漲跌(%)"),
                use_container_width=True
            )

with tab3:
    st.subheader("🏦 券商分點主力抄底雷達 (大跌日誰在偷接刀？)")
    st.markdown("指定大跌事件日與個股，直接剖析當日 **前 15 大買超/賣超券商分點**、**主力集中度** 與 **指定外資/地緣分點動向**。")
    
    c_stk, c_dt, c_btn = st.columns([2, 2, 1])
    target_stk_id = c_stk.text_input("輸入台股代碼 (4碼)", value="2408")
    target_event_dt = c_dt.text_input("輸入大跌事件日期 (YYYY-MM-DD)", value=event_dates[-1] if event_dates else "2024-08-05")
    
    if c_btn.button("🔍 調閱當日分點明細"):
        dl = DataLoader()
        if finmind_token:
            dl.login_by_token(api_token=finmind_token)
        with st.spinner(f"正在抓取 {target_stk_id} 於 {target_event_dt} 的全台券商分點明細..."):
            df_brk = dl.taiwan_stock_trading_daily_report(
                stock_id=target_stk_id, start_date=target_event_dt, end_date=target_event_dt
            )
            if not df_brk.empty:
                # 依分點彙總 (同一分點不同價位合併)
                brk_sum = df_brk.groupby(["securities_trader_id", "securities_trader"]).agg(
                    buy_shares=("buy", "sum"),
                    sell_shares=("sell", "sum")
                ).reset_index()
                brk_sum["買進(張)"] = (brk_sum["buy_shares"] / 1000).round(1)
                brk_sum["賣出(張)"] = (brk_sum["sell_shares"] / 1000).round(1)
                brk_sum["買賣超(張)"] = (brk_sum["買進(張)"] - brk_sum["賣出(張)"]).round(1)
                
                total_buy_lots = brk_sum["買進(張)"].sum()
                top5_buy_net = brk_sum.sort_values("買賣超(張)", ascending=False).head(5)["買賣超(張)"].sum()
                top5_conc = (top5_buy_net / total_buy_lots * 100) if total_buy_lots > 0 else 0
                
                # 檢查指定關鍵字分點
                kw_list = [k.strip() for k in target_brokers.split(",") if k.strip()]
                kw_pattern = "|".join(kw_list)
                matched_brokers = brk_sum[brk_sum["securities_trader"].str.contains(kw_pattern, na=False)]
                
                k1, k2, k3 = st.columns(3)
                k1.metric("前 5 大買超分點合計淨買超", f"{top5_buy_net:,.1f} 張")
                k2.metric("前 5 大買超主力集中度", f"{top5_conc:.2f}%", 
                          delta="達標主力吸籌" if top5_conc >= min_top5_conc else "未達門檻")
                k3.metric("指定關鍵分點淨買賣超合計", f"{matched_brokers['買賣超(張)'].sum():,.1f} 張")
                
                b_left, b_right = st.columns(2)
                with b_left:
                    st.markdown("#### 🟢 當日買超前 15 大主力分點 (偷接刀主力)")
                    st.dataframe(
                        brk_sum.sort_values("買賣超(張)", ascending=False).head(15)[
                            ["securities_trader_id", "securities_trader", "買進(張)", "賣出(張)", "買賣超(張)"]
                        ], use_container_width=True
                    )
                with b_right:
                    st.markdown("#### 🔴 當日賣超前 15 大主力分點 (恐慌殺出分點)")
                    st.dataframe(
                        brk_sum.sort_values("買賣超(張)", ascending=True).head(15)[
                            ["securities_trader_id", "securities_trader", "買進(張)", "賣出(張)", "買賣超(張)"]
                        ], use_container_width=True
                    )
            else:
                st.warning("查無該日分點資料，請確認該日是否為台股交易日，或於左側填入免費的 FinMind API Token。")

with tab4:
    st.subheader("🕷️ 全台股 1,800 檔自動爬蟲與本地資料庫管理")
    st.markdown("""
    為確保回測能在 **1 秒內** 完成，系統採用 `SQLite` 本地高速資料庫 (`tw_quant_master.db`)。
    你可以在此直接指定要抓取的『大盤大跌事件日』，一鍵把當天全台股 1,800 檔的 **日K、成交量、三大法人、融資融券、借券買賣與餘額** 全部抓進資料庫！
    """)
    if st.button("🚀 一鍵同步全台股 1,800 檔股票清單與近期大跌事件日數據"):
        from db_updater import TWStockCrawler
        crawler = TWStockCrawler(api_token=finmind_token)
        crawler.update_stock_list()
        progress = st.progress(0)
        # 抓取最近 10 個大盤大跌日及其後續區間
        target_events = event_dates[-5:] if len(event_dates) >= 5 else event_dates
        for idx, dt in enumerate(target_events):
            st.write(f"正在抓取事件日 {dt} 全市場 1,800 檔數據...")
            crawler.fetch_and_save_by_date(dt)
            progress.progress((idx + 1) / len(target_events))
        crawler.deduplicate_tables()
        st.success("✅ 全市場上市櫃清單與五大籌碼資料表同步完成！請重新整理網頁即可開始回測。")
