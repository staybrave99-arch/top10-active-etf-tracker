import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

print("=== each ticker's own latest snapshot ===")
cur.execute("""
    SELECT DISTINCT ON (s.ticker) s.ticker, s.data_date, s.id
    FROM etf_snapshot s
    ORDER BY s.ticker, s.data_date DESC
""")
latest_snapshots = cur.fetchall()
for row in latest_snapshots:
    print(row)

print()
print("=== stock_code format sanity check (repr, to catch whitespace/leading-zero drift) ===")
cur.execute("""
    SELECT DISTINCT s.ticker, h.stock_code
    FROM etf_holding h
    JOIN etf_snapshot s ON s.id = h.snapshot_id
    WHERE s.id = ANY(%s)
    ORDER BY h.stock_code, s.ticker
""", ([row[2] for row in latest_snapshots],))
rows = cur.fetchall()
for ticker, code in rows[:20]:
    print(ticker, repr(code), len(code))

print()
print("=== per-ticker holding count (own latest snapshot) ===")
from collections import defaultdict
by_code = defaultdict(set)
by_ticker_count = defaultdict(int)
for ticker, code in rows:
    by_code[code].add(ticker)
    by_ticker_count[ticker] += 1
for ticker, n in sorted(by_ticker_count.items()):
    print(ticker, n)

print()
print("=== stock_code overlap across ETFs, using EACH ticker's own latest snapshot ===")
overlap = {code: tickers for code, tickers in by_code.items() if len(tickers) >= 2}
if not overlap:
    print("(none -- zero stocks are commonly held by 2+ ETFs right now)")
for code, tickers in sorted(overlap.items(), key=lambda kv: -len(kv[1])):
    print(code, sorted(tickers))

print()
print("=== sample known large-cap codes (2330/2454/2317) presence per ticker ===")
for code in ("2330", "2454", "2317", "2308"):
    holders = by_code.get(code, set())
    print(code, "->", sorted(holders) if holders else "not held by anyone")

conn.close()
