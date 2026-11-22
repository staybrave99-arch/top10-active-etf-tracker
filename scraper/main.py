import csv
import sys
import time
from urllib.parse import urlparse

from scraper.db import (
    get_conn,
    get_held_codes,
    get_snapshot_tickers,
    init_schema,
    save_etf_snapshot,
    save_stock_prices,
)
from scraper.prices import fetch_price_lookup
from scraper.sites import capitalfund, cathay, ezmoney, fhtrust, fsit, nomura
from scraper.utils import expected_trade_date, now_taipei

# Run schedule (.github/workflows/scrape.yml): once per trade date, at
# 04:00 Asia/Taipei the next morning. The run works out the trade date it
# is scraping for (expected_trade_date(), which treats 22:00 as the day
# boundary, so 04:00 files under the previous day), skips every fund that
# already has a snapshot for that date, and scrapes the rest -- normally
# all of them; a manual re-run (workflow_dispatch) only fills the gaps. A
# fund that still can't be read is NOT stood in for here: nothing fake is
# written to the database; the report does the stand-in (see
# analysis/correlation_study.fund_flow()).
#
# Allowed window: from 22:00 (fund sites have posted that day's PCF by
# ~21:00) until 13:00 the next day. The cutoff leaves a wide margin before
# the next evening's PCF goes up: a run at, say, 21:30 the next day would
# still compute the previous trade date but could already be seeing the
# new PCF, filing it under the wrong day. GH Actions cron can drift by
# hours (up to ~5.5h seen), so a drifted run landing past the cutoff skips
# itself instead of risking that.
RUN_WINDOW_START_HOUR = 22
RUN_WINDOW_END_HOUR = 13

DISPATCH = {
    "www.ezmoney.com.tw": ezmoney.scrape,
    "www.fhtrust.com.tw": fhtrust.scrape,
    "www.capitalfund.com.tw": capitalfund.scrape,
    "websys.fsit.com.tw": fsit.scrape,
    "www.cathaysite.com.tw": cathay.scrape,
    "www.nomurafunds.com.tw": nomura.scrape,
}

# www.fhtrust.com.tw drops every connection from fly.io's IP range with no
# HTTP response at all (confirmed directly from the fly.io machine, with
# the exact same request succeeding from other networks) -- looks like a
# WAF rule blocking cloud/datacenter IPs rather than anything fixable on
# our end. Skipping it here instead of letting it fail the whole run every
# day; revisit if a workaround (different egress path, etc.) shows up.
SKIP_TICKERS = {"00991A"}


def in_run_window(now):
    return now.hour >= RUN_WINDOW_START_HOUR or now.hour < RUN_WINDOW_END_HOUR


def plan_tickers(rows, already_saved, skip=frozenset()):
    """Rows from the ETF list still to scrape: not permanently skipped and
    without a snapshot for the target date yet."""
    return [
        row
        for row in rows
        if row["代號"].strip() not in skip and row["代號"].strip() not in already_saved
    ]


def load_etf_list(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def attach_prices(holdings, price_lookup):
    for h in holdings:
        price, change_pct = price_lookup.get(h["stock_code"], (None, None))
        h["price"] = price
        h["change_pct"] = change_pct


def main():
    now = now_taipei()
    if not in_run_window(now):
        print(
            f"[SKIP] {now.strftime('%Y-%m-%d %H:%M %Z')} is outside the "
            f"{RUN_WINDOW_START_HOUR:02d}:00-{RUN_WINDOW_END_HOUR:02d}:00 Asia/Taipei run window "
            f"-- not scraping (the next PCF may already be up)"
        )
        return

    csv_path = sys.argv[1] if len(sys.argv) > 1 else "Top10ActiveETF.csv"
    rows = load_etf_list(csv_path)

    conn = get_conn()
    init_schema(conn)

    print("[PRICES] fetching TWSE/TPEx daily quotes ...")
    try:
        price_lookup, price_trade_date = fetch_price_lookup()
        print(f"[PRICES] loaded {len(price_lookup)} stock quotes for {price_trade_date}")
    except Exception as exc:
        print(f"[PRICES] failed to fetch price data, continuing without it: {exc}")
        price_lookup, price_trade_date = {}, None

    # The trade date this run is scraping for. expected_trade_date() pins
    # the cutoff to hour-of-day (22:00) rather than calendar date:
    # capitalfund.com.tw has been observed advancing its *displayed* date
    # label to the next trading day right after midnight while the PCF
    # content underneath is unchanged, and the scheduled run is after
    # midnight by design -- either way the right trade date is still the
    # previous day's.
    #
    # But hour-of-day alone can't tell a holiday weekday from a real
    # trading day. TWSE/TPEx's daily-quote endpoints keep serving the last
    # session's quotes on non-trading weekdays (see fetch_price_lookup()),
    # so price_trade_date is the exchange calendar's own answer to "what's
    # the most recent real trading day" -- trust it over our own guess
    # whenever it's earlier. (An earlier content-comparison approach was
    # tried and reverted: it couldn't tell a stale duplicate from a
    # low-turnover ETF that genuinely made no changes for a day.)
    expected = expected_trade_date(now)
    if price_trade_date and price_trade_date < expected:
        expected = price_trade_date

    already_saved = get_snapshot_tickers(conn, expected)
    todo = plan_tickers(rows, already_saved, SKIP_TICKERS)
    for row in rows:
        ticker = row["代號"].strip()
        if ticker in SKIP_TICKERS:
            print(f"[SKIP] {ticker} {row['ETF名稱'].strip()}: known-blocked, see SKIP_TICKERS")
        elif ticker in already_saved:
            print(f"[HAVE] {ticker} {row['ETF名稱'].strip()}: already saved for {expected}")
    if todo:
        print(f"[PLAN] scraping {len(todo)} fund(s) for {expected}: {[r['代號'].strip() for r in todo]}")
    else:
        print(f"[PLAN] every fund already has a snapshot for {expected}, nothing to scrape")

    failures = []
    held_codes = set()
    for row in todo:
        ticker = row["代號"].strip()
        name = row["ETF名稱"].strip()
        url = row["URL"].strip()
        domain = urlparse(url).netloc

        scrape_fn = DISPATCH.get(domain)
        if scrape_fn is None:
            print(f"[SKIP] {ticker} {name}: no parser registered for {domain}")
            failures.append(ticker)
            continue

        print(f"[SCRAPE] {ticker} {name} ({domain}) ...")
        try:
            result = scrape_fn(ticker=ticker, url=url)
            if not result["holdings"]:
                raise ValueError("no holdings parsed")
            attach_prices(result["holdings"], price_lookup)
            held_codes.update(h["stock_code"] for h in result["holdings"])
            # Fallback when a parser couldn't read the site's own date:
            # the target trade date, not the wall-clock date (which is
            # already "tomorrow" for any run after midnight).
            data_date = result["data_date"] or expected
            # Last-resort guard: a data_date can never legitimately be
            # later than the trade date this run is scraping for. Each
            # parser already tries to reject an outright future date
            # itself (e.g. capitalfund.py's date1/date2-vs-today check),
            # but that's proven not fully reliable -- a future-dated
            # snapshot from 00982A/00992A has recurred more than once.
            if data_date > expected:
                print(
                    f"[WARN] {ticker}: parser returned data_date={data_date}, ahead of the "
                    f"most recent real trading day {expected} -- clamping down (the site has "
                    f"likely already advanced its displayed date label past the actual PCF)"
                )
                data_date = expected
            snapshot_id = save_etf_snapshot(
                conn, ticker, name, data_date, result["net_asset"], result["holdings"]
            )
            print(
                f"[SAVED] {ticker} snapshot_id={snapshot_id} "
                f"data_date={data_date} net_asset={result['net_asset']} "
                f"holdings={len(result['holdings'])}"
            )
            if data_date < expected:
                # The site is still showing an older PCF (not refreshed
                # yet). Saved under its own older date as before, but this
                # fund is still missing for `expected` -- report it so the
                # run's status says so (the report stands in its previous
                # snapshot and flags it in ntfy).
                print(f"[STALE] {ticker}: site still shows {data_date}, not {expected} yet")
                failures.append(ticker)
        except Exception as exc:
            print(f"[ERROR] {ticker} {name}: {exc}")
            failures.append(ticker)

        time.sleep(1.5)

    if price_trade_date:
        # Every stock held that day, not just the funds scraped in this
        # run -- a manual re-run also fills in prices an earlier run
        # couldn't fetch. Upsert, so repeating is harmless.
        codes = held_codes | get_held_codes(conn, price_trade_date)
        price_rows = [(code, *price_lookup[code]) for code in codes if code in price_lookup]
        save_stock_prices(conn, price_trade_date, price_rows)
        print(f"[PRICES] saved {len(price_rows)} stock_price rows for {price_trade_date}")

    conn.close()

    if failures:
        print(f"[DONE] {len(failures)} fund(s) still missing for {expected}: {failures}")
        sys.exit(1)

    print(f"[DONE] every fund has a snapshot for {expected}")


if __name__ == "__main__":
    main()
