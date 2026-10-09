import csv
import sys
import time
from urllib.parse import urlparse

from scraper.db import get_conn, get_latest_snapshot_holdings, init_schema, save_etf_snapshot, save_stock_prices
from scraper.prices import fetch_price_lookup
from scraper.sites import capitalfund, cathay, ezmoney, fhtrust, fsit, nomura
from scraper.utils import expected_trade_date, now_taipei, today_taipei

# The scrape is meant to run once daily after every fund site has posted its
# real end-of-day PCF (observed ~21:00 Asia/Taipei) -- GitHub Actions' cron
# targets 23:00 (moved from the original 22:00 so more sites have already
# refreshed by scrape time; some were still showing yesterday's PCF at
# 22:00, which briefly under-counted their holdings in the cross-ETF
# variation-rate calc until the site caught up the next day -- see
# analysis/top_movers_study.py's settled-date fix). Running well outside
# this window doesn't corrupt data (capitalfund.com.tw's date2 field is
# used specifically so it can't), but it can mean some sites haven't
# refreshed yet, so flag it loudly rather than silently saving
# early/stale-looking data.
SCRAPE_WINDOW_START = (23, 0)
SCRAPE_WINDOW_END = (23, 50)


def _check_scrape_window():
    now = now_taipei()
    start = now.replace(hour=SCRAPE_WINDOW_START[0], minute=SCRAPE_WINDOW_START[1], second=0, microsecond=0)
    end = now.replace(hour=SCRAPE_WINDOW_END[0], minute=SCRAPE_WINDOW_END[1], second=0, microsecond=0)
    if not (start <= now <= end):
        print(
            f"[WARN] running at {now.strftime('%Y-%m-%d %H:%M %Z')}, outside the intended "
            f"{SCRAPE_WINDOW_START[0]:02d}:{SCRAPE_WINDOW_START[1]:02d}-"
            f"{SCRAPE_WINDOW_END[0]:02d}:{SCRAPE_WINDOW_END[1]:02d} Asia/Taipei scrape window -- "
            f"some fund sites may not have posted today's real data yet"
        )

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


def load_etf_list(csv_path):
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def attach_prices(holdings, price_lookup):
    for h in holdings:
        price, change_pct = price_lookup.get(h["stock_code"], (None, None))
        h["price"] = price
        h["change_pct"] = change_pct


def main():
    _check_scrape_window()

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

    failures = []
    held_codes = set()
    for row in rows:
        ticker = row["代號"].strip()
        name = row["ETF名稱"].strip()
        url = row["URL"].strip()
        domain = urlparse(url).netloc

        if ticker in SKIP_TICKERS:
            print(f"[SKIP] {ticker} {name}: known-blocked, see SKIP_TICKERS")
            continue

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
            data_date = result["data_date"] or today_taipei()
            # Last-resort guard: a data_date can never legitimately be
            # later than the trade date this scrape window could
            # plausibly already have a PCF for. Each parser already
            # tries to reject an outright future date itself (e.g.
            # capitalfund.py's date1/date2-vs-today check), but that's
            # proven not fully reliable -- a future-dated snapshot from
            # 00982A/00992A has recurred more than once despite it (see
            # analysis notes), each time hijacking every latest-date pick
            # downstream until manually deleted.
            #
            # expected_trade_date() pins the cutoff to hour-of-day
            # (~22:00) rather than calendar date, which is what makes
            # this reliable: capitalfund.com.tw has been observed
            # advancing its *displayed* date label to the next trading
            # day right after midnight while the PCF content underneath
            # is unchanged, and separately GH Actions cron drift has
            # pushed a nominally-23:00 run past midnight by itself (once
            # by ~5.5h) -- either way, clamping to expected_trade_date()
            # (not today_taipei(), which would itself already be
            # "tomorrow" in both cases) recovers the correct trade date.
            expected = expected_trade_date()
            if data_date > expected:
                print(
                    f"[WARN] {ticker}: parser returned data_date={data_date}, ahead of this "
                    f"scrape window's expected trade date {expected} -- clamping down (the "
                    f"site has likely already advanced its displayed date label to the next "
                    f"trading day)"
                )
                data_date = expected

            # Second, separate guard: expected_trade_date() only fixes
            # which date a real new PCF belongs to -- it doesn't know
            # whether this scrape actually ran on a non-trading day
            # (a holiday, or anything else where the site simply had no
            # fresh PCF to publish). On that kind of day, the scrape
            # still succeeds and returns last trading day's holdings
            # again, just now labeled under a new date. Saving that as
            # a distinct snapshot would create a duplicate "trading day"
            # with identical content, which skews every day-over-day
            # comparison downstream (variation_rate, buy/sell streaks)
            # even though nothing changed. So before saving under a
            # *different* date than this ticker's latest snapshot, check
            # whether the holdings are actually unchanged -- if so,
            # there's nothing new to record.
            latest = get_latest_snapshot_holdings(conn, ticker)
            new_holdings = {h["stock_code"]: h["shares"] for h in result["holdings"]}
            if latest is not None and latest["data_date"] != data_date and latest["holdings"] == new_holdings:
                print(
                    f"[SKIP] {ticker}: holdings unchanged from the {latest['data_date']} "
                    f"snapshot -- no fresh PCF (likely a non-trading day), not saving a "
                    f"duplicate under {data_date}"
                )
            else:
                snapshot_id = save_etf_snapshot(
                    conn, ticker, name, data_date, result["net_asset"], result["holdings"]
                )
                print(
                    f"[SAVED] {ticker} snapshot_id={snapshot_id} "
                    f"data_date={data_date} net_asset={result['net_asset']} "
                    f"holdings={len(result['holdings'])}"
                )
        except Exception as exc:
            print(f"[ERROR] {ticker} {name}: {exc}")
            failures.append(ticker)

        time.sleep(1.5)

    if held_codes and price_trade_date:
        price_rows = [
            (code, *price_lookup[code]) for code in held_codes if code in price_lookup
        ]
        save_stock_prices(conn, price_trade_date, price_rows)
        print(f"[PRICES] saved {len(price_rows)} stock_price rows for {price_trade_date}")

    conn.close()

    if failures:
        print(f"[DONE] finished with {len(failures)} failure(s): {failures}")
        sys.exit(1)

    print("[DONE] all ETFs scraped successfully")


if __name__ == "__main__":
    main()
