import os

import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

# 00982A/00992A got a snapshot filed under 2026-07-29 by a manual run this
# morning (09:29 Asia/Taipei), before capitalfund.com.tw's ~21:00 daily
# refresh -- confirmed live at 09:50 that the site was still serving
# 2026-07-28's NAV/holdings at that point, mislabeled by the old date1-based
# parsing. Delete it; tonight's real 22:00 run will insert the correct
# 2026-07-29 snapshot once the site has actually refreshed.
cur.execute(
    """
    DELETE FROM etf_snapshot
    WHERE ticker IN ('00982A', '00992A') AND data_date = '2026-07-29'
    RETURNING ticker, data_date
    """
)
print("deleted:", cur.fetchall())
conn.commit()
conn.close()
