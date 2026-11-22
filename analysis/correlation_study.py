"""One-off study: does a stock's combined cross-ETF shares change rate
correlate with its own price return? Not part of the daily pipeline --
run manually, e.g. via a temporary GH Actions job with the DB proxy
(see this repo's session history for the pattern), or locally with
DATABASE_URL pointed at a `flyctl proxy` tunnel.

Metric definitions:
  variation_rate(stock, date) -- see fund_flow(): each fund's shares
      today vs. that SAME fund's own previous snapshot, summed over the
      funds that actually have a snapshot today:
        sum_f shares_f(today) / sum_f shares_f(f's previous snapshot) - 1
  price_return(stock, date)   = close.pct_change()

Reported per stock, as a *rolling* correlation (not one number for the
whole period) so day-to-day changes in the relationship are visible:
  - same-day:  variation_rate(t) vs price_return(t)
  - lead-1:    variation_rate(t) vs price_return(t+1)  (does today's
    combined ETF flow predict tomorrow's price move?)
Both Pearson and Spearman are computed; Spearman is the primary read
since these series can have the same kind of magnitude outliers seen
in NetScore.
"""

import json
import os
import sys

import pandas as pd
import psycopg2

ROLLING_WINDOW = 10
ROLLING_MIN_PERIODS = 5

# How many recent trading days a price/variation-rate CHART plots (not the
# correlation study itself, which wants the full history). Keeps each
# bar/point wide enough to tap on a phone -- see build_series(window=...).
CHART_WINDOW_DAYS = 48

DEFAULT_STOCKS = {
    "2330": "台積電",
    "2454": "聯發科",
    "2317": "鴻海",
    "2308": "台達電",
    "3711": "日月光投控",
    "2408": "南亞科",
}


def fund_flow(holdings, snapshots=None):
    """Cross-ETF variation rate per (stock_code, trade_date), robust to a
    fund missing a day (its scrape failed, or its site still showed the
    previous day's PCF).

    holdings: rows of (ticker, stock_code, trade_date, shares).
    snapshots: (ticker, trade_date) of every saved fund snapshot. Defaults
    to the pairs found in `holdings`, which is only correct when
    `holdings` is UNFILTERED (every snapshot has at least one holding
    row) -- a caller that queried holdings for a few stock codes only
    must pass the real snapshot list, or a fund that simply doesn't hold
    the stock would look like a missing snapshot.

    Each fund is compared against ITS OWN previous snapshot, not against
    the previous calendar date:
      - fund missing on day D: its last real snapshot (normally D-1)
        stands in for D, i.e. "no buying or selling that day" -- it stays
        in D's numerator and denominator with unchanged shares, so there
        is no phantom sell on D. The stand-in is computed here only;
        nothing is written to the database.
      - next day D+1 with real data again: that fund compares D+1 against
        its last REAL snapshot (D-1), so its real D-1 -> D+1 change is
        counted on D+1 -- the stand-in corrects itself, nothing is lost
        and there is no phantom buy.
    Fund disclosures are full holdings lists (not top-N), so a stock
    absent from a fund's snapshot really is 0 shares in that fund.

    With complete data this equals the plain "sum today / sum yesterday
    - 1" except that a stock every fund sold out of now shows -100% on
    that day (the plain sum had no row at all that day, so it was
    invisible), and a brand-new position (previous total 0) is NaN.

    Returns columns stock_code, trade_date, cur_shares, prev_shares (both
    including stand-in shares), variation_rate (NaN when prev_shares ==
    0), carried_shares (the stand-in part) and incomplete (True when a
    fund holding this stock is missing that day -- see missing_fund_days()).
    """
    if snapshots is None:
        snapshots = holdings[["ticker", "trade_date"]]
    snaps = snapshots[["ticker", "trade_date"]].drop_duplicates().sort_values(["ticker", "trade_date"])
    snaps["prev_date"] = snaps.groupby("ticker")["trade_date"].shift(1)
    pairs = snaps.dropna(subset=["prev_date"])

    pos = holdings.groupby(["ticker", "trade_date", "stock_code"], as_index=False)["shares"].sum()

    # Stand-in rows for missing funds: the stocks each one held at its
    # last snapshot before the missing day, with those shares.
    missing = missing_fund_days(snaps)
    if missing.empty:
        carried = pd.DataFrame(columns=["stock_code", "trade_date", "carried_shares"])
    else:
        missing = missing.sort_values("trade_date")
        last_before = pd.merge_asof(
            missing,
            snaps[["ticker", "trade_date"]].rename(columns={"trade_date": "last_date"}).sort_values("last_date"),
            left_on="trade_date",
            right_on="last_date",
            by="ticker",
            allow_exact_matches=False,
        )
        carried = (
            last_before.merge(pos.rename(columns={"trade_date": "last_date"}), on=["ticker", "last_date"])
            .groupby(["stock_code", "trade_date"], as_index=False)["shares"].sum()
            .rename(columns={"shares": "carried_shares"})
        )
    carried = carried.assign(incomplete=True)

    cur = pairs.merge(pos, on=["ticker", "trade_date"]).rename(columns={"shares": "cur_shares"})
    prev = pairs.merge(
        pos.rename(columns={"trade_date": "prev_date", "shares": "prev_shares"}),
        on=["ticker", "prev_date"],
    )
    both = cur.merge(prev, on=["ticker", "trade_date", "prev_date", "stock_code"], how="outer")
    both[["cur_shares", "prev_shares"]] = both[["cur_shares", "prev_shares"]].fillna(0.0)

    out = both.groupby(["stock_code", "trade_date"], as_index=False)[["cur_shares", "prev_shares"]].sum()
    out = out.merge(carried, on=["stock_code", "trade_date"], how="outer")
    out[["cur_shares", "prev_shares", "carried_shares"]] = (
        out[["cur_shares", "prev_shares", "carried_shares"]].astype(float).fillna(0.0)
    )
    out["incomplete"] = out["incomplete"].fillna(False).astype(bool)
    out["cur_shares"] += out["carried_shares"]
    out["prev_shares"] += out["carried_shares"]
    out["variation_rate"] = (out["cur_shares"] / out["prev_shares"] - 1).where(out["prev_shares"] > 0)
    return out.sort_values(["stock_code", "trade_date"]).reset_index(drop=True)


# A fund whose latest snapshot is within this many of the most recent
# dates counts as "still active", so a gap at the very end (failed last
# night, nothing newer yet) is reported as missing. A fund with no
# snapshot for longer than this is treated as discontinued (00991A, frozen
# since 2026-07-20 by a WAF block) and is not flagged forever.
ACTIVE_FUND_LOOKBACK_DATES = 20


def missing_fund_days(snapshots, lookback=ACTIVE_FUND_LOOKBACK_DATES):
    """(ticker, trade_date) pairs where a fund should have a snapshot but
    doesn't: any dataset date between the fund's first snapshot and its
    last one (or the latest dataset date, if the fund is still active)
    with no snapshot of its own."""
    snaps = snapshots[["ticker", "trade_date"]].drop_duplicates()
    all_dates = sorted(snaps["trade_date"].unique())
    if not all_dates:
        return pd.DataFrame(columns=["ticker", "trade_date"])
    recent_cutoff = all_dates[-lookback] if len(all_dates) >= lookback else all_dates[0]
    have = set(zip(snaps["ticker"], snaps["trade_date"]))
    rows = []
    for ticker, g in snaps.groupby("ticker"):
        first, last = g["trade_date"].min(), g["trade_date"].max()
        end = all_dates[-1] if last >= recent_cutoff else last
        rows += [(ticker, d) for d in all_dates if first <= d <= end and (ticker, d) not in have]
    return pd.DataFrame(rows, columns=["ticker", "trade_date"])


def fetch(conn_str, stock_codes):
    """Returns (holdings, prices, snapshots). holdings is filtered to
    `stock_codes`, so pass `snapshots` to fund_flow() alongside it."""
    conn = psycopg2.connect(conn_str)

    holdings = pd.read_sql(
        """
        SELECT s.ticker, h.stock_code, s.data_date AS trade_date, h.shares
        FROM etf_holding h
        JOIN etf_snapshot s ON s.id = h.snapshot_id
        WHERE h.stock_code = ANY(%(codes)s)
        """,
        conn,
        params={"codes": list(stock_codes)},
    )
    snapshots = pd.read_sql("SELECT ticker, data_date AS trade_date FROM etf_snapshot", conn)
    prices = pd.read_sql(
        """
        SELECT stock_code, trade_date, price AS close
        FROM stock_price
        WHERE stock_code = ANY(%(codes)s)
        """,
        conn,
        params={"codes": list(stock_codes)},
    )
    conn.close()

    holdings["trade_date"] = pd.to_datetime(holdings["trade_date"])
    holdings["shares"] = holdings["shares"].astype(float)
    prices["trade_date"] = pd.to_datetime(prices["trade_date"])
    prices["close"] = prices["close"].astype(float)
    snapshots["trade_date"] = pd.to_datetime(snapshots["trade_date"])
    today = pd.Timestamp.now(tz="Asia/Taipei").normalize().tz_localize(None)
    snapshots = snapshots[snapshots["trade_date"] <= today]
    return holdings, prices, snapshots


def shared_chart_axis(holdings, prices, window=CHART_WINDOW_DAYS):
    """The last `window` calendar dates seen in EITHER holdings or prices,
    for passing as build_series(..., date_axis=...) so every stock in a
    multi-panel chart plots against the same x-axis. Union of both tables
    (not holdings alone) so a day where only price has posted yet isn't
    dropped from the shared window.
    """
    all_dates = sorted(set(holdings["trade_date"].unique()) | set(prices["trade_date"].unique()))
    return all_dates[-window:]


def build_series(holdings, prices, code, window=None, date_axis=None, flow=None):
    """window: if given, keep only the most recent `window` rows -- rolling
    correlation callers need the full history and leave this at None; chart
    callers pass a window so a stock with months of accumulated history
    doesn't get plotted as dozens of hairline-thin, near-untappable bars.

    date_axis: if given (a sequence of dates), reindex onto it instead of
    using this stock's own tail(window). Charts that plot several stocks
    side by side need every stock on the SAME calendar x-axis -- letting
    each stock independently tail() its own sparser-or-denser history
    stretches each one across the full chart width on its own schedule,
    so the same pixel column ends up meaning a different calendar date in
    every panel. Passing the shared axis here makes a stock's chart show
    an actual gap (null) for a date it has no data on, rather than
    silently compressing its history to fill the width. Takes precedence
    over `window` when both are given.

    flow: fund_flow() output. Pass it when calling this for many stocks
    (computed once), and always when `holdings` is filtered to a few
    stock codes (fund_flow() then needs the real snapshot list).
    """
    if flow is None:
        flow = fund_flow(holdings)
    h = holdings[holdings["stock_code"] == code]
    f = flow[flow["stock_code"] == code].set_index("trade_date").sort_index()
    # Displayed shares: plain total of the funds that reported that day;
    # a day where every holder reported 0 (sold out) gets an explicit 0
    # rather than no row, so the chart doesn't keep showing stale shares.
    # A day where a fund holding this stock is missing adds that fund's
    # stand-in (last real) shares, so the chart doesn't dip.
    combined = h.groupby("trade_date")["shares"].sum().sort_index()
    combined = combined.add(f["carried_shares"][f["carried_shares"] > 0], fill_value=0)
    combined = combined.combine_first(f["cur_shares"][f["cur_shares"] == 0]).sort_index()
    variation_rate = f["variation_rate"]

    p = prices[prices["stock_code"] == code].drop_duplicates("trade_date").set_index("trade_date")["close"].sort_index()
    price_return = p.pct_change()

    df = pd.DataFrame(
        {"close": p, "shares": combined, "variation_rate": variation_rate, "price_return": price_return}
    ).dropna(subset=["variation_rate", "price_return"], how="all")
    df["price_return_lead1"] = df["price_return"].shift(-1)
    if date_axis is not None:
        axis = pd.to_datetime(list(date_axis))
        df = df.reindex(axis)
        # Shares are a *state*, not an event: a day with no fresh PCF
        # disclosure doesn't mean holdings vanished, it means they're
        # still whatever they last were. Without this, shares and
        # variation_rate visibly stopped a day or more before price on
        # charts, purely because a fund's PCF happens to trail the
        # exchange's own EOD post -- not because anything reportable
        # changed. Forward-fill from the full pre-window history (so the
        # window's own first day can still inherit a real prior value),
        # and give a no-new-disclosure day (no fund_flow row, but the
        # stock was already held on an EARLIER date) 0% rather than a gap.
        filled_full = combined.reindex(combined.index.union(axis)).ffill()
        df["shares"] = filled_full.reindex(axis)
        var = variation_rate.reindex(axis)
        no_row = ~axis.isin(variation_rate.index)
        held_before = filled_full.shift(1).reindex(axis).notna().to_numpy()
        var[no_row & held_before] = 0.0
        df["variation_rate"] = var
    elif window is not None:
        df = df.tail(window)
    return df


def _rolling_bivariate(s1, s2, method):
    """Rolling correlation between two aligned series, windowed manually
    since pandas' built-in Rolling.corr() only does Pearson. Spearman is
    Pearson-on-ranks, computed with ranks taken *within each window* (not
    globally, which would not be equivalent) -- avoids adding scipy for
    what pandas' own .rank() already does exactly.
    """
    out = []
    for i in range(len(s1)):
        lo = max(0, i - ROLLING_WINDOW + 1)
        a, b = s1.iloc[lo : i + 1], s2.iloc[lo : i + 1]
        mask = a.notna() & b.notna()
        a, b = a[mask], b[mask]
        if len(a) < ROLLING_MIN_PERIODS or a.nunique() < 2 or b.nunique() < 2:
            out.append(float("nan"))
            continue
        if method == "spearman":
            a, b = a.rank(), b.rank()
        out.append(a.corr(b))
    return pd.Series(out, index=s1.index)


def rolling_corr(df):
    out = pd.DataFrame(index=df.index)
    for method in ("pearson", "spearman"):
        out[f"same_day_{method}"] = _rolling_bivariate(df["variation_rate"], df["price_return"], method)
        out[f"lead1_{method}"] = _rolling_bivariate(df["variation_rate"], df["price_return_lead1"], method)
    out["n_variation_days"] = df["variation_rate"].notna().rolling(ROLLING_WINDOW, min_periods=1).sum()
    return out


def main():
    conn_str = os.environ["DATABASE_URL"].strip().lstrip("﻿")
    stocks = DEFAULT_STOCKS

    holdings, prices, snapshots = fetch(conn_str, stocks.keys())
    print(f"holdings rows: {len(holdings)}, price rows: {len(prices)}")
    flow = fund_flow(holdings, snapshots)

    result = {"window": ROLLING_WINDOW, "min_periods": ROLLING_MIN_PERIODS, "stocks": []}
    for code, name in stocks.items():
        df = build_series(holdings, prices, code, flow=flow)
        corr = rolling_corr(df)
        n_valid = corr[["same_day_spearman"]].dropna().shape[0]
        print(f"{code} {name}: {len(df)} daily rows, {n_valid} rolling-correlation points")

        dates = [d.strftime("%Y-%m-%d") for d in corr.index]

        def col(name_):
            return [None if pd.isna(v) else round(float(v), 3) for v in corr[name_]]

        close = [None if pd.isna(v) else round(float(v), 2) for v in df["close"]]

        result["stocks"].append(
            {
                "stock_code": code,
                "stock_name": name,
                "dates": dates,
                "close": close,
                "same_day_pearson": col("same_day_pearson"),
                "same_day_spearman": col("same_day_spearman"),
                "lead1_pearson": col("lead1_pearson"),
                "lead1_spearman": col("lead1_spearman"),
                "n_variation_days": [int(v) for v in corr["n_variation_days"]],
            }
        )

    out_path = os.path.join(os.path.dirname(__file__), "correlation_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"wrote {out_path}")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
