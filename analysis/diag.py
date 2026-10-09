"""Throwaway diagnostic: when and how did another future-dated capitalfund
snapshot (data_date=2026-10-12) get written despite the date1/date2-vs-today
guard in scraper/sites/capitalfund.py?
"""

import os

import pandas as pd
import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)

    future = pd.read_sql(
        "SELECT id, ticker, etf_name, data_date, scraped_at FROM etf_snapshot "
        "WHERE data_date > CURRENT_DATE ORDER BY ticker, data_date",
        conn,
    )
    print("=== future-dated snapshots (data_date > today) ===")
    print(future.to_string(index=False) if not future.empty else "none")
    print()

    # All recent capitalfund (00982A/00992A) snapshots, to see the data_date
    # vs. scraped_at relationship around when the bad one appeared.
    recent = pd.read_sql(
        "SELECT ticker, data_date, scraped_at FROM etf_snapshot "
        "WHERE ticker IN ('00982A','00992A') ORDER BY ticker, data_date DESC LIMIT 20",
        conn,
    )
    print("=== recent 00982A/00992A snapshots (data_date, scraped_at) ===")
    print(recent.to_string(index=False))

    conn.close()


if __name__ == "__main__":
    main()
