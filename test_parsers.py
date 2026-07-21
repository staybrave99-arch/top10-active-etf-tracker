import csv
from urllib.parse import urlparse

from scraper.sites import capitalfund, cathay, ezmoney, fhtrust, fsit, nomura

DISPATCH = {
    "www.ezmoney.com.tw": ezmoney.scrape,
    "www.fhtrust.com.tw": fhtrust.scrape,
    "www.capitalfund.com.tw": capitalfund.scrape,
    "websys.fsit.com.tw": fsit.scrape,
    "www.cathaysite.com.tw": cathay.scrape,
    "www.nomurafunds.com.tw": nomura.scrape,
}

with open("Top10ActiveETF.csv", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

for row in rows:
    ticker = row["代號"].strip()
    name = row["ETF名稱"].strip()
    url = row["URL"].strip()
    domain = urlparse(url).netloc
    fn = DISPATCH.get(domain)
    print("=" * 60)
    print(ticker, name, domain)
    if fn is None:
        print("NO PARSER")
        continue
    try:
        result = fn(ticker=ticker, url=url)
        print("net_asset:", result["net_asset"])
        print("data_date:", result["data_date"])
        print("holdings count:", len(result["holdings"]))
        for h in result["holdings"][:3]:
            print("   ", h)
    except Exception as e:
        print("ERROR:", repr(e))
