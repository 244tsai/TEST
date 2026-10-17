import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

# 網頁基本設定
st.set_page_config(page_title="台美股事件驅動量化回測平台", layout="wide")
st.title("📊 台美股【大盤事件 × 個股表現】量化回測網站")

# ==================== 1. 預設市場與個股池設定 ====================
MARKET_CONFIG = {
    "🇹🇼 台股市場 (TW)": {
        "indices": {
            "加權指數 (^TWII)": "^TWII",
            "櫃買指數 (^TWOII)": "^TWOII"
        },
        "default_stocks": [
            "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "3711.TW", 
            "2408.TW", "2344.TW", "3006.TW", "2383.TW", "6274.TWO", "6669.TW",
            "3406.TW", "3037.TW", "2327.TW", "2412.TW", "2881.TW", "2882.TW",
            "2603.TW", "1303.TW", "3661.TW", "3443.TW", "6531.TW"
        ],
        "stock_names": {
            "2330.TW": "台積電", "2317.TW": "鴻海", "2454.TW": "聯發科", "2308.TW": "台達電",
            "2382.TW": "廣達", "3711.TW": "日月光投控", "2408.TW": "南亞科", "2344.TW": "華邦電",
            "3006.TW": "晶豪科", "2383.TW": "台光電", "6274.TWO": "台燿", "6669.TW": "緯穎",
            "3406.TW": "玉晶光", "3037.TW": "欣興", "2327.TW": "國巨", "2412.TW": "中華電",
            "2881.TW": "富邦金", "2882.TW": "國泰金", "2603.TW": "長榮", "1303.TW": "南亞",
            "3661.TW": "世芯-KY", "3443.TW": "創意", "6531.TW": "愛普*"
        }
    },
    "🇺🇸 美股市場 (US)": {
        "indices": {
            "S&P 500 (^GSPC)": "^GSPC",
            "Nasdaq 綜合指數 (^IXIC)": "^IXIC",
            "費城半導體指數 (^SOX)": "^SOX"
        },
        "default_stocks": [
            "NVDA", "TSM", "MU", "AMD", "AVGO", "AAPL", "MSFT", "GOOGL", 
            "META", "AMZN", "TSLA", "INTC", "QCOM", "AMAT", "LRCX", "SMCI",
            "ARM", "MRVL", "KLAC", "JPM", "BRK-B", "LLY", "COST"
        ],
        "stock_names": {}
    }
}

# ==================== 2. 側邊欄：參數控制台 ====================
st.sidebar.header("⚙️ 1. 市場與大盤設定")
market_choice = st.sidebar.radio("選擇市場", list(MARKET_CONFIG.keys()))
m_cfg = MARKET_CONFIG[market_choice]

index_label = st.sidebar.selectbox("選擇大盤指數", list(m_cfg["indices"].keys()))
index_ticker = m_cfg["indices"][index_label]

start_year = st.sidebar.slider("回測起始年份", 2008, 2025, 2008)
index_drop_thresh = st.sidebar.slider("大盤單日跌幅觸發門檻 (%)", -8.0, -1.0, -3.0, 0.5)

st.sidebar.header("⚙️ 2. 個股分群與持有設定")
strong_thresh = st.sidebar.slider("【抗跌組】當日漲跌幅門檻 (%) ≥", -2.0, 5.0, 0.0, 0.5)
weak_thresh = st.sidebar.slider("【重挫組】當日漲跌幅門檻 (%) ≤", -9.5, -3.0, -7.0, 0.5)

custom_tickers_str = st.sidebar.text_area(
    "個股池清單 (可自行新增代碼，逗號分隔)",
    value=", ".join(m_cfg["default_stocks"]),
    height=110
)
stock_tickers = [t.strip().upper() for t in custom_tickers_str.split(",") if t.strip()]

# ==================== 3. 數據抓取與快取引擎 ====================
@st.cache_data(ttl=3600, show_spinner="正在從 Yahoo Finance 下載大盤與個股歷史日 K 資料...")
def load_market_data(idx_ticker, stk_tickers, start_yr):
    all_tickers = list(set([idx_ticker] + stk_tickers))
    start_date = f"{start_yr}-01-01"
    raw = yf.download(all_tickers, start=start_date, auto_adjust=True, progress=False)
    
    if isinstance(raw.columns, pd.MultiIndex):
        close_df = raw["Close"].copy()
        open_df = raw["Open"].copy()
    else:
        close_df = pd.DataFrame({all_tickers[0]: raw["Close"]})
        open_df = pd.DataFrame({all_tickers[0]: raw["Open"]})
        
    # 以大盤有開盤的交易日為基準對齊
    valid_idx = close_df[idx_ticker].dropna().index
    close_df = close_df.loc[valid_idx]
    open_df = open_df.loc[valid_idx]
    return close_df, open_df

close_df, open_df = load_market_data(index_ticker, stock_tickers, start_year)
available_stocks = [s for s in stock_tickers if s in close_df.columns]

def format_stock_name(ticker):
    cn_name = m_cfg["stock_names"].get(ticker, "")
    return f"{ticker} ({cn_name})" if cn_name else ticker

# ==================== 4. 向量化回測核心運算 ====================
idx_ret = close_df[index_ticker].pct_change() * 100
stk_ret = close_df[available_stocks].pct_change() * 100

# 篩選大盤大跌事件日
event_dates = idx_ret[idx_ret <= index_drop_thresh].index

def run_group_backtest(hold_days):
    # 隔日 (T+1) 開盤買進，持有 hold_days 個交易日收盤賣出
    entry_price = open_df[available_stocks].shift(-1)
    exit_price = close_df[available_stocks].shift(-(1 + hold_days))
    fwd_ret = (exit_price - entry_price) / entry_price * 100

    records = []
    for dt in event_dates:
        day_stk_ret = stk_ret.loc[dt].dropna()
        day_fwd_ret = fwd_ret.loc[dt].dropna()
        common = day_stk_ret.index.intersection(day_fwd_ret.index)
        
        if len(common) == 0:
            continue
            
        s_ret = day_stk_ret[common]
        f_ret = day_fwd_ret[common]
        
        strong_stocks = s_ret[s_ret >= strong_thresh].index.tolist()
        weak_stocks = s_ret[s_ret <= weak_thresh].index.tolist()
        
        strong_avg = f_ret[strong_stocks].mean() if len(strong_stocks) > 0 else np.nan
        weak_avg = f_ret[weak_stocks].mean() if len(weak_stocks) > 0 else np.nan
        all_avg = f_ret.mean()
        
        records.append({
            "事件日期": dt.strftime("%Y-%m-%d"),
            "大盤單日跌幅(%)": round(idx_ret.loc[dt], 2),
            "抗跌組檔數": len(strong_stocks),
            "重挫組檔數": len(weak_stocks),
            "抗跌組平均報酬(%)": strong_avg,
            "重挫組平均報酬(%)": weak_avg,
            "全樣本平均報酬(%)": all_avg,
            "抗跌組名單": [format_stock_name(x) for x in strong_stocks],
            "重挫組名單": [format_stock_name(x) for x in weak_stocks]
        })
    return pd.DataFrame(records), fwd_ret

# ==================== 5. 視覺化三大分頁 ====================
tab1, tab2, tab3 = st.tabs([
    "🔥 分頁一：群組回測對決 (FinLab 模式)", 
    "📋 分頁二：歷次大跌日個股名單與績效", 
    "🎯 分頁三：單一個股 × 大盤聯動體檢"
])

with tab1:
    st.subheader(f"當【{index_label}】單日跌幅 ≤ {index_drop_thresh}% 時：追強勢 vs. 撿跌深")
    st.markdown(f"**歷史觸發總天數**：自 {start_year} 年以來共發生 **{len(event_dates)}** 天。")
    
    # 同時比較 1週(5天)、1個月(20天)、2個月(40天)
    horizons = {"抱 1 週 (5交易日)": 5, "抱 1 個月 (20交易日)": 20, "抱 2 個月 (40交易日)": 40}
    summary_rows = []
    
    for label, h_days in horizons.items():
        df_res, _ = run_group_backtest(h_days)
        if not df_res.empty:
            # 篩選兩組同時有股票可比較的日子
            both_valid = df_res.dropna(subset=["抗跌組平均報酬(%)", "重挫組平均報酬(%)"])
            n_compare = len(both_valid)
            weak_wins = (both_valid["重挫組平均報酬(%)"] > both_valid["抗跌組平均報酬(%)"]).sum()
            strong_wins = n_compare - weak_wins
            
            avg_strong = both_valid["抗跌組平均報酬(%)"].mean()
            avg_weak = both_valid["重挫組平均報酬(%)"].mean()
            diff = avg_weak - avg_strong
            
            summary_rows.append({
                "持有週期": label,
                "兩組同時有股可比天數": f"{n_compare} 天",
                "抗跌組勝出天數": f"{strong_wins} 天 ({strong_wins/n_compare*100:.1f}%)" if n_compare else "0",
                "重挫組勝出天數": f"{weak_wins} 天 ({weak_wins/n_compare*100:.1f}%)" if n_compare else "0",
                "抗跌組平均報酬": round(avg_strong, 2) if n_compare else 0,
                "重挫組平均報酬": round(avg_weak, 2) if n_compare else 0,
                "重挫組多賺差距 (百分點)": round(diff, 2) if n_compare else 0
            })
            
    summary_df = pd.DataFrame(summary_rows)
    if not summary_df.empty:
        st.dataframe(summary_df, use_container_width=True)
        
        # 繪製群組報酬對比長條圖
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=summary_df["持有週期"], y=summary_df["抗跌組平均報酬"],
            name=f"逆勢抗跌組 (≥ {strong_thresh}%)", marker_color="#EF553B"
        ))
        fig_bar.add_trace(go.Bar(
            x=summary_df["持有週期"], y=summary_df["重挫組平均報酬"],
            name=f"重挫跌深組 (≤ {weak_thresh}%)", marker_color="#00CC96"
        ))
        fig_bar.update_layout(
            title="不同持有週期下：抗跌組 vs. 重挫組 平均報酬率 (%)",
            barmode="group", yaxis_title="平均報酬率 (%)", template="plotly_dark"
        )
        st.plotly_chart(fig_bar, use_container_width=True)

with tab2:
    st.subheader("📋 歷次大盤大跌日：個股分群名單與後續表現")
    sel_hold = st.selectbox("選擇檢視的持有天數", [5, 20, 40], index=1, format_func=lambda x: f"持有 {x} 個交易日")
    df_detail, fwd_ret_matrix = run_group_backtest(sel_hold)
    
    if not df_detail.empty:
        df_show = df_detail.sort_values("事件日期", ascending=False)
        selected_date = st.selectbox("選擇特定大跌日查看當天個股明細", df_show["事件日期"].tolist())
        
        row_data = df_show[df_show["事件日期"] == selected_date].iloc[0]
        c1, c2, c3 = st.columns(3)
        c1.metric("大盤當日跌幅", f"{row_data['大盤單日跌幅(%)']}%")
        c2.metric(f"抗跌組 ({row_data['抗跌組檔數']}檔) 後續平均", f"{row_data['抗跌組平均報酬(%)']:.2f}%" if pd.notna(row_data['抗跌組平均報酬(%)']) else "無符合個股")
        c3.metric(f"重挫組 ({row_data['重挫組檔數']}檔) 後續平均", f"{row_data['重挫組平均報酬(%)']:.2f}%" if pd.notna(row_data['重挫組平均報酬(%)']) else "無符合個股")
        
        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown(f"#### 🔴 當日逆勢抗跌名單 (≥ {strong_thresh}%)")
            st.write(", ".join(row_data["抗跌組名單"]) if row_data["抗跌組名單"] else "當日無個股符合")
        with col_right:
            st.markdown(f"#### 🟢 當日重挫跌深名單 (≤ {weak_thresh}%)")
            st.write(", ".join(row_data["重挫組名單"]) if row_data["重挫組名單"] else "當日無個股符合")
            
        st.markdown("#### 歷次大跌事件統計總表")
        st.dataframe(df_show.drop(columns=["抗跌組名單", "重挫組名單"]), use_container_width=True)

with tab3:
    st.subheader("🎯 單一個股 × 大盤崩跌日 深度體檢")
    target_stock = st.selectbox("選擇要單獨分析的個股", available_stocks, format_func=format_stock_name)
    ind_hold_days = st.slider("設定買進後持有交易日數", 1, 60, 20)
    
    # 計算該個股在所有大盤大跌日的表現
    idx_entry = open_df[index_ticker].shift(-1)
    idx_exit = close_df[index_ticker].shift(-(1 + ind_hold_days))
    idx_fwd = (idx_exit - idx_entry) / idx_entry * 100
    
    stk_entry = open_df[target_stock].shift(-1)
    stk_exit = close_df[target_stock].shift(-(1 + ind_hold_days))
    stk_fwd = (stk_exit - stk_entry) / stk_entry * 100
    
    ind_records = []
    for dt in event_dates:
        if pd.notna(stk_ret.loc[dt, target_stock]) and pd.notna(stk_fwd.loc[dt]):
            ind_records.append({
                "事件日期": dt.strftime("%Y-%m-%d"),
                "大盤當日跌幅(%)": round(idx_ret.loc[dt], 2),
                "個股當日漲跌(%)": round(stk_ret.loc[dt, target_stock], 2),
                f"個股隔日買抱{ind_hold_days}天報酬(%)": round(stk_fwd.loc[dt], 2),
                f"大盤同期報酬(%)": round(idx_fwd.loc[dt], 2),
                "超額報酬 Alpha(%)": round(stk_fwd.loc[dt] - idx_fwd.loc[dt], 2)
            })
            
    df_ind = pd.DataFrame(ind_records)
    if not df_ind.empty:
        win_rate = (df_ind[f"個股隔日買抱{ind_hold_days}天報酬(%)"] > 0).mean() * 100
        avg_ret = df_ind[f"個股隔日買抱{ind_hold_days}天報酬(%)"].mean()
        avg_alpha = df_ind["超額報酬 Alpha(%)"].mean()
        
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("歷史參與大跌次數", f"{len(df_ind)} 次")
        m2.metric(f"抱 {ind_hold_days} 天正報酬勝率", f"{win_rate:.1f}%")
        m3.metric(f"抱 {ind_hold_days} 天平均報酬", f"{avg_ret:.2f}%")
        m4.metric("平均擊敗大盤 (Alpha)", f"{avg_alpha:.2f}%")
        
        # 散佈圖：個股當日跌幅 vs. 後續反彈幅度
        fig_scatter = px.scatter(
            df_ind, x="個股當日漲跌(%)", y=f"個股隔日買抱{ind_hold_days}天報酬(%)",
            hover_data=["事件日期", "大盤當日跌幅(%)"],
            color="超額報酬 Alpha(%)", color_continuous_scale="RdYlGn",
            title=f"{format_stock_name(target_stock)}：大盤重挫當日漲跌幅 vs. 隔日買進抱 {ind_hold_days} 天報酬率",
            template="plotly_dark"
        )
        fig_scatter.add_hline(y=0, line_dash="dash", line_color="gray")
        fig_scatter.add_vline(x=0, line_dash="dash", line_color="gray")
        st.plotly_chart(fig_scatter, use_container_width=True)
        
        st.dataframe(df_ind.sort_values("事件日期", ascending=False), use_container_width=True)