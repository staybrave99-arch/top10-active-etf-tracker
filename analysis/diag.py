import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import psycopg2

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

print("=== every (date, stock) with breadth >= 2 (n_buy + n_sell), most recent 10 dates ===")
recent_dates = sorted(report_with_bias["trade_date"].unique())[-10:]
for d in recent_dates:
    day = report_with_bias[report_with_bias["trade_date"] == d]
    eligible = day[(day["n_buy"] + day["n_sell"]) >= 2]
    same_dir = eligible[(eligible["n_buy"] >= 2) | (eligible["n_sell"] >= 2)]
    print(f"--- {d.date()}: eligible={len(eligible)} same_direction={len(same_dir)} ---")
    for _, r in eligible.iterrows():
        name = stock_names.get(str(r["stock_code"]), "")
        tag = "SAME-DIR" if (r["n_buy"] >= 2 or r["n_sell"] >= 2) else "mixed"
        print(
            f"  {r['stock_code']} {name} buy={r['n_buy']} sell={r['n_sell']} "
            f"net_score={r['net_score']:.3f} bias={r['bias']:.2f} quadrant={r['quadrant']} [{tag}]"
        )
