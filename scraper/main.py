import csv
import sys
import time
from urllib.parse import urlparse

from scraper.db import get_conn, init_schema, save_etf_snapshot
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
        price_lookup = fetch_price_lookup()
        print(f"[PRICES] loaded {len(price_lookup)} stock quotes")
    except Exception as exc:
        print(f"[PRICES] failed to fetch price data, continuing without it: {exc}")
        price_lookup = {}

    failures = []
    for row in rows:
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

    conn.close()

    if failures:
        print(f"[DONE] finished with {len(failures)} failure(s): {failures}")
        sys.exit(1)

    print("[DONE] all ETFs scraped successfully")


if __name__ == "__main__":
    main()
