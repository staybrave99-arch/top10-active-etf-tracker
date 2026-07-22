"""Validates compute_flags_and_streaks / aggregate_cross_etf against the
exact worked example in section 11 of the design doc (STRICT mode, since
that's the mode the example was written to demonstrate). This is a
correctness check on the formulas themselves, independent of which
streak_mode this project actually runs with.
"""

import pandas as pd

from indicators import aggregate_cross_etf, compute_flags_and_streaks

rows = [
    # Stock X @ ETF A
    ("A", "X", "2024-01-01", 1000, 5.0),
    ("A", "X", "2024-01-02", 1200, 5.4),
    ("A", "X", "2024-01-03", 1500, 6.0),
    ("A", "X", "2024-01-04", 1500, 6.3),  # shares flat -> not a buy day under STRICT
    # Stock X @ ETF B (day1 baseline implied by the doc's day2 deltas: +100/+0.2)
    ("B", "X", "2024-01-01", 500, 2.0),
    ("B", "X", "2024-01-02", 600, 2.2),
    ("B", "X", "2024-01-03", 700, 2.5),
    ("B", "X", "2024-01-04", 900, 2.9),
    # Stock Y @ ETF A (distribution example)
    ("A", "Y", "2024-01-01", 2000, 8.0),
    ("A", "Y", "2024-01-02", 1800, 7.5),
    ("A", "Y", "2024-01-03", 1500, 6.8),
]
holdings = pd.DataFrame(rows, columns=["etf_code", "stock_code", "trade_date", "shares", "weight"])
holdings["trade_date"] = pd.to_datetime(holdings["trade_date"])

scored = compute_flags_and_streaks(holdings, streak_mode="STRICT")


def get(etf, stock, date):
    r = scored[
        (scored.etf_code == etf) & (scored.stock_code == stock) & (scored.trade_date == pd.Timestamp(date))
    ].iloc[0]
    return r


checks = []


def check(label, actual, expected, tol=1e-9):
    ok = abs(actual - expected) < tol
    checks.append((label, actual, expected, ok))


# X @ A
r = get("A", "X", "2024-01-02")
check("X@A day2 buy_streak", r.buy_streak, 1)
check("X@A day2 cum_dw_up", r.cum_dw_up, 0.4)
check("X@A day2 a_up", r.a_up, 0.40)

r = get("A", "X", "2024-01-03")
check("X@A day3 buy_streak", r.buy_streak, 2)
check("X@A day3 cum_dw_up", r.cum_dw_up, 1.0)
check("X@A day3 a_up", r.a_up, 1.25)

r = get("A", "X", "2024-01-04")
check("X@A day4 buy_flag (shares flat -> excluded under STRICT)", r.buy_flag, 0)
check("X@A day4 a_up", r.a_up, 0.0)

# X @ B
r = get("B", "X", "2024-01-02")
check("X@B day2 a_up", r.a_up, 0.20)
r = get("B", "X", "2024-01-03")
check("X@B day3 buy_streak", r.buy_streak, 2)
check("X@B day3 a_up", r.a_up, 0.625)
r = get("B", "X", "2024-01-04")
check("X@B day4 buy_streak", r.buy_streak, 3)
check("X@B day4 cum_dw_up", r.cum_dw_up, 0.9)
check("X@B day4 a_up", r.a_up, 1.35)

# Y @ A (sell side)
r = get("A", "Y", "2024-01-02")
check("Y@A day2 sell_streak", r.sell_streak, 1)
check("Y@A day2 a_dn", r.a_dn, 0.50)
r = get("A", "Y", "2024-01-03")
check("Y@A day3 sell_streak", r.sell_streak, 2)
check("Y@A day3 cum_dw_dn", r.cum_dw_dn, 1.2)
check("Y@A day3 a_dn", r.a_dn, 1.50)

# Cross-ETF aggregation for stock X (EQUAL weighting, i.e. aum=1e8 for both funds
# so the aum_weight multiplier becomes 1 and net_score reduces to the doc's example)
aum = pd.DataFrame(
    {
        "etf_code": ["A", "B"] * 4,
        "trade_date": pd.to_datetime(
            ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02", "2024-01-03", "2024-01-03", "2024-01-04", "2024-01-04"]
        ),
        "aum": [1e8] * 8,
    }
)
agg = aggregate_cross_etf(scored, aum)


def get_agg(stock, date):
    return agg[(agg.stock_code == stock) & (agg.trade_date == pd.Timestamp(date))].iloc[0]


r = get_agg("X", "2024-01-02")
check("X cross-ETF day2 buy_pressure", r.buy_pressure, 0.60)
r = get_agg("X", "2024-01-03")
check("X cross-ETF day3 buy_pressure", r.buy_pressure, 1.875)
r = get_agg("X", "2024-01-04")
check("X cross-ETF day4 buy_pressure", r.buy_pressure, 1.35)

r = get_agg("Y", "2024-01-03")
check("Y cross-ETF day3 net_score", r.net_score, -1.50)

print(f"{'label':55s} {'actual':>10s} {'expected':>10s}  ok")
all_ok = True
for label, actual, expected, ok in checks:
    print(f"{label:55s} {actual:>10.4f} {expected:>10.4f}  {'OK' if ok else 'FAIL'}")
    all_ok = all_ok and ok

print()
print("ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED")
