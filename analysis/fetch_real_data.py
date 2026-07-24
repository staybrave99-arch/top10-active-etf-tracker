"""Pulls real data out of Postgres into the shape indicators.build_report
expects, instead of the synthetic generator. Run via the same DATABASE_URL
env var the production scraper uses (e.g. through `flyctl proxy`).
"""

import json

import pandas as pd
import psycopg2


def fetch(conn_str):
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

    prices = pd.read_sql(
        "SELECT stock_code, trade_date, price AS close FROM stock_price",
        conn,
    )
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["close"] = prices["close"].astype(float)

    stock_names = pd.read_sql(
        "SELECT DISTINCT ON (stock_code) stock_code, stock_name FROM etf_holding WHERE stock_name IS NOT NULL",
        conn,
    )
    names = dict(zip(stock_names["stock_code"], stock_names["stock_name"]))

    conn.close()
    return holdings, etf_aum, prices, names


if __name__ == "__main__":
    import os

    conn_str = os.environ["DATABASE_URL"]
    holdings, etf_aum, prices, names = fetch(conn_str)
    print("holdings:", holdings.shape)
    print("etf_aum:", etf_aum.shape)
    print("prices:", prices.shape)
    print("names:", len(names))
    holdings.to_csv("real_holdings.csv", index=False)
    etf_aum.to_csv("real_aum.csv", index=False)
    prices.to_csv("real_prices.csv", index=False)
    with open("real_stock_names.json", "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=2)
