import os

import psycopg2
from psycopg2.extras import execute_values

SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_snapshot (
    id SERIAL PRIMARY KEY,
    ticker TEXT NOT NULL,
    etf_name TEXT NOT NULL,
    data_date DATE NOT NULL,
    net_asset NUMERIC,
    scraped_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, data_date)
);

CREATE TABLE IF NOT EXISTS etf_holding (
    id SERIAL PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES etf_snapshot(id) ON DELETE CASCADE,
    stock_code TEXT NOT NULL,
    stock_name TEXT,
    shares NUMERIC,
    weight_pct NUMERIC,
    price NUMERIC,
    change_pct NUMERIC
);

ALTER TABLE etf_holding ADD COLUMN IF NOT EXISTS price NUMERIC;
ALTER TABLE etf_holding ADD COLUMN IF NOT EXISTS change_pct NUMERIC;

CREATE INDEX IF NOT EXISTS idx_etf_holding_snapshot_id ON etf_holding (snapshot_id);

-- Independent daily price history per stock, decoupled from etf_holding
-- so it can be backfilled (and keeps accumulating) even for dates where
-- we don't have a matching ETF holdings snapshot.
CREATE TABLE IF NOT EXISTS stock_price (
    stock_code TEXT NOT NULL,
    trade_date DATE NOT NULL,
    price NUMERIC,
    change_pct NUMERIC,
    PRIMARY KEY (stock_code, trade_date)
);
"""


def get_conn():
    dsn = os.environ["DATABASE_URL"]
    return psycopg2.connect(dsn)


def init_schema(conn):
    with conn.cursor() as cur:
        cur.execute(SCHEMA)
    conn.commit()


def save_etf_snapshot(conn, ticker, etf_name, data_date, net_asset, holdings):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO etf_snapshot (ticker, etf_name, data_date, net_asset)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (ticker, data_date) DO UPDATE
                SET net_asset = EXCLUDED.net_asset,
                    etf_name = EXCLUDED.etf_name,
                    scraped_at = now()
            RETURNING id
            """,
            (ticker, etf_name, data_date, net_asset),
        )
        snapshot_id = cur.fetchone()[0]

        cur.execute("DELETE FROM etf_holding WHERE snapshot_id = %s", (snapshot_id,))

        if holdings:
            execute_values(
                cur,
                """
                INSERT INTO etf_holding
                    (snapshot_id, stock_code, stock_name, shares, weight_pct, price, change_pct)
                VALUES %s
                """,
                [
                    (
                        snapshot_id,
                        h["stock_code"],
                        h["stock_name"],
                        h["shares"],
                        h["weight_pct"],
                        h.get("price"),
                        h.get("change_pct"),
                    )
                    for h in holdings
                ],
            )
    conn.commit()
    return snapshot_id


def get_latest_snapshot_holdings(conn, ticker):
    """Most recent snapshot on file for this ticker, as {stock_code: shares}
    plus its data_date -- lets a caller tell whether a newly-scraped
    future-dated payload actually carries new content, or whether the
    site has simply advanced its displayed date label early while the
    PCF underneath is still the same (observed for capitalfund.com.tw
    past midnight: shares unchanged, but date1/date2 already pointing at
    the next trading day). Returns None if this ticker has no snapshot
    on file yet.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, data_date FROM etf_snapshot WHERE ticker = %s ORDER BY data_date DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        snapshot_id, data_date = row

        cur.execute("SELECT stock_code, shares FROM etf_holding WHERE snapshot_id = %s", (snapshot_id,))
        holdings = {code: shares for code, shares in cur.fetchall()}

    return {"data_date": data_date, "holdings": holdings}


def save_stock_prices(conn, trade_date, rows):
    """rows: iterable of (stock_code, price, change_pct)."""
    rows = list(rows)
    if not rows:
        return
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO stock_price (stock_code, trade_date, price, change_pct)
            VALUES %s
            ON CONFLICT (stock_code, trade_date) DO UPDATE
                SET price = EXCLUDED.price,
                    change_pct = EXCLUDED.change_pct
            """,
            [(code, trade_date, price, change_pct) for code, price, change_pct in rows],
        )
    conn.commit()
