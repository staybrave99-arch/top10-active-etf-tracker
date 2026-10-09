"""One-off: delete any etf_snapshot row with data_date beyond today. A
trade_date can never legitimately be in the future; such a row hijacks
every latest-date pick downstream (compute_screens(), shared_chart_axis()).
ON DELETE CASCADE on etf_holding.snapshot_id removes the associated
holding rows automatically.
"""

import os

import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)
    cur = conn.cursor()

    cur.execute("SELECT id, ticker, data_date FROM etf_snapshot WHERE data_date > CURRENT_DATE ORDER BY data_date")
    rows = cur.fetchall()
    print(f"future-dated snapshots found: {rows}")

    if not rows:
        print("nothing to delete")
        conn.close()
        return

    ids = [r[0] for r in rows]
    cur.execute("DELETE FROM etf_snapshot WHERE id = ANY(%s)", (ids,))
    print(f"deleted {cur.rowcount} etf_snapshot rows (etf_holding rows cascade automatically)")

    conn.commit()
    conn.close()
    print("done")


if __name__ == "__main__":
    main()
