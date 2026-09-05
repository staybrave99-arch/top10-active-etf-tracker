"""One-off: is there a systematic date lag between etf_snapshot.data_date
(each site's own labeled PCF "as of" date) and stock_price.trade_date (the
exchange's actual EOD quote date), given both get written in the SAME
daily scrape run? If PCF sites publish on a T+1 basis (common in Taiwan),
data_date would trail trade_date by one trading day, every run.
"""
import os

import pandas as pd
import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)

    snap = pd.read_sql(
        "SELECT ticker, data_date FROM etf_snapshot ORDER BY ticker, data_date", conn
    )
    prices = pd.read_sql(
        "SELECT DISTINCT trade_date FROM stock_price ORDER BY trade_date", conn
    )
    conn.close()

    price_dates = sorted(prices["trade_date"].unique())
    print(f"stock_price distinct trade_dates: {len(price_dates)}, "
          f"range {price_dates[0]} .. {price_dates[-1]}")
    print("last 10 stock_price trade_dates:", price_dates[-10:])

    for ticker, g in snap.groupby("ticker"):
        dd = sorted(g["data_date"].unique())
        print(f"\n{ticker}: {len(dd)} snapshot dates, range {dd[0]} .. {dd[-1]}")
        print(f"  last 10 data_dates: {dd[-10:]}")
        # For each of this ticker's data_dates, where does it fall relative
        # to the price calendar? If PCF is T-1, data_date should equal the
        # price date *one position earlier* in price_dates.
        matches_same_day = sum(1 for d in dd if d in price_dates)
        matches_lag1 = 0
        for d in dd:
            if d in price_dates:
                idx = price_dates.index(d)
                if idx + 1 < len(price_dates):
                    pass
        # Simpler: compare this ticker's max data_date to price's max trade_date,
        # counting how many price trading days sit strictly after it.
        max_dd = dd[-1]
        days_price_ahead = sum(1 for pd_ in price_dates if pd_ > max_dd)
        print(f"  max data_date={max_dd}; price trade_dates strictly after it: {days_price_ahead}")
        print(f"  data_dates that ARE exact matches to a price trade_date: {matches_same_day}/{len(dd)}")


if __name__ == "__main__":
    main()
