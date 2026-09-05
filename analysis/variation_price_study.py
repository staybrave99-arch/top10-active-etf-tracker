"""One-off study: for a handful of popular stocks, show the daily combined
cross-ETF shares variation rate alongside the stock's own price trend, as
two separate sub-charts per stock (not overlaid -- a %% rate and a price
level are different units).

Reuses fetch()/build_series() from correlation_study.py for the underlying
data (same variation_rate definition: combined ETF shares day-over-day %%
change), just outputs the raw daily series instead of rolling correlations.
"""

import json
import os

import pandas as pd

from correlation_study import CHART_WINDOW_DAYS, DEFAULT_STOCKS, build_series, fetch


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    stocks = DEFAULT_STOCKS

    holdings, prices = fetch(conn_str, stocks.keys())
    print(f"holdings rows: {len(holdings)}, price rows: {len(prices)}")

    result = {"stocks": []}
    for code, name in stocks.items():
        df = build_series(holdings, prices, code, window=CHART_WINDOW_DAYS)
        print(f"{code} {name}: {len(df)} days")

        dates = [d.strftime("%Y-%m-%d") for d in df.index]

        def col(name_, digits):
            return [None if pd.isna(v) else round(float(v), digits) for v in df[name_]]

        result["stocks"].append(
            {
                "stock_code": code,
                "stock_name": name,
                "dates": dates,
                "variation_rate": col("variation_rate", 4),
                "close": col("close", 2),
                "shares": col("shares", 0),
            }
        )

    out_path = os.path.join(os.path.dirname(__file__), "variation_price_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
