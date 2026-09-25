"""Throwaway diagnostic: how complete is each ETF's daily reporting, and
what's really going on with the stocks that keep getting tagged "出清" in
the top-movers report (2881, 3653, 8358, 2303, ...).
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
    snaps["data_date"] = pd.to_datetime(snaps["data_date"])
    all_dates = sorted(snaps["data_date"].unique())
    print(f"total distinct report dates across all ETFs: {len(all_dates)}")
    print(f"date range: {all_dates[0].date()} .. {all_dates[-1].date()}")
    print()

    print("=== per-ETF reporting completeness (vs. union of all report dates) ===")
    rows = []
    for ticker, g in snaps.groupby("ticker"):
        name = g["etf_name"].iloc[0]
        have = set(g["data_date"])
        missing = [d for d in all_dates if d not in have]
        recent = all_dates[-20:]
        missing_recent = [d for d in recent if d not in have]
        rows.append(
            {
                "ticker": ticker,
                "name": name,
                "reported": len(have),
                "of": len(all_dates),
                "missing_pct": round(100 * len(missing) / len(all_dates), 1),
                "missing_recent20": len(missing_recent),
                "recent_missing_dates": ",".join(d.strftime("%m-%d") for d in missing_recent),
            }
        )
    result = pd.DataFrame(rows).sort_values("missing_pct", ascending=False)
    print(result.to_string(index=False))
    print()

    print("=== per (ticker, stock_code) rows for the flagged codes, last 20 report dates ===")
    codes = ["2881", "3653", "8358", "2303"]
    holdings = pd.read_sql(
        """
        SELECT s.ticker, s.data_date AS trade_date, h.stock_code, h.stock_name, h.shares, h.weight_pct
        FROM etf_holding h
        JOIN etf_snapshot s ON s.id = h.snapshot_id
        WHERE h.stock_code = ANY(%(codes)s)
        ORDER BY h.stock_code, s.ticker, s.data_date
        """,
        conn,
        params={"codes": codes},
    )
    holdings["trade_date"] = pd.to_datetime(holdings["trade_date"])
    conn.close()

    recent_cutoff = all_dates[-20]
    for code in codes:
        sub = holdings[(holdings["stock_code"] == code) & (holdings["trade_date"] >= recent_cutoff)]
        name = sub["stock_name"].iloc[0] if not sub.empty else "?"
        print(f"--- {code} {name}: held by tickers {sorted(sub['ticker'].unique())} ---")
        pivot = sub.pivot_table(index="trade_date", columns="ticker", values="shares", aggfunc="first")
        pivot = pivot.reindex(all_dates[-20:])
        print(pivot.to_string())
        print()


if __name__ == "__main__":
    main()
