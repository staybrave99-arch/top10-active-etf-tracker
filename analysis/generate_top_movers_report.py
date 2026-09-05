"""Daily production entry point (mirrors generate_report.py): fetch from
Postgres, screen the full market for the day's biggest movers (see
top_movers_study.py), render docs/top_movers.html for GitHub Pages, and
write the ntfy notification payload (summary text + a "查看圖表" action
button linking to that page).
"""

import json
import os

import pandas as pd

from correlation_study import build_series, shared_chart_axis
from top_movers_study import LIQUIDATION_THRESHOLD, compute_screens, fetch_all, fmt

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "top_movers.html")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "variation_price_chart_template.html")
NTFY_PAYLOAD_PATH = os.path.join(os.path.dirname(__file__), "..", "ntfy_payload.json")
NTFY_TOPIC = "ETF-Tracking-0577"
PAGE_TITLE = "全市場買賣最大股票：變動率與股價走勢"
PAGE_URL = "https://staybrave99-arch.github.io/top10-active-etf-tracker/top_movers.html"


def _empty_page(reason: str) -> str:
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{PAGE_TITLE}</title>"
        f"<p style='font-family:sans-serif;padding:2rem'>{reason}</p>"
    )


def _section(title, rows, rate_col, names):
    lines = [f"【{title}】"]
    if rows.empty:
        lines.append("無")
    else:
        for _, r in rows.iterrows():
            lines.append(fmt(r["stock_code"], r[rate_col], names))
    return lines


def _build_message(screens, names, date_str):
    lines = [f"每日買賣最大變動率 ({date_str}):", ""]
    lines += _section("今日買超前5名", screens["top_buy_today"], "variation_rate", names)
    lines.append("")
    lines += _section("今日賣超前5名", screens["top_sell_today"], "variation_rate", names)
    lines.append("")
    lines += _section("連續3日買超前3名", screens["top_buy_streak"], "total_rate", names)
    lines.append("")
    lines += _section("連續3日賣超前3名", screens["top_sell_streak"], "total_rate", names)
    return "\n".join(lines)


def _write_ntfy_payload(message: str):
    payload = {
        "topic": NTFY_TOPIC,
        "title": "主動式ETF每日買賣最大變動率",
        "message": message,
        "actions": [{"action": "view", "label": "查看圖表", "url": PAGE_URL}],
    }
    with open(NTFY_PAYLOAD_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"wrote {NTFY_PAYLOAD_PATH}")


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    holdings, prices, names = fetch_all(conn_str)
    print(f"holdings rows: {len(holdings)}, price rows: {len(prices)}, distinct stocks: {holdings['stock_code'].nunique()}")

    if holdings.empty:
        html = _empty_page("尚無持股資料，請稍候排程累積資料後再查看。")
        ntfy_message = f"尚無持股資料，請稍候排程累積資料後再查看。\n{PAGE_URL}"
    else:
        screens = compute_screens(holdings, prices, names)
        date_str = pd.Timestamp(screens["latest_date"]).strftime("%Y-%m-%d")
        print(f"latest_date={date_str}, selected={screens['selected']}")

        selected = screens["selected"]
        tags_by_code = screens["tags_by_code"]

        chart_axis = shared_chart_axis(holdings, prices)
        data = {"stocks": []}
        for code in selected:
            df = build_series(holdings, prices, code, date_axis=chart_axis)

            def col(name_, digits):
                return [None if pd.isna(v) else round(float(v), digits) for v in df[name_]]

            data["stocks"].append(
                {
                    "stock_code": code,
                    "stock_name": names.get(code, ""),
                    "tags": tags_by_code.get(code, []),
                    "dates": [d.strftime("%Y-%m-%d") for d in df.index],
                    "variation_rate": col("variation_rate", 4),
                    "close": col("close", 2),
                    "shares": col("shares", 0),
                }
            )

        with open(TEMPLATE_PATH, encoding="utf-8") as f:
            template = f.read()
        html = template.replace("__VP_DATA__", json.dumps(data, ensure_ascii=False))
        html = html.replace("個股變動率與股價走勢", PAGE_TITLE, 1)

        ntfy_message = _build_message(screens, names, date_str)

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {OUT_PATH}")

    _write_ntfy_payload(ntfy_message)


if __name__ == "__main__":
    main()
