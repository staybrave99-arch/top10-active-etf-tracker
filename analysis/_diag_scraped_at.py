"""One-off: for the two capitalfund-sourced tickers, show data_date next to
scraped_at (the actual wall-clock time we wrote the row) to figure out
when/why data_date ended up ahead of real calendar time.
"""
import os

import pandas as pd
import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)
    df = pd.read_sql(
        """
        SELECT ticker, data_date, scraped_at
        FROM etf_snapshot
        WHERE ticker IN ('00982A', '00992A')
        ORDER BY ticker, data_date
        """,
        conn,
    )
    conn.close()
    with pd.option_context("display.max_rows", None):
        print(df.to_string())


if __name__ == "__main__":
    main()
