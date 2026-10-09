"""One-off study: does the cross-ETF variation_rate signal have any
real predictive power over future stock returns, market-wide?

correlation_study.py's approach (single-stock time-series rolling
correlation over a handful of hand-picked large caps, 10-day window)
has too little statistical power to tell a real signal from noise -- a
10-point rolling correlation swings wildly (observed -0.77 to +0.9 for
the same stock across different windows) even when the true
correlation is exactly zero, and picking large caps like 2330 means
the 8 tracked ETFs' trading is a rounding error next to that stock's
own daily volume, so no effect should be expected there regardless of
whether the signal is real.

This instead runs a cross-sectional Information Coefficient (IC)
analysis -- the standard method for validating whether a factor/signal
has predictive power in quant finance:

  For each trading day t, across EVERY stock held by any of the 8
  tracked ETFs with a valid variation_rate(t) that day (not a fixed
  handful of large caps -- however many names actually qualify):
    IC(t, lag) = Spearman rank correlation between variation_rate(t)
                 and forward_return(t, t+lag)
                     = (close[t+lag] - close[t]) / close[t]   (lag>=1)
                 or same_day_return(t) = close[t]/close[t-1] - 1  (lag=0)

  Then mean(IC) across all valid days, with a t-test on whether it's
  significantly different from zero, and a win rate (fraction of days
  with IC > 0).

This has far more statistical power than the single-stock approach:
each day's cross-section has however many stocks qualified that day
(usually dozens), not a fixed 10-calendar-day window.
"""

import os

import numpy as np
import pandas as pd
import psycopg2

from top_movers_study import fetch_all

LAGS = [0, 1, 3, 5, 10]
MIN_STOCKS_PER_DAY = 10  # a day's IC is only meaningful with enough names


def build_panels(holdings, prices):
    combined = holdings.groupby(["stock_code", "trade_date"])["shares"].sum().reset_index()
    combined = combined.sort_values(["stock_code", "trade_date"])
    combined["variation_rate"] = combined.groupby("stock_code")["shares"].pct_change()

    var_panel = combined.pivot(index="trade_date", columns="stock_code", values="variation_rate")
    price_panel = prices.pivot_table(index="trade_date", columns="stock_code", values="close", aggfunc="last")
    price_panel = price_panel.sort_index()

    return var_panel, price_panel


def forward_return(price_panel, lag):
    """lag=0: same-day return (close[t]/close[t-1] - 1) -- how much the
    price already moved on the very day this variation_rate became
    known (reactive, not predictive). lag>=1: forward lag-day return
    (close[t+lag]/close[t] - 1) -- whether today's variation_rate
    predicts the next `lag` days' move.
    """
    if lag == 0:
        return price_panel.pct_change()
    return price_panel.shift(-lag) / price_panel - 1


def daily_ic(var_panel, fwd_panel, min_stocks):
    dates = var_panel.index.intersection(fwd_panel.index)
    ic = pd.Series(index=dates, dtype=float)
    n_stocks = pd.Series(index=dates, dtype="Int64")
    for d in dates:
        v, f = var_panel.loc[d], fwd_panel.loc[d]
        common = v.index.intersection(f.index)
        v, f = v[common], f[common]
        mask = v.notna() & f.notna()
        v, f = v[mask], f[mask]
        n_stocks[d] = len(v)
        if len(v) < min_stocks or v.nunique() < 2 or f.nunique() < 2:
            ic[d] = np.nan
            continue
        # Spearman = Pearson-on-ranks; computed this way (rather than
        # .corr(method="spearman")) to avoid a scipy dependency this
        # project doesn't otherwise need, matching correlation_study.py's
        # own _rolling_bivariate().
        ic[d] = v.rank().corr(f.rank())
    return ic, n_stocks


def summarize(ic, n_stocks):
    valid = ic.dropna()
    n = len(valid)
    if n == 0:
        return {"n_days": 0}
    mean_ic, std_ic = valid.mean(), valid.std()
    t_stat = mean_ic / (std_ic / np.sqrt(n)) if std_ic > 0 else float("nan")
    return {
        "n_days": n,
        "mean_ic": round(float(mean_ic), 4),
        "std_ic": round(float(std_ic), 4),
        "t_stat": round(float(t_stat), 2),
        "win_rate": round(float((valid > 0).mean()), 3),
        "median_n_stocks": int(n_stocks[valid.index].median()),
    }


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    holdings, prices, names = fetch_all(conn_str)
    print(f"holdings rows: {len(holdings)}, price rows: {len(prices)}, distinct stocks: {holdings['stock_code'].nunique()}")

    var_panel, price_panel = build_panels(holdings, prices)
    print(f"variation_rate panel: {var_panel.shape[0]} dates x {var_panel.shape[1]} stocks")
    print(f"price panel: {price_panel.shape[0]} dates x {price_panel.shape[1]} stocks")
    print()

    for lag in LAGS:
        fwd = forward_return(price_panel, lag)
        ic, n_stocks = daily_ic(var_panel, fwd, MIN_STOCKS_PER_DAY)
        stats = summarize(ic, n_stocks)
        label = "same-day return (reactive)" if lag == 0 else f"forward {lag}-day return (predictive)"
        print(f"=== lag={lag}: {label} ===")
        print(f"  {stats}")
        print()


if __name__ == "__main__":
    main()
