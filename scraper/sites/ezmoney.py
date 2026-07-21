"""Parser for www.ezmoney.com.tw (統一投信 active ETFs).

The fund detail page embeds a full JSON payload in
<div id="DataAsset" data-content="[...]">. Each entry is one asset-class
row (NAV, outstanding units, per-unit NAV, stock holdings, ...); the
stock-holdings row (AssetCode == "ST") carries the individual positions
in its "Details" list.
"""

import json

from bs4 import BeautifulSoup

from scraper.utils import clean_number, fetch_html, parse_date


def scrape(ticker, url):
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    asset_div = soup.find("div", id="DataAsset")
    if asset_div is None or not asset_div.get("data-content"):
        raise ValueError("DataAsset block not found")
    data = json.loads(asset_div["data-content"])

    net_asset = None
    for entry in data:
        if entry.get("AssetName") == "淨資產":
            net_asset = clean_number(entry.get("Value"))
            break

    holdings = []
    data_date = None
    for entry in data:
        if entry.get("AssetCode") != "ST":
            continue
        for detail in entry.get("Details") or []:
            code = (detail.get("DetailCode") or "").strip()
            name = (detail.get("DetailName") or "").strip()
            if not code:
                continue
            holdings.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "shares": clean_number(detail.get("Share")),
                    "weight_pct": clean_number(detail.get("NavRate")),
                }
            )
            if data_date is None:
                data_date = parse_date(detail.get("TranDate"))

    return {"net_asset": net_asset, "data_date": data_date, "holdings": holdings}
