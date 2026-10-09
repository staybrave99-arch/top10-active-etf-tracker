"""Throwaway diagnostic: full health check of etf_snapshot/etf_holding
after today's fixes (expected_trade_date clamp + duplicate-content
guard). Read-only -- lists suspicious rows for a human to review, does
not delete anything.
"""

import os

import pandas as pd
import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)

    snaps = pd.read_sql(
        "SELECT ticker, etf_name, data_date, scraped_at FROM etf_snapshot ORDER BY ticker, data_date",
        conn,
    )
    snaps["data_date"] = pd.to_datetime(snaps["data_date"])

    print("=== 1) any future-dated snapshot left? ===")
    today = pd.Timestamp.now(tz="Asia/Taipei").normalize().tz_localize(None)
    future = snaps[snaps["data_date"] > today]
    print(future.to_string(index=False) if not future.empty else "none")
    print()

    print("=== 2) per-ETF reporting completeness, last 20 report dates ===")
    all_dates = sorted(snaps["data_date"].unique())
    recent_dates = all_dates[-20:]
    rows = []
    for ticker, g in snaps.groupby("ticker"):
        have = set(g["data_date"])
        missing = [d for d in recent_dates if d not in have]
        rows.append({"ticker": ticker, "reported": len(recent_dates) - len(missing), "of": len(recent_dates),
                     "missing_dates": ",".join(d.strftime("%m-%d") for d in missing)})
    print(pd.DataFrame(rows).sort_values("ticker").to_string(index=False))
    print()

    print("=== 3) duplicate-content check: consecutive snapshots per ticker with IDENTICAL holdings but different data_date ===")
    holdings = pd.read_sql(
        """
        SELECT s.ticker, s.data_date, h.stock_code, h.shares
        FROM etf_holding h
        JOIN etf_snapshot s ON s.id = h.snapshot_id
        ORDER BY s.ticker, s.data_date, h.stock_code
        """,
        conn,
    )
    holdings["data_date"] = pd.to_datetime(holdings["data_date"])

    found_any = False
    for ticker, g in holdings.groupby("ticker"):
        dates = sorted(g["data_date"].unique())
        # build {date: {stock_code: shares}}
        by_date = {}
        for d in dates:
            sub = g[g["data_date"] == d]
            by_date[d] = dict(zip(sub["stock_code"], sub["shares"]))
        for i in range(1, len(dates)):
            prev_d, cur_d = dates[i - 1], dates[i]
            if by_date[prev_d] == by_date[cur_d]:
                found_any = True
                print(f"  {ticker}: {prev_d.date()} and {cur_d.date()} have byte-for-byte identical holdings ({len(by_date[cur_d])} stocks)")
    if not found_any:
        print("none found -- no two consecutive snapshots for any ticker have identical holdings under different dates")
    print()

    print("=== 4) last 5 snapshots per ticker (data_date, scraped_at, holdings count) ===")
    counts = holdings.groupby(["ticker", "data_date"]).size().reset_index(name="n_holdings")
    merged = snaps.merge(counts, on=["ticker", "data_date"], how="left")
    for ticker, g in merged.groupby("ticker"):
        g = g.sort_values("data_date").tail(5)
        print(f"--- {ticker} ---")
        print(g[["data_date", "scraped_at", "n_holdings"]].to_string(index=False))

    conn.close()


if __name__ == "__main__":
    main()
