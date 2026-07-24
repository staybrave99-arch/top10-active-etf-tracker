import csv
import sys
import time
from urllib.parse import urlparse

from scraper.db import get_conn, init_schema, save_etf_snapshot, save_stock_prices
from scraper.prices import fetch_price_lookup
from scraper.sites import capitalfund, cathay, ezmoney, fhtrust, fsit, nomura
from scraper.utils import today_taipei

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
