"""Consolidated real-data report generator for CI use: fetch from Postgres,
build indicators, render docs/index.html for GitHub Pages, and write the
ntfy notification payload (quadrant highlights + link).

Expects DATABASE_URL in the environment (e.g. pointed at a local
`flyctl proxy` tunnel to the Postgres app).
"""

import json
import os

from build_chart_data import prepare_chart_data, PLOT_BREADTH_MIN
from fetch_real_data import fetch
from indicators import build_report

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "chart_template.html")
NTFY_PAYLOAD_PATH = os.path.join(os.path.dirname(__file__), "..", "ntfy_payload.json")
NTFY_TOPIC = "ETF-Tracking-0577"
PAGES_URL = "https://staybrave99-arch.github.io/top10-active-etf-tracker/"

# (label, quadrant tag, corner-distance score to rank candidates within that quadrant)
QUADRANT_PICKS = [
    ("最強勢", "順勢加碼", lambda ns, b: ns + b),
    ("留意反轉", "強勢減碼", lambda ns, b: b - ns),
    ("聰明錢先卡位", "逆勢加碼", lambda ns, b: ns - b),
    ("最該迴避", "弱勢減碼", lambda ns, b: -ns - b),
]


def _empty_page(reason: str) -> str:
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>主動式ETF持股加碼指標</title>"
        f"<p style='font-family:sans-serif;padding:2rem'>{reason}</p>"
    )


def _quadrant_highlight_lines(report_with_bias, stock_names):
    latest_date = report_with_bias["trade_date"].max()
    latest = report_with_bias[report_with_bias["trade_date"] == latest_date]
    breadth = latest["n_buy"] + latest["n_sell"]
    eligible = latest[breadth >= PLOT_BREADTH_MIN]

    lines = []
    for label, quadrant, score_fn in QUADRANT_PICKS:
        candidates = eligible[eligible["quadrant"] == quadrant]
        if candidates.empty:
            lines.append(f"{label}: 無")
            continue
        scores = candidates.apply(lambda r: score_fn(r["net_score"], r["bias"]), axis=1)
        best = candidates.loc[scores.idxmax()]
        code = str(best["stock_code"])
        name = stock_names.get(code, "")
        lines.append(f"{label}: {code} {name}".rstrip())
    return lines


def _write_ntfy_payload(message: str):
    payload = {
        "topic": NTFY_TOPIC,
        "title": "主動式ETF持股加碼指標已更新",
        "message": message,
        "click": PAGES_URL,
    }
    with open(NTFY_PAYLOAD_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"wrote {NTFY_PAYLOAD_PATH}")


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    holdings, etf_aum, prices, stock_names = fetch(conn_str)

    if holdings.empty:
        html = _empty_page("尚無持股資料，請稍候排程累積資料後再查看。")
        ntfy_message = f"尚無持股資料，請稍候排程累積資料後再查看。\n{PAGES_URL}"
    else:
        report = build_report(holdings, etf_aum, prices)
        report_with_bias = report.dropna(subset=["bias"])
        if report_with_bias.empty:
            html = _empty_page("持股資料已開始累積，但股價歷史尚不足以計算 BIAS 指標，請稍候再查看。")
            ntfy_message = f"持股資料已開始累積，但股價歷史尚不足以計算 BIAS 指標，請稍候再查看。\n{PAGES_URL}"
        else:
            data = prepare_chart_data(report, stock_names)
            with open(TEMPLATE_PATH, encoding="utf-8") as f:
                template = f.read()
            html = template.replace("__CHART_DATA__", json.dumps(data, ensure_ascii=False))
            print(f"trade_date={data['trade_date']} points={len(data['points'])}")

            highlight_lines = _quadrant_highlight_lines(report_with_bias, stock_names)
            ntfy_message = "\n".join(["主動式ETF持股趨勢分析:"] + highlight_lines + ["", PAGES_URL])

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {OUT_PATH}")

    _write_ntfy_payload(ntfy_message)


if __name__ == "__main__":
    main()
