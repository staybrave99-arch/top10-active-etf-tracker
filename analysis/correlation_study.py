"""One-off study: does a stock's combined cross-ETF shares change rate
correlate with its own price return? Not part of the daily pipeline --
run manually, e.g. via a temporary GH Actions job with the DB proxy
(see this repo's session history for the pattern), or locally with
DATABASE_URL pointed at a `flyctl proxy` tunnel.

Metric definitions:
  variation_rate(stock, date) = (sum of shares across every ETF holding
      it today - sum of shares across every ETF holding it yesterday)
      / sum of shares yesterday
  price_return(stock, date)   = close.pct_change()

Reported per stock, as a *rolling* correlation (not one number for the
whole period) so day-to-day changes in the relationship are visible:
  - same-day:  variation_rate(t) vs price_return(t)
  - lead-1:    variation_rate(t) vs price_return(t+1)  (does today's
    combined ETF flow predict tomorrow's price move?)
Both Pearson and Spearman are computed; Spearman is the primary read
since these series can have the same kind of magnitude outliers seen
in NetScore.
"""

import json
import os
import sys

import pandas as pd
import psycopg2

ROLLING_WINDOW = 10
ROLLING_MIN_PERIODS = 5

# How many recent trading days a price/variation-rate CHART plots (not the
# correlation study itself, which wants the full history). Keeps each
# bar/point wide enough to tap on a phone -- see build_series(window=...).
CHART_WINDOW_DAYS = 48

DEFAULT_STOCKS = {
    "2330": "台積電",
    "2454": "聯發科",
    "2317": "鴻海",
    "2308": "台達電",
    "3711": "日月光投控",
    "2408": "南亞科",
}


def fetch(conn_str, stock_codes):
    conn = psycopg2.connect(conn_str)

    holdings = pd.read_sql(
        """
        SELECT h.stock_code, s.data_date AS trade_date, h.shares
        FROM etf_holding h
        JOIN etf_snapshot s ON s.id = h.snapshot_id
        WHERE h.stock_code = ANY(%(codes)s)
        """,
        conn,
        params={"codes": list(stock_codes)},
    )
    prices = pd.read_sql(
        """
        SELECT stock_code, trade_date, price AS close
        FROM stock_price
        WHERE stock_code = ANY(%(codes)s)
        """,
        conn,
        params={"codes": list(stock_codes)},
    )
    conn.close()

    holdings["trade_date"] = pd.to_datetime(holdings["trade_date"])
    holdings["shares"] = holdings["shares"].astype(float)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["close"] = prices["close"].astype(float)
    return holdings, prices


def build_series(holdings, prices, code, window=None):
    """window: if given, keep only the most recent `window` rows -- rolling
    correlation callers need the full history and leave this at None; chart
    callers pass a window so a stock with months of accumulated history
    doesn't get plotted as dozens of hairline-thin, near-untappable bars.
    """
    h = holdings[holdings["stock_code"] == code]
    combined = h.groupby("trade_date")["shares"].sum().sort_index()
    variation_rate = combined.pct_change()

    p = prices[prices["stock_code"] == code].drop_duplicates("trade_date").set_index("trade_date")["close"].sort_index()
    price_return = p.pct_change()

    df = pd.DataFrame(
        {"close": p, "shares": combined, "variation_rate": variation_rate, "price_return": price_return}
    ).dropna(subset=["variation_rate", "price_return"], how="all")
    df["price_return_lead1"] = df["price_return"].shift(-1)
    if window is not None:
        df = df.tail(window)
    return df


def _rolling_bivariate(s1, s2, method):
    """Rolling correlation between two aligned series, windowed manually
    since pandas' built-in Rolling.corr() only does Pearson. Spearman is
    Pearson-on-ranks, computed with ranks taken *within each window* (not
    globally, which would not be equivalent) -- avoids adding scipy for
    what pandas' own .rank() already does exactly.
    """
    out = []
    for i in range(len(s1)):
        lo = max(0, i - ROLLING_WINDOW + 1)
        a, b = s1.iloc[lo : i + 1], s2.iloc[lo : i + 1]
        mask = a.notna() & b.notna()
        a, b = a[mask], b[mask]
        if len(a) < ROLLING_MIN_PERIODS or a.nunique() < 2 or b.nunique() < 2:
            out.append(float("nan"))
            continue
        if method == "spearman":
            a, b = a.rank(), b.rank()
        out.append(a.corr(b))
    return pd.Series(out, index=s1.index)


def rolling_corr(df):
    out = pd.DataFrame(index=df.index)
    for method in ("pearson", "spearman"):
        out[f"same_day_{method}"] = _rolling_bivariate(df["variation_rate"], df["price_return"], method)
        out[f"lead1_{method}"] = _rolling_bivariate(df["variation_rate"], df["price_return_lead1"], method)
    out["n_variation_days"] = df["variation_rate"].notna().rolling(ROLLING_WINDOW, min_periods=1).sum()
    return out


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    stocks = DEFAULT_STOCKS

    holdings, prices = fetch(conn_str, stocks.keys())
    print(f"holdings rows: {len(holdings)}, price rows: {len(prices)}")

    result = {"window": ROLLING_WINDOW, "min_periods": ROLLING_MIN_PERIODS, "stocks": []}
    for code, name in stocks.items():
        df = build_series(holdings, prices, code)
        corr = rolling_corr(df)
        n_valid = corr[["same_day_spearman"]].dropna().shape[0]
        print(f"{code} {name}: {len(df)} daily rows, {n_valid} rolling-correlation points")

        dates = [d.strftime("%Y-%m-%d") for d in corr.index]

        def col(name_):
            return [None if pd.isna(v) else round(float(v), 3) for v in corr[name_]]

        close = [None if pd.isna(v) else round(float(v), 2) for v in df["close"]]

        result["stocks"].append(
            {
                "stock_code": code,
                "stock_name": name,
                "dates": dates,
                "close": close,
                "same_day_pearson": col("same_day_pearson"),
                "same_day_spearman": col("same_day_spearman"),
                "lead1_pearson": col("lead1_pearson"),
                "lead1_spearman": col("lead1_spearman"),
                "n_variation_days": [int(v) for v in corr["n_variation_days"]],
            }
        )

    out_path = os.path.join(os.path.dirname(__file__), "correlation_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path}")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
