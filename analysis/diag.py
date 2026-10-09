"""Throwaway diagnostic: since the 2026-09-25 fix (23:00 scrape + settled-
date holdback), is each ETF still missing days it should have reported?
"""

import os

import pandas as pd
import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)

    snaps = pd.read_sql(
        "SELECT ticker, etf_name, data_date FROM etf_snapshot ORDER BY ticker, data_date", conn
    )
    conn.close()

    snaps["data_date"] = pd.to_datetime(snaps["data_date"])
    all_dates = sorted(snaps["data_date"].unique())
    print(f"total distinct report dates across all ETFs: {len(all_dates)}")
    print(f"date range: {all_dates[0].date()} .. {all_dates[-1].date()}")
    print()

    # Focus on the window since the fix landed.
    since = pd.Timestamp("2026-09-25")
    recent_dates = [d for d in all_dates if d >= since]
    print(f"=== per-ETF completeness since {since.date()} ({len(recent_dates)} report dates) ===")
    rows = []
    for ticker, g in snaps.groupby("ticker"):
        name = g["etf_name"].iloc[0]
        have = set(g["data_date"])
        missing = [d for d in recent_dates if d not in have]
        rows.append(
            {
                "ticker": ticker,
                "name": name,
                "reported": len(recent_dates) - len(missing),
                "of": len(recent_dates),
                "missing_dates": ",".join(d.strftime("%m-%d") for d in missing),
            }
        )
    result = pd.DataFrame(rows).sort_values("ticker")
    print(result.to_string(index=False))
    print()

    print("=== any trade_date beyond today? ===")
    today = pd.Timestamp.now(tz="Asia/Taipei").normalize().tz_localize(None)
    future = snaps[snaps["data_date"] > today]
    print(future.to_string(index=False) if not future.empty else "none")


if __name__ == "__main__":
    main()
