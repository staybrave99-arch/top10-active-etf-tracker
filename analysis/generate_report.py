"""Consolidated real-data report generator for CI use: fetch from Postgres,
build indicators, render docs/index.html for GitHub Pages, and write the
ntfy notification payload (quadrant highlights + link).

Expects DATABASE_URL in the environment (e.g. pointed at a local
`flyctl proxy` tunnel to the Postgres app).
"""

import json
import os

from build_chart_data import prepare_chart_data
from fetch_real_data import fetch
from indicators import build_report, compute_quadrant_streaks

MIN_STREAK = 2
MAX_TRANSITION_STREAK = 5

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "index.html")
TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "chart_template.html")
NTFY_PAYLOAD_PATH = os.path.join(os.path.dirname(__file__), "..", "ntfy_payload.json")
NTFY_TOPIC = "ETF-Tracking-0577"
PAGES_URL = "https://staybrave99-arch.github.io/top10-active-etf-tracker/"


def _empty_page(reason: str) -> str:
    return (
        "<!doctype html><meta charset='utf-8'>"
        "<title>主動式ETF持股加碼指標</title>"
        f"<p style='font-family:sans-serif;padding:2rem'>{reason}</p>"
    )


def _format_pick(stock_code, stock_names):
    code = str(stock_code)
    name = stock_names.get(code, "")
    return f"{code} {name}".rstrip()


def _top_n_by_streak(latest, quadrant, n, stock_names):
    """Top-n stocks currently sitting in `quadrant`, ranked by how many
    consecutive days (>= MIN_STREAK) they've stayed there. Streak length is
    a *necessary* condition -- a stock only 1 day into the quadrant never
    qualifies, no matter how strong its score.
    """
    candidates = latest[(latest["quadrant"] == quadrant) & (latest["quadrant_streak"] >= MIN_STREAK)]
    if candidates.empty:
        return "無"
    top = candidates.sort_values("quadrant_streak", ascending=False).head(n)
    return "、".join(_format_pick(code, stock_names) for code in top["stock_code"])


def _transition_list(latest, from_quadrant, to_quadrant, stock_names):
    """Every stock now sitting in `to_quadrant` for 2-5 consecutive days,
    where the quadrant right before that streak began was `from_quadrant`
    -- i.e. it made the from->to move within roughly the last 5 trading
    days and hasn't left since. These two corners are about the *move*
    itself (a leading signal), so every qualifying stock is listed.
    """
    candidates = latest[
        (latest["quadrant"] == to_quadrant)
        & (latest["quadrant_streak"] >= MIN_STREAK)
        & (latest["quadrant_streak"] <= MAX_TRANSITION_STREAK)
        & (latest["quadrant_before_streak"] == from_quadrant)
    ]
    if candidates.empty:
        return "無"
    return "、".join(_format_pick(code, stock_names) for code in candidates["stock_code"])


def _quadrant_highlight_lines(report_with_bias, stock_names):
    streaked = compute_quadrant_streaks(report_with_bias)
    latest_date = streaked["trade_date"].max()
    latest = streaked[streaked["trade_date"] == latest_date]

    return [
        "主動式ETF持股趨勢分析:",
        f"最強勢: {_top_n_by_streak(latest, '順勢加碼', 3, stock_names)}",
        f"留意反轉: {_transition_list(latest, '順勢加碼', '強勢減碼', stock_names)}",
        f"聰明錢先卡位: {_transition_list(latest, '弱勢減碼', '逆勢加碼', stock_names)}",
        f"最該迴避: {_top_n_by_streak(latest, '弱勢減碼', 3, stock_names)}",
    ]


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
            ntfy_message = "\n".join(highlight_lines + ["", PAGES_URL])

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {OUT_PATH}")

    _write_ntfy_payload(ntfy_message)


if __name__ == "__main__":
    main()
