"""Builds the quadrant chart's point+trail data for an explicit list of
stock codes (the ones named in the ntfy notification -- see
generate_report._quadrant_highlight_lines) so the chart and the
notification always agree on which stocks are being called out.
"""

import json

import pandas as pd

from indicators import build_report, compute_quadrant_streaks

TRAIL_DAYS = 5


def prepare_chart_data(report: pd.DataFrame, stock_names: dict | None, highlighted_codes: list[str]):
    stock_names = stock_names or {}
    report = report.dropna(subset=["bias"]).copy()
    streaked = compute_quadrant_streaks(report)
    latest_date = streaked["trade_date"].max()
    latest = streaked[streaked["trade_date"] == latest_date].copy()

    plotted = latest[latest["stock_code"].isin(highlighted_codes)].copy()
    order = {code: i for i, code in enumerate(highlighted_codes)}
    plotted["_order"] = plotted["stock_code"].map(order)
    plotted = plotted.sort_values("_order")

    history = report[report["stock_code"].isin(plotted["stock_code"])].sort_values("trade_date")

    points = []
    for _, row in plotted.iterrows():
        code = str(row["stock_code"])
        trail_all = history[history["stock_code"] == code].tail(TRAIL_DAYS)
        trail = [
            {
                "date": d.strftime("%Y-%m-%d"),
                "net_score": None if pd.isna(ns) else round(float(ns), 2),
                "bias": None if pd.isna(b) else round(float(b), 2),
            }
            for d, ns, b in zip(trail_all["trade_date"], trail_all["net_score"], trail_all["bias"])
        ]
        points.append(
            {
                "stock_code": code,
                "stock_name": stock_names.get(code, ""),
                "net_score": round(float(row["net_score"]), 2),
                "bias": round(float(row["bias"]), 2),
                "quadrant": row["quadrant"],
                "n_buy": int(row["n_buy"]),
                "n_sell": int(row["n_sell"]),
                "breadth": int(row["n_buy"] + row["n_sell"]),
                "quadrant_streak": int(row["quadrant_streak"]),
                "max_buy_streak": int(row["max_buy_streak"]),
                "max_sell_streak": int(row["max_sell_streak"]),
                "close": None if pd.isna(row["close"]) else round(float(row["close"]), 2),
                "trail": trail,
            }
        )

    return {"trade_date": latest_date.strftime("%Y-%m-%d"), "points": points}


if __name__ == "__main__":
    holdings = pd.read_csv("synthetic_holdings.csv", parse_dates=["trade_date"], dtype={"stock_code": str})
    etf_aum = pd.read_csv("synthetic_aum.csv", parse_dates=["trade_date"])
    prices = pd.read_csv("synthetic_prices.csv", parse_dates=["trade_date"], dtype={"stock_code": str})
    with open("synthetic_stock_names.json", encoding="utf-8") as f:
        stock_names = json.load(f)

    from generate_report import _quadrant_highlight_lines  # local: avoids a circular import at module load

    report = build_report(holdings, etf_aum, prices)
    report_with_bias = report.dropna(subset=["bias"])
    _lines, highlighted_codes = _quadrant_highlight_lines(report_with_bias, stock_names)
    data = prepare_chart_data(report, stock_names, highlighted_codes)
    print(f"trade_date={data['trade_date']} points={len(data['points'])}")
    with open("chart_data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
