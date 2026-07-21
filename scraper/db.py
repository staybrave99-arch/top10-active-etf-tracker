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
    weight_pct NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_etf_holding_snapshot_id ON etf_holding (snapshot_id);
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
                INSERT INTO etf_holding (snapshot_id, stock_code, stock_name, shares, weight_pct)
                VALUES %s
                """,
                [
                    (snapshot_id, h["stock_code"], h["stock_name"], h["shares"], h["weight_pct"])
                    for h in holdings
                ],
            )
    conn.commit()
    return snapshot_id
