"""Checks the scraper's scheduling logic (no network, no DB):
the 22:00-13:00 run window, the trade date the 04:00 run files under, and which funds a run decides to scrape.

Run: .venv/Scripts/python.exe test_catchup_plan.py
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from scraper.main import SKIP_TICKERS, in_run_window, load_etf_list, plan_tickers
from scraper.utils import expected_trade_date

TPE = ZoneInfo("Asia/Taipei")


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TPE)


def test_run_window():
    assert not in_run_window(at(2026, 11, 23, 21, 59))
    assert in_run_window(at(2026, 11, 23, 22, 0))
    assert in_run_window(at(2026, 11, 24, 4, 0))  # scheduled run
    assert in_run_window(at(2026, 11, 24, 12, 59))  # drifted run still ok
    assert not in_run_window(at(2026, 11, 24, 13, 0))  # cutoff
    assert not in_run_window(at(2026, 11, 24, 20, 0))
    print("ok run window: 22:00 <= t < 13:00")


def test_scheduled_run_files_under_the_previous_trade_date():
    monday = date(2026, 11, 23)
    runs = [at(2026, 11, 24, 4), at(2026, 11, 24, 9, 30),  # 04:00 run, and drifted 5.5h
            at(2026, 11, 23, 23)]  # a manual re-run the same night
    for r in runs:
        assert in_run_window(r)
        assert expected_trade_date(r) == monday, (r, expected_trade_date(r))
    # Friday's data is scraped on Saturday 04:00
    assert expected_trade_date(at(2026, 11, 28, 4)) == date(2026, 11, 27)
    print("ok 04:00 run (and drift to 09:30) files under the previous trade date")


def test_plan_only_missing_funds():
    rows = load_etf_list("Top10ActiveETF.csv")
    all_live = {r["代號"] for r in rows} - SKIP_TICKERS

    todo = plan_tickers(rows, already_saved=set(), skip=SKIP_TICKERS)
    assert {r["代號"] for r in todo} == all_live, "scheduled run: every fund except the skipped one"

    saved = all_live - {"00982A"}
    todo = plan_tickers(rows, already_saved=saved, skip=SKIP_TICKERS)
    assert [r["代號"] for r in todo] == ["00982A"], "manual re-run: only the missing fund"

    assert plan_tickers(rows, already_saved=all_live, skip=SKIP_TICKERS) == [], "nothing missing -> no-op"
    print("ok plan: all funds / only the missing one / nothing")


if __name__ == "__main__":
    test_run_window()
    test_scheduled_run_files_under_the_previous_trade_date()
    test_plan_only_missing_funds()
    print("ALL PASSED")
