"""Generates a synthetic multi-day dataset for demoing the indicator
pipeline, since the real database only has 1 real trading day so far
(not enough for streaks or a 20-day BIAS). Anchored to today's actual
scraped holdings/prices/AUM where possible, with ~30 days of synthetic
history walked backwards from there.

This is a throwaway demo generator, not part of the production scraper.
"""

import json
import random
import sys
from datetime import timedelta

import numpy as np
import pandas as pd

sys.path.insert(0, "..")

from scraper.main import DISPATCH, attach_prices, load_etf_list  # noqa: E402
from scraper.prices import fetch_price_lookup  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

random.seed(42)
np.random.seed(42)

N_DAYS = 30


def fetch_today_snapshot(csv_path="../Top10ActiveETF.csv"):
    """Scrape all 8 ETFs live to use as the synthetic dataset's anchor day."""
    rows = load_etf_list(csv_path)
    price_lookup, _price_trade_date = fetch_price_lookup()

    snapshots = []
    holdings = []
    for row in rows:
        ticker = row["代號"].strip()
        url = row["URL"].strip()
        domain = urlparse(url).netloc
        fn = DISPATCH[domain]
        result = fn(ticker=ticker, url=url)
        attach_prices(result["holdings"], price_lookup)
        snapshots.append({"etf_code": ticker, "aum": float(result["net_asset"])})
        for h in result["holdings"]:
            holdings.append(
                {
                    "etf_code": ticker,
                    "stock_code": h["stock_code"],
                    "stock_name": h["stock_name"],
                    "shares": float(h["shares"]) if h["shares"] is not None else None,
                    "weight": float(h["weight_pct"]),
                    "price": float(h["price"]) if h["price"] is not None else None,
                }
            )
    return pd.DataFrame(snapshots), pd.DataFrame(holdings)


def build_trade_dates(n_days, end_date):
    """n_days worth of Mon-Fri dates ending at end_date."""
    dates = []
    d = end_date
    while len(dates) < n_days:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    return sorted(dates)


def walk_backwards(anchor_value, n_days, daily_pct_std, drift_pct=0.0, floor=None):
    """Random walk anchored at the LAST value (today), walking backwards
    so index 0 = earliest day, index -1 = anchor_value exactly.
    """
    vals = [anchor_value]
    v = anchor_value
    for _ in range(n_days - 1):
        shock = np.random.normal(-drift_pct, daily_pct_std)
        v = v / (1 + shock)
        if floor is not None:
            v = max(v, floor)
        vals.append(v)
    vals.reverse()
    return vals


def make_storyline(values, n_days, kind):
    """Overwrite the tail of a random-walk series with a deliberate
    multi-day accumulation (kind='buy') or distribution (kind='sell')
    streak, so the demo chart has clear signal to show.
    """
    values = list(values)
    streak_len = 6
    start = n_days - streak_len
    base = values[start]
    for i in range(streak_len):
        step = base * (0.03 if kind == "buy" else -0.03) * (i + 1)
        values[start + i] = base + step
    values[-1] = values[start] * (1.20 if kind == "buy" else 0.80)
    return values


def generate(csv_path="../Top10ActiveETF.csv"):
    today = pd.Timestamp.today().normalize()
    trade_dates = build_trade_dates(N_DAYS, today)

    etf_snap, holdings_today = fetch_today_snapshot(csv_path)
    etf_snap = etf_snap.drop_duplicates("etf_code")

    # --- AUM history: small noise around today's real AUM ---
    aum_rows = []
    for _, row in etf_snap.iterrows():
        series = walk_backwards(row["aum"], N_DAYS, daily_pct_std=0.003)
        for d, v in zip(trade_dates, series):
            aum_rows.append({"etf_code": row["etf_code"], "trade_date": d, "aum": v})
    etf_aum = pd.DataFrame(aum_rows)

    # --- Holdings history: random walk per (etf, stock), with a handful
    # of storyline stocks getting an engineered accumulation/distribution
    # streak in the final days ---
    holdings_today = holdings_today.dropna(subset=["shares", "weight"]).reset_index(drop=True)
    unique_stocks = holdings_today["stock_code"].unique().tolist()
    rng = random.Random(7)
    buy_story_stocks = set(rng.sample(unique_stocks, min(4, len(unique_stocks))))
    remaining = [s for s in unique_stocks if s not in buy_story_stocks]
    sell_story_stocks = set(rng.sample(remaining, min(4, len(remaining))))

    holding_rows = []
    for _, row in holdings_today.iterrows():
        shares_series = walk_backwards(row["shares"], N_DAYS, daily_pct_std=0.02, floor=1.0)
        weight_series = walk_backwards(row["weight"], N_DAYS, daily_pct_std=0.02, floor=0.01)

        if row["stock_code"] in buy_story_stocks:
            shares_series = make_storyline(shares_series, N_DAYS, "buy")
            weight_series = make_storyline(weight_series, N_DAYS, "buy")
        elif row["stock_code"] in sell_story_stocks:
            shares_series = make_storyline(shares_series, N_DAYS, "sell")
            weight_series = make_storyline(weight_series, N_DAYS, "sell")

        for d, sh, w in zip(trade_dates, shares_series, weight_series):
            holding_rows.append(
                {
                    "etf_code": row["etf_code"],
                    "stock_code": row["stock_code"],
                    "trade_date": d,
                    "shares": round(sh),
                    "weight": round(w, 4),
                }
            )
    holdings = pd.DataFrame(holding_rows)

    # --- Price history: random walk per stock anchored at today's real close ---
    price_today = holdings_today.drop_duplicates("stock_code")[["stock_code", "price"]].dropna()
    price_rows = []
    for _, row in price_today.iterrows():
        drift = 0.0
        if row["stock_code"] in buy_story_stocks:
            drift = -0.004  # upward drift into today (price rising as accumulation happens)
        elif row["stock_code"] in sell_story_stocks:
            drift = 0.004  # downward drift into today
        series = walk_backwards(row["price"], N_DAYS, daily_pct_std=0.015, drift_pct=drift, floor=0.1)
        for d, p in zip(trade_dates, series):
            price_rows.append({"stock_code": row["stock_code"], "trade_date": d, "close": round(p, 2)})
    prices = pd.DataFrame(price_rows)

    stock_names = (
        holdings_today.drop_duplicates("stock_code").set_index("stock_code")["stock_name"].to_dict()
    )

    return holdings, etf_aum, prices, stock_names, buy_story_stocks, sell_story_stocks


if __name__ == "__main__":
    holdings, etf_aum, prices, stock_names, buy_s, sell_s = generate()
    print("holdings:", holdings.shape)
    print("etf_aum:", etf_aum.shape)
    print("prices:", prices.shape)
    print("buy storyline stocks:", buy_s)
    print("sell storyline stocks:", sell_s)
    holdings.to_csv("synthetic_holdings.csv", index=False)
    etf_aum.to_csv("synthetic_aum.csv", index=False)
    prices.to_csv("synthetic_prices.csv", index=False)
    with open("synthetic_stock_names.json", "w", encoding="utf-8") as f:
        json.dump(stock_names, f, ensure_ascii=False, indent=2)
