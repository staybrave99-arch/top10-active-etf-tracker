"""Screens the FULL universe of held stocks (not just the handful of
popular ones tracked elsewhere) for the biggest movers.

  Screen 1 (today, single day): top 5 stocks by variation_rate today
  (biggest combined cross-ETF buying) and top 5 by most negative
  variation_rate today (biggest combined selling).

  Screen 2 (3-day streak): stocks whose variation_rate has been strictly
  positive for each of the dataset's actual last 3 trading days
  ("continuous buying") or strictly negative for each ("continuous
  selling") -- exact zero breaks the streak, same as the "no signal isn't
  a quadrant" rule used elsewhere in this project, and a gap on any one
  of those 3 specific dates disqualifies the stock (no reaching further
  back into its history). Ranked by the TOTAL 3-day variation rate
  ((shares_end - shares_before_streak) / shares_before_streak), top 3 of
  each direction.

Both screens additionally require |rate| > MOVEMENT_THRESHOLD (10%) to
be listed at all -- a stock that's merely the "least small" move of the
day doesn't belong on a "biggest movers" list, so a screen can come back
with fewer than 5/3 entries (or none) rather than padding out with
sub-10% noise.

compute_screens() is the reusable core (imported by
generate_top_movers_report.py for the daily ntfy+chart pipeline); running
this file directly is a standalone CLI for ad hoc checks that also writes
top_movers_data.json in the {dates, variation_rate, close} shape
variation_price_study.py produces, for variation_price_chart_template.html.
"""

import json
import os

import pandas as pd
import psycopg2

from correlation_study import CHART_WINDOW_DAYS, build_series

LIQUIDATION_THRESHOLD = -0.95  # sell variation rate more extreme than this -> "出清"
MOVEMENT_THRESHOLD = 0.10  # |rate| must exceed this to be listed in any screen at all


def fetch_all(conn_str):
    conn = psycopg2.connect(conn_str)
    holdings = pd.read_sql(
        """
        SELECT h.stock_code, s.data_date AS trade_date, h.shares
        FROM etf_holding h
        JOIN etf_snapshot s ON s.id = h.snapshot_id
        """,
        conn,
    )
    prices = pd.read_sql("SELECT stock_code, trade_date, price AS close FROM stock_price", conn)
    names_df = pd.read_sql(
        """
        SELECT DISTINCT ON (stock_code) stock_code, stock_name
        FROM etf_holding
        WHERE stock_name IS NOT NULL
        """,
        conn,
    )
    conn.close()

    holdings["trade_date"] = pd.to_datetime(holdings["trade_date"])
    holdings["shares"] = holdings["shares"].astype(float)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["close"] = prices["close"].astype(float)
    names = dict(zip(names_df["stock_code"], names_df["stock_name"]))
    return holdings, prices, names


def _three_day_streak(g, last3_dates):
    """g: one stock's (trade_date, shares, variation_rate) rows, any order.
    last3_dates: the dataset's own [D-2, D-1, D] trading dates (ascending)
    -- NOT whatever 3 rows this stock last happened to have. A stock
    missing a variation_rate on any one of those exact 3 dates (a gap in
    its own disclosure, e.g. it dropped out of a fund's reported top-N
    for a day) does not count as "continuous," even if it has 3 valid
    rates somewhere earlier in its history.
    """
    by_date = g.set_index("trade_date")
    if not all(d in by_date.index for d in last3_dates):
        return None
    rates = by_date.loc[last3_dates, "variation_rate"]
    if rates.isna().any():
        return None
    if (rates > 0).all():
        direction = "buy"
    elif (rates < 0).all():
        direction = "sell"
    else:
        return None

    g = g.sort_values("trade_date").reset_index(drop=True)
    dates_sorted = g["trade_date"].tolist()
    shares_sorted = g["shares"].tolist()
    start_pos = dates_sorted.index(last3_dates[0])
    if start_pos == 0:
        return None
    start_shares = shares_sorted[start_pos - 1]
    end_shares = by_date.loc[last3_dates[-1], "shares"]
    if not start_shares:
        return None
    return direction, (end_shares - start_shares) / start_shares


def compute_screens(holdings, prices, names):
    """Returns a dict with everything the report/CLI need:
    latest_date, last3_dates, top_buy_today, top_sell_today,
    top_buy_streak, top_sell_streak (all DataFrames), tags_by_code
    (stock_code -> list[str]), and selected (deduplicated stock_code list,
    in a stable buy-today/sell-today/buy-streak/sell-streak order).
    """
    combined = holdings.groupby(["stock_code", "trade_date"])["shares"].sum().reset_index()
    combined = combined.sort_values(["stock_code", "trade_date"])
    combined["variation_rate"] = combined.groupby("stock_code")["shares"].pct_change()

    all_dates = sorted(combined["trade_date"].unique())
    latest_date = all_dates[-1]
    last3_dates = all_dates[-3:]

    latest = combined[(combined["trade_date"] == latest_date) & combined["variation_rate"].notna()]
    buy_candidates_today = latest[latest["variation_rate"] > MOVEMENT_THRESHOLD]
    sell_candidates_today = latest[latest["variation_rate"] < -MOVEMENT_THRESHOLD]
    top_buy_today = buy_candidates_today.sort_values("variation_rate", ascending=False).head(5)
    top_sell_today = sell_candidates_today.sort_values("variation_rate", ascending=True).head(5)

    streak_rows = []
    for code, g in combined.groupby("stock_code"):
        info = _three_day_streak(g, last3_dates)
        if info is None:
            continue
        direction, total_rate = info
        streak_rows.append({"stock_code": code, "direction": direction, "total_rate": total_rate})
    streak_df = pd.DataFrame(streak_rows, columns=["stock_code", "direction", "total_rate"])

    buy_streak_candidates = streak_df[(streak_df["direction"] == "buy") & (streak_df["total_rate"] > MOVEMENT_THRESHOLD)]
    sell_streak_candidates = streak_df[(streak_df["direction"] == "sell") & (streak_df["total_rate"] < -MOVEMENT_THRESHOLD)]
    top_buy_streak = buy_streak_candidates.sort_values("total_rate", ascending=False).head(3)
    top_sell_streak = sell_streak_candidates.sort_values("total_rate", ascending=True).head(3)

    tags_by_code = {}

    def add_tags(ranked_df, label, rate_col):
        for rank, (_, r) in enumerate(ranked_df.iterrows(), start=1):
            tags = tags_by_code.setdefault(r["stock_code"], [])
            tags.append(f"{label}第{rank}名")
            if r[rate_col] <= LIQUIDATION_THRESHOLD:
                tags.append("出清")

    add_tags(top_buy_today, "買超", "variation_rate")
    add_tags(top_sell_today, "賣超", "variation_rate")
    add_tags(top_buy_streak, "連續買超", "total_rate")
    add_tags(top_sell_streak, "連續賣超", "total_rate")

    selected = list(
        dict.fromkeys(
            list(top_buy_today["stock_code"])
            + list(top_sell_today["stock_code"])
            + list(top_buy_streak["stock_code"])
            + list(top_sell_streak["stock_code"])
        )
    )

    return {
        "latest_date": latest_date,
        "last3_dates": last3_dates,
        "top_buy_today": top_buy_today,
        "top_sell_today": top_sell_today,
        "top_buy_streak": top_buy_streak,
        "top_sell_streak": top_sell_streak,
        "tags_by_code": tags_by_code,
        "selected": selected,
    }


def fmt(code, rate, names):
    mark = "（出清）" if rate <= LIQUIDATION_THRESHOLD else ""
    return f"{code} {names.get(code, '')} {rate * 100:+.2f}%{mark}"


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    holdings, prices, names = fetch_all(conn_str)
    print(f"holdings rows: {len(holdings)}, price rows: {len(prices)}, distinct stocks: {holdings['stock_code'].nunique()}")

    screens = compute_screens(holdings, prices, names)
    print(f"latest_date={pd.Timestamp(screens['latest_date']).date()}, "
          f"3-day window={[pd.Timestamp(d).date() for d in screens['last3_dates']]}")

    print("=== today's biggest buy (top 5) ===")
    for _, r in screens["top_buy_today"].iterrows():
        print(" ", fmt(r["stock_code"], r["variation_rate"], names))
    print("=== today's biggest sell (top 5) ===")
    for _, r in screens["top_sell_today"].iterrows():
        print(" ", fmt(r["stock_code"], r["variation_rate"], names))
    print("=== 3-day continuous buy, biggest total (top 3) ===")
    for _, r in screens["top_buy_streak"].iterrows():
        print(" ", fmt(r["stock_code"], r["total_rate"], names))
    print("=== 3-day continuous sell, biggest total (top 3) ===")
    for _, r in screens["top_sell_streak"].iterrows():
        print(" ", fmt(r["stock_code"], r["total_rate"], names))

    selected = screens["selected"]
    tags_by_code = screens["tags_by_code"]
    print(f"selected {len(selected)} distinct stocks: {selected}")

    result = {"stocks": []}
    for code in selected:
        df = build_series(holdings, prices, code, window=CHART_WINDOW_DAYS)

        def col(name_, digits):
            return [None if pd.isna(v) else round(float(v), digits) for v in df[name_]]

        result["stocks"].append(
            {
                "stock_code": code,
                "stock_name": names.get(code, ""),
                "tags": tags_by_code.get(code, []),
                "dates": [d.strftime("%Y-%m-%d") for d in df.index],
                "variation_rate": col("variation_rate", 4),
                "close": col("close", 2),
            }
        )

    out_path = os.path.join(os.path.dirname(__file__), "top_movers_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
