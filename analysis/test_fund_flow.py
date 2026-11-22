"""Independent checks for correlation_study.fund_flow() and the screens
built on it (compute_screens, build_series, the ntfy message), from
several angles:

  1. complete data       -> identical to the old plain "sum today / sum
                            yesterday - 1" formula, computed separately here
  2. one fund missing D  -> its previous snapshot stands in for D ("no
                            change"): no phantom sell on D, no phantom buy
                            on D+1, and its real D-1 -> D+1 change lands on
                            D+1 (self-correcting)
  3. stand-in vs. leave-out -> other funds' real moves on D are diluted by
                            the stand-in fund's unchanged shares, as specified
  4. 3-day streak total  -> compounded daily rates == head-to-tail
  5. every holder sells out -> -100% and the 出清 tag
  6. chart series        -> no dip on the missing day, 0% on a price-only day
  7. ntfy message        -> flags the missing fund only when one is missing;
                            a long-discontinued fund is not flagged

Run: .venv/Scripts/python.exe -X utf8 analysis/test_fund_flow.py
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from correlation_study import build_series, fund_flow, missing_fund_days  # noqa: E402
from top_movers_study import compute_screens  # noqa: E402

D = pd.to_datetime(["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"])
NAMES = {}


def H(rows):
    return pd.DataFrame(rows, columns=["ticker", "stock_code", "trade_date", "shares"]).assign(
        trade_date=lambda d: pd.to_datetime(d["trade_date"]), shares=lambda d: d["shares"].astype(float)
    )


def prices_for(codes, dates):
    return pd.DataFrame([(c, d, 100.0) for c in codes for d in dates], columns=["stock_code", "trade_date", "close"])


def rate(flow, code, date):
    r = flow[(flow["stock_code"] == code) & (flow["trade_date"] == pd.Timestamp(date))]
    return None if r.empty else r["variation_rate"].iloc[0]


def old_formula(holdings):
    c = holdings.groupby(["stock_code", "trade_date"])["shares"].sum().reset_index()
    c = c.sort_values(["stock_code", "trade_date"])
    c["variation_rate"] = c.groupby("stock_code")["shares"].pct_change()
    return c.dropna(subset=["variation_rate"])


def missing_day_rows(include_b_on_d2=False, a_sells_on_d2=False):
    """A and B each hold 2330; B buys +300 on D[3]; B's D[2] snapshot is
    missing unless include_b_on_d2. C holds 2454 throughout."""
    rows = []
    for i, d in enumerate(D):
        rows.append(("A", "2330", d, 500.0 if (a_sells_on_d2 and i >= 2) else 1000.0))
        b_shares = 1000.0 if i < 3 else 1300.0
        if i != 2 or include_b_on_d2:
            rows.append(("B", "2330", d, b_shares))
            rows.append(("B", "9999", d, 50.0))
        rows.append(("C", "2454", d, 500.0))
    return H(rows)


def test_complete_data_matches_old_formula():
    rng = np.random.default_rng(7)
    rows = []
    stocks = [f"{1000 + i}" for i in range(40)]
    for f in ["A", "B", "C", "D", "E"]:
        held = rng.choice(stocks, size=25, replace=False)
        for d in pd.bdate_range("2026-09-01", periods=30):
            for s in held:  # every holder keeps a position > 0 every day
                rows.append((f, s, d, float(rng.integers(1, 10_000))))
    h = H(rows)
    new = fund_flow(h).dropna(subset=["variation_rate"]).set_index(["stock_code", "trade_date"])["variation_rate"]
    old = old_formula(h).set_index(["stock_code", "trade_date"])["variation_rate"]
    assert set(new.index) == set(old.index), "same (stock, date) coverage"
    assert np.allclose(new.sort_index(), old.sort_index()), "same values"
    assert not fund_flow(h)["incomplete"].any()
    print("ok 1 complete data == old formula on", len(new), "rates")


def test_missing_fund_stand_in_and_self_correction():
    flow = fund_flow(missing_day_rows())
    assert rate(flow, "2330", D[2]) == 0.0, "D[2]: B stood in with D[1] shares -> 0%, not -50%"
    assert rate(flow, "9999", D[2]) == 0.0, "stock held only by the missing fund: no change, not -100%"
    # D[3]: B's real D[1] 1000 -> D[3] 1300 counted here: 2300/2000 - 1
    assert abs(rate(flow, "2330", D[3]) - 0.15) < 1e-12, rate(flow, "2330", D[3])
    assert rate(flow, "2330", D[4]) == 0.0

    # Same result as if B's D[2] had been read and simply been unchanged
    real = fund_flow(missing_day_rows(include_b_on_d2=True))
    for d in D[1:]:
        assert rate(flow, "2330", d) == rate(real, "2330", d), d
    print("ok 2 missing fund: D[2]=0% (stand-in), D[3]=+15% (B's real 2-day change), D[4]=0%")


def test_screens_no_phantom_with_missing_fund():
    h = missing_day_rows()
    p = prices_for(["2330", "2454", "9999"], D)
    for cut in [3, 4, 5]:  # 04:00 runs after D[2], D[3], D[4]
        s = compute_screens(h[h.trade_date <= D[cut - 1]], p, NAMES)
        assert s["latest_date"] == D[cut - 1], "report the freshest date straight away"
        assert "2330" not in list(s["top_sell_today"]["stock_code"]), cut
    s = compute_screens(h[h.trade_date <= D[3]], p, NAMES)
    assert list(s["top_buy_today"]["stock_code"]) == ["2330"], "B's real buy shows up on D[3]"
    print("ok 2b screens: freshest date reported, no phantom sell, real buy on D[3]")


def test_stand_in_dilutes_other_funds_move():
    flow = fund_flow(missing_day_rows(a_sells_on_d2=True))
    # A 1000 -> 500 on D[2]; B stands in unchanged at 1000: 1500/2000 - 1
    assert abs(rate(flow, "2330", D[2]) - (-0.25)) < 1e-12, rate(flow, "2330", D[2])
    print("ok 3 stand-in counts B as unchanged: A's -50% shows as -25% combined")


def test_streak_compounding_equals_head_to_tail():
    rows = []
    a = [1000, 1100, 1300, 1600, 2000]
    b = [500, 520, 600, 610, 700]
    for i, d in enumerate(D):
        rows += [("A", "2330", d, a[i]), ("B", "2330", d, b[i])]
    h = H(rows)
    s = compute_screens(h, prices_for(["2330"], D), NAMES)
    # last 3 dates = D[2..4]; head-to-tail from D[1]
    expect = (a[4] + b[4]) / (a[1] + b[1]) - 1
    got = s["top_buy_streak"].set_index("stock_code")["total_rate"]["2330"]
    assert abs(got - expect) < 1e-12, (got, expect)
    print(f"ok 4 streak total {got:.4f} == head-to-tail {expect:.4f}")


def test_full_liquidation_visible():
    rows = []
    for i, d in enumerate(D):
        if i < 4:
            rows.append(("A", "3008", d, 800.0))  # A sells out on D[4]
        rows.append(("A", "2330", d, 1000.0))
    h = H(rows)
    assert rate(fund_flow(h), "3008", D[4]) == -1.0
    s = compute_screens(h, prices_for(["2330", "3008"], D), NAMES)
    assert "出清" in s["tags_by_code"].get("3008", []), s["tags_by_code"]
    print("ok 5 sold out by every holder -> -100%, tagged 出清")


def test_chart_series():
    h = missing_day_rows()
    axis = list(D) + [pd.Timestamp("2026-11-23")]  # last one: price-only day
    df = build_series(h, prices_for(["2330"], axis), "2330", date_axis=axis)
    vr, sh = df["variation_rate"], df["shares"]
    assert vr[D[2]] == 0.0 and sh[D[2]] == 2000.0, "missing day: no dip (B stood in at 1000)"
    assert abs(vr[D[3]] - 0.15) < 1e-12 and sh[D[3]] == 2300.0
    assert vr[pd.Timestamp("2026-11-23")] == 0.0, "price-only day reads 0%"
    assert np.isnan(vr[D[0]]), "first day has nothing to compare against"
    print("ok 6 chart: 0% / 2000 shares on missing day, +15% next day, 0% on price-only day")


def test_missing_flags_and_ntfy_message():
    import generate_top_movers_report as g

    h = missing_day_rows()
    m = missing_fund_days(h[["ticker", "trade_date"]])
    assert list(zip(m["ticker"], m["trade_date"])) == [("B", D[2])], m

    # a fund discontinued long ago (like 00991A) is not flagged forever
    old = H([("Z", "2330", d, 1.0) for d in pd.bdate_range("2026-06-01", periods=3)]
            + [("A", "2330", d, 1.0) for d in pd.bdate_range("2026-06-01", periods=40)])
    assert missing_fund_days(old[["ticker", "trade_date"]]).empty

    p = prices_for(["2330", "2454", "9999"], D)
    s = compute_screens(h[h.trade_date <= D[2]], p, NAMES)  # 04:00 run right after B failed
    assert s["missing_funds"] == {D[2]: ["B"]}, s["missing_funds"]
    msg = g._build_message(s, NAMES, "2026-11-18")
    assert "資料缺漏" in msg and "以前一日持股代替" in msg and "11-18：B" in msg, msg

    s2 = compute_screens(missing_day_rows(include_b_on_d2=True), p, NAMES)
    assert s2["missing_funds"] == {} and "資料缺漏" not in g._build_message(s2, NAMES, "x")
    print("ok 7 ntfy flags the missing fund only when one is missing; discontinued fund not flagged")


if __name__ == "__main__":
    test_complete_data_matches_old_formula()
    test_missing_fund_stand_in_and_self_correction()
    test_screens_no_phantom_with_missing_fund()
    test_stand_in_dilutes_other_funds_move()
    test_streak_compounding_equals_head_to_tail()
    test_full_liquidation_visible()
    test_chart_series()
    test_missing_flags_and_ntfy_message()
    print("ALL PASSED")
