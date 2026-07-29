import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import psycopg2

from generate_report import _quadrant_highlight_lines, QUADRANT_PICKS
from indicators import build_report

conn_str = os.environ["DATABASE_URL"]
conn = psycopg2.connect(conn_str)

holdings = pd.read_sql(
    """
    SELECT s.ticker AS etf_code, h.stock_code, s.data_date AS trade_date,
           h.shares, h.weight_pct AS weight
    FROM etf_holding h
    JOIN etf_snapshot s ON s.id = h.snapshot_id
    """,
    conn,
)
holdings["trade_date"] = pd.to_datetime(holdings["trade_date"])
holdings["shares"] = holdings["shares"].astype(float)
holdings["weight"] = holdings["weight"].astype(float)

etf_aum = pd.read_sql(
    "SELECT ticker AS etf_code, data_date AS trade_date, net_asset AS aum FROM etf_snapshot",
    conn,
)
etf_aum["trade_date"] = pd.to_datetime(etf_aum["trade_date"])
etf_aum["aum"] = etf_aum["aum"].astype(float)

prices = pd.read_sql("SELECT stock_code, trade_date, price AS close FROM stock_price", conn)
prices["trade_date"] = pd.to_datetime(prices["trade_date"])
prices["close"] = prices["close"].astype(float)

stock_names_df = pd.read_sql(
    "SELECT DISTINCT ON (stock_code) stock_code, stock_name FROM etf_holding WHERE stock_name IS NOT NULL",
    conn,
)
stock_names = dict(zip(stock_names_df["stock_code"], stock_names_df["stock_name"]))
conn.close()

report = build_report(holdings, etf_aum, prices)
report_with_bias = report.dropna(subset=["bias"])

latest_date = report_with_bias["trade_date"].max()
print("latest_date used by _quadrant_highlight_lines:", latest_date)

latest = report_with_bias[report_with_bias["trade_date"] == latest_date]
breadth = latest["n_buy"] + latest["n_sell"]
eligible = latest[breadth >= 2]
print("eligible rows (breadth>=2) on that date:")
for _, r in eligible.iterrows():
    print(" ", r["stock_code"], "buy=", r["n_buy"], "sell=", r["n_sell"], "quadrant=", r["quadrant"])

print()
print("=== actual generate_report._quadrant_highlight_lines() output ===")
for line in _quadrant_highlight_lines(report_with_bias, stock_names):
    print(line)
