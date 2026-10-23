import sqlite3
import pandas as pd
from FinMind.data import DataLoader
from datetime import datetime, timedelta
import time

DB_PATH = "tw_quant_master.db"

class TWStockCrawler:
    def __init__(self, api_token=""):
        self.dl = DataLoader()
        if api_token:
            self.dl.login_by_token(api_token=api_token)
        self.conn = sqlite3.connect(DB_PATH)

    def update_stock_list(self):
        """1. 抓取全台股上市櫃股票清單 (排除權證與ETN，保留4碼普通股與ETF)"""
        print("🔄 正在更新全台股上市櫃股票清單...")
        df_info = self.dl.taiwan_stock_info()
        # 篩選上市(twse)與上櫃(tpex)，且代碼為4位數普通股
        df_stocks = df_info[
            (df_info["type"].isin(["twse", "tpex"])) & 
            (df_info["stock_id"].str.len() == 4) &
            (df_info["stock_id"].str.isdigit())
        ].drop_duplicates(subset=["stock_id"])
        
        df_stocks.to_sql("stock_info", self.conn, if_exists="replace", index=False)
        print(f"✅ 共計收錄 {len(df_stocks)} 檔上市櫃股票！")
        return df_stocks

    def fetch_and_save_by_date(self, target_date: str):
        """
        2. 依『單日全市場』批次抓取五大數據 (比一檔一檔抓快 100 倍！)
        包含：日K成交量、三大法人、融資融券、借券買賣與借券餘額
        """
        print(f"📥 正在抓取 {target_date} 全市場價量與籌碼數據...")
        
        # (A) 全市場日K與成交量 (Trading_Volume 單位為股，除以1000轉為『張』)
        df_price = self.dl.taiwan_stock_daily(start_date=target_date, end_date=target_date)
        if not df_price.empty:
            df_price["volume_lots"] = df_price["Trading_Volume"] / 1000.0
            df_price[["date", "stock_id", "open", "max", "min", "close", "spread", "volume_lots"]].to_sql(
                "daily_price", self.conn, if_exists="append", index=False
            )

        # (B) 全市場三大法人買賣超 (外資 Foreign_Investor、投信 Investment_Trust、自營商 Dealer)
        df_inst = self.dl.taiwan_stock_institutional_investors(start_date=target_date, end_date=target_date)
        if not df_inst.empty:
            df_inst["net_lots"] = (df_inst["buy"] - df_inst["sell"]) / 1000.0
            df_inst_pivot = df_inst.pivot_table(
                index=["date", "stock_id"], columns="name", values="net_lots", aggfunc="sum"
            ).reset_index().fillna(0)
            # 統一欄位名稱
            for col in ["Foreign_Investor", "Investment_Trust", "Dealer_self", "Dealer_Hedging"]:
                if col not in df_inst_pivot.columns:
                    df_inst_pivot[col] = 0.0
            df_inst_pivot["Dealer_Total"] = df_inst_pivot["Dealer_self"] + df_inst_pivot["Dealer_Hedging"]
            df_inst_pivot[["date", "stock_id", "Foreign_Investor", "Investment_Trust", "Dealer_Total"]].to_sql(
                "institutional", self.conn, if_exists="append", index=False
            )

        # (C) 全市場融資融券 (計算融資增減張數、融券增減張數)
        df_margin = self.dl.taiwan_stock_margin_purchase_short_sale(start_date=target_date, end_date=target_date)
        if not df_margin.empty:
            df_margin["margin_net_change"] = df_margin["MarginPurchaseTodayBalance"] - df_margin["MarginPurchaseYesterdayBalance"]
            df_margin["short_net_change"] = df_margin["ShortSaleTodayBalance"] - df_margin["ShortSaleYesterdayBalance"]
            df_margin[[
                "date", "stock_id", 
                "MarginPurchaseTodayBalance", "margin_net_change",
                "ShortSaleTodayBalance", "short_net_change"
            ]].to_sql("margin_short", self.conn, if_exists="append", index=False)

        # (D) 全市場借券買賣與借券餘額 (taiwan_daily_short_sale_balances)
        df_sbl = self.dl.taiwan_daily_short_sale_balances(start_date=target_date, end_date=target_date)
        if not df_sbl.empty:
            # 單位由股轉為張 (/1000)
            df_sbl["sbl_short_sale_lots"] = df_sbl["SBLShortSalesCurrentDayShortSales"] / 1000.0  # 當日借券賣出(張)
            df_sbl["sbl_returns_lots"] = df_sbl["SBLShortSalesCurrentDayReturns"] / 1000.0        # 當日借券還券(張)
            df_sbl["sbl_net_change_lots"] = df_sbl["sbl_short_sale_lots"] - df_sbl["sbl_returns_lots"] # 借券賣出淨增減(張)
            df_sbl["sbl_balance_lots"] = df_sbl["SBLShortSalesCurrentDayBalance"] / 1000.0        # 借券賣出餘額(張)
            df_sbl[[
                "date", "stock_id", "sbl_short_sale_lots", 
                "sbl_returns_lots", "sbl_net_change_lots", "sbl_balance_lots"
            ]].to_sql("sbl_data", self.conn, if_exists="append", index=False)

    def fetch_broker_branch_data(self, stock_id: str, target_date: str):
        """
        3. 抓取特定個股在特定事件日的『券商分點買賣明細』
        (分點資料量極大，採「事件日觸發精準抓取」寫入本地快取，避免塞爆硬碟)
        """
        df_broker = self.dl.taiwan_stock_trading_daily_report(
            stock_id=stock_id, start_date=target_date, end_date=target_date
        )
        if not df_broker.empty:
            df_broker["net_lots"] = (df_broker["buy"] - df_broker["sell"]) / 1000.0
            df_broker.to_sql("broker_branch", self.conn, if_exists="append", index=False)
        return df_broker

    def deduplicate_tables(self):
        """去除資料庫重複日期紀錄，並建立高速索引"""
        cursor = self.conn.cursor()
        for tbl in ["daily_price", "institutional", "margin_short", "sbl_data"]:
            cursor.execute(f"""
                DELETE FROM {tbl} 
                WHERE rowid NOT IN (
                    SELECT MIN(rowid) FROM {tbl} GROUP BY date, stock_id
                )
            """)
            cursor.execute(f"CREATE INDEX IF NOT EXISTS idx_{tbl}_dt_stk ON {tbl} (date, stock_id)")
        self.conn.commit()
        print("✨ 資料庫去重與高速索引建立完成！")

if __name__ == "__main__":
    # 填入你的 FinMind Token (若留空則使用匿名額度)
    FINMIND_TOKEN = ""
    crawler = TWStockCrawler(api_token=FINMIND_TOKEN)
    crawler.update_stock_list()
    
    # 示範：自動回補最近 30 個交易日的全市場 1800 檔數據 (可自行拉長日期區間)
    today = datetime.today()
    for i in range(30, -1, -1):
        dt_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            crawler.fetch_and_save_by_date(dt_str)
            time.sleep(0.5)
        except Exception as e:
            print(f"⚠️ {dt_str} 非交易日或略過: {e}")
            
    crawler.deduplicate_tables()