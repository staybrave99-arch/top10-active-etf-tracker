"""One-off: delete the corrupted etf_snapshot rows found for 00982A/00992A
where data_date (2026-09-07) landed in the future relative to when the
scrape actually ran (2026-09-04 evening / 2026-09-05 just after midnight
Taipei) -- a capitalfund.com.tw PCF date2 field caught mid-refresh,
before the site had settled to its real basis date. capitalfund.py now
rejects a future-dated data_date going forward; this just removes the
two already-corrupted rows (etf_holding cascades via FK) so they stop
throwing off variation_rate/price joins. The correct data for that
trading day will reappear on capitalfund.py's own next scrape.
"""
import os
from datetime import date

import psycopg2


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    conn = psycopg2.connect(conn_str)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker, data_date FROM etf_snapshot "
            "WHERE ticker IN ('00982A', '00992A') AND data_date > %s",
            (date.today(),),
        )
        rows = cur.fetchall()
        print(f"found {len(rows)} future-dated row(s): {rows}")
        if rows:
            ids = [r[0] for r in rows]
            cur.execute("DELETE FROM etf_snapshot WHERE id = ANY(%s)", (ids,))
            print(f"deleted {cur.rowcount} row(s) (etf_holding cascades)")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
