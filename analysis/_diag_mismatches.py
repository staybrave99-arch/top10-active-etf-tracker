"""One-off: for one of the non-capitalfund tickers, show exactly which
data_dates don't match any stock_price trade_date, and whether any of
them are in the future relative to today -- same class of bug as the
capitalfund date2/date1 issue, or just benign holiday/weekend noise?
"""
import os
from datetime import date

import pandas as pd
import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)
    snap = pd.read_sql("SELECT ticker, data_date FROM etf_snapshot ORDER BY ticker, data_date", conn)
    prices = pd.read_sql("SELECT DISTINCT trade_date FROM stock_price ORDER BY trade_date", conn)
    conn.close()

    price_dates = set(prices["trade_date"].unique())
    today = date.today()

    for ticker, g in snap.groupby("ticker"):
        dd = sorted(g["data_date"].unique())
        mismatches = [d for d in dd if d not in price_dates]
        future = [d for d in dd if d > today]
        if mismatches or future:
            print(f"{ticker}: {len(mismatches)} mismatches, {len(future)} future dates")
            print(f"  mismatches: {mismatches}")
            if future:
                print(f"  FUTURE (today={today}): {future}")


if __name__ == "__main__":
    main()
