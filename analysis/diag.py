import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import pandas as pd
import psycopg2

from indicators import build_report, compute_flags_and_streaks

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
conn.close()

print("=== holdings ETF codes present ===")
print(sorted(holdings["etf_code"].unique()))
print("etf_aum rows:", len(etf_aum), "holdings rows:", len(holdings), "prices rows:", len(prices))

for label, h in [("ALL (incl. 00991A)", holdings), ("EXCLUDING 00991A", holdings[holdings["etf_code"] != "00991A"])]:
    print()
    print(f"=== {label} ===")
    report = build_report(h, etf_aum, prices)
    by_date = report.dropna(subset=["bias"]).groupby("trade_date")
    for d, g in by_date:
        breadth = g["n_buy"] + g["n_sell"]
        n_ge1 = (breadth >= 1).sum()
        n_ge2 = (breadth >= 2).sum()
        max_breadth = breadth.max()
        top = g.loc[breadth.idxmax()] if len(g) else None
        top_desc = f"{top['stock_code']} buy={top['n_buy']} sell={top['n_sell']}" if top is not None else "-"
        print(f"{d.date()}: rows={len(g)} breadth>=1:{n_ge1} breadth>=2:{n_ge2} max_breadth={max_breadth} top={top_desc}")

print()
print("=== raw flag counts (STRICT) per day, all ETFs, before cross-ETF aggregation ===")
scored = compute_flags_and_streaks(holdings, streak_mode="STRICT")
flag_counts = scored.groupby(["trade_date", "flag"]).size().unstack(fill_value=0)
print(flag_counts.tail(15))
