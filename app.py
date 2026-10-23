import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.express as px
from datetime import datetime, timedelta
import requests
import urllib3
import time
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

st.set_page_config(page_title="台美股量化回測與官方本地高速籌碼系統", layout="wide")
st.title("📊 台美股【動態期望值回測 × 券商分點三層連動 × 證交所/櫃買官方統計 × 即時資金流】系統")

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# ==================== 1. 核心清單與名稱對照 ====================
DEFAULT_TW_MAP = {
    "2330": "台積電", "2317": "鴻海", "2454": "聯發科", "2308": "台達電",
    "6274": "台燿", "2383": "台光電", "3037": "欣興", "2408": "南亞科",
    "2344": "華邦電", "3006": "晶豪科", "3711": "日月光投控", "6669": "緯穎",
    "2382": "廣達", "3406": "玉晶光", "6209": "今國光", "6278": "台表科",
    "2327": "國巨", "2449": "京元電子", "6531": "愛普*", "3443": "創意",
    "3661": "世芯-KY", "6510": "精測", "3008": "大立光", "6223": "旺矽",
    "3260": "威剛", "2303": "聯電", "2412": "中華電", "2881": "富邦金",
    "2882": "國泰金", "1303": "南亞", "1301": "台塑", "1326": "台化",
    "1519": "華城", "1504": "東元", "2603": "長榮", "2609": "陽明",
    "2002": "中鋼", "1907": "永豐餘", "6446": "藥華藥", "1795": "美時",
    "8021": "尖點", "3374": "精材", "6727": "亞泰金屬", "2368": "金像電", "2313": "華通"
}

@st.cache_data(ttl=86400)
def load_all_tw_stock_dict():
    local_