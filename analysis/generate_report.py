"""Consolidated real-data report generator for CI use: fetch from Postgres,
build indicators, and render docs/index.html for GitHub Pages.

Expects DATABASE_URL in the environment (e.g. pointed at a local
`flyctl proxy` tunnel to the Postgres app).
"""

import json
import os

from build_chart_data import prepare_chart_data
from fetch_real_data import fetch
from indicators import build_report

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "chart_template.html")


def _empty_page(reason: str) -> str:
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>主動式ETF持股加碼指標</title>"
        f"<p style='font-family:sans-serif;padding:2rem'>{reason}</p>"
    )


def main():
    conn_str = os.environ["DATABASE_URL"]
    holdings, etf_aum, prices, stock_names = fetch(conn_str)

    if holdings.empty:
        html = _empty_page("尚無持股資料，請稍候排程累積資料後再查看。")
    else:
        report = build_report(holdings, etf_aum, prices)
        if report.dropna(subset=["bias"]).empty:
            html = _empty_page("持股資料已開始累積，但股價歷史尚不足以計算 BIAS 指標，請稍候再查看。")
        else:
            data = prepare_chart_data(report, stock_names)
            with open(TEMPLATE_PATH, encoding="utf-8") as f:
                template = f.read()
            html = template.replace("__CHART_DATA__", json.dumps(data, ensure_ascii=False))
            print(f"trade_date={data['trade_date']} points={len(data['points'])}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
