import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

print("=== recent etf_snapshot ===")
cur.execute("""
    SELECT ticker, data_date, net_asset
    FROM etf_snapshot
    ORDER BY data_date DESC, ticker
    LIMIT 40
""")
for row in cur.fetchall():
    print(row)

print()
print("=== distinct data_date count per ticker (last 10 days) ===")
cur.execute("""
    SELECT ticker, COUNT(DISTINCT data_date) AS n_dates, MIN(data_date), MAX(data_date)
    FROM etf_snapshot
    WHERE data_date >= (SELECT MAX(data_date) FROM etf_snapshot) - INTERVAL '10 days'
    GROUP BY ticker
    ORDER BY ticker
""")
for row in cur.fetchall():
    print(row)

print()
print("=== share-count changes day over day (last 8 days), per ticker ===")
cur.execute("""
    WITH ranked AS (
        SELECT s.ticker, s.data_date, h.stock_code, h.shares,
               LAG(h.shares) OVER (PARTITION BY s.ticker, h.stock_code ORDER BY s.data_date) AS prev_shares
        FROM etf_holding h
        JOIN etf_snapshot s ON s.id = h.snapshot_id
        WHERE s.data_date >= (SELECT MAX(data_date) FROM etf_snapshot) - INTERVAL '8 days'
    )
    SELECT ticker, data_date,
           COUNT(*) AS n_rows,
           COUNT(*) FILTER (WHERE prev_shares IS NOT NULL AND shares <> prev_shares) AS n_changed,
           COUNT(*) FILTER (WHERE prev_shares IS NOT NULL AND shares = prev_shares) AS n_unchanged
    FROM ranked
    GROUP BY ticker, data_date
    ORDER BY ticker, data_date
""")
for row in cur.fetchall():
    print(row)

print()
print("=== stock_code overlap across ETFs on latest date ===")
cur.execute("""
    SELECT h.stock_code, COUNT(DISTINCT s.ticker) AS n_etfs, STRING_AGG(DISTINCT s.ticker, ',')
    FROM etf_holding h
    JOIN etf_snapshot s ON s.id = h.snapshot_id
    WHERE s.data_date = (SELECT MAX(data_date) FROM etf_snapshot)
    GROUP BY h.stock_code
    HAVING COUNT(DISTINCT s.ticker) >= 2
    ORDER BY n_etfs DESC
    LIMIT 30
""")
for row in cur.fetchall():
    print(row)

print()
print("=== total holding rows per latest snapshot ===")
cur.execute("""
    SELECT s.ticker, s.data_date, COUNT(*) FROM etf_holding h
    JOIN etf_snapshot s ON s.id = h.snapshot_id
    WHERE s.data_date = (SELECT MAX(data_date) FROM etf_snapshot)
    GROUP BY s.ticker, s.data_date
    ORDER BY s.ticker
""")
for row in cur.fetchall():
    print(row)

conn.close()
