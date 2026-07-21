"""Parser for www.fhtrust.com.tw (復華投信 active ETFs).

The static server-rendered page shows a "資料日期" and a NAV figure, but
that NAV turns out to be a stale/different number from the fund's real
current PCF (Portfolio Composition File) NAV, and the visible stock
table is capped at the top 10 holdings. The actual, complete, correct
data (matching what the browser shows once the page's JS runs) comes
from a separate JSON API, found via the browser's Network tab:

    https://www.fhtrust.com.tw/api/assets?fundID=<ETFxx>&qDate=<yyyy/mm/dd>

`fundID` is the short code embedded in the page URL itself (e.g.
"ETF23" for 00991A) -- no separate lookup needed. `qDate` must be the
PCF's actual publish date (there's a T+1-ish lag: querying "today"
returns nothing); the "資料日期" text on the static page reliably names
that publish date, so it's used to build the query.
"""

import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from scraper.utils import clean_number, fetch_html, get_session, parse_date

API_URL = "https://www.fhtrust.com.tw/api/assets"


def _fund_code_from_url(url):
    path = urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1]


def _find_data_date(soup):
    data_date = None
    for tag in soup.find_all(string=re.compile("資料日期")):
        container = tag.find_parent(["p", "div"]) or tag
        m = re.search(r"([0-9]{4}/[0-9]{1,2}/[0-9]{1,2})", container.get_text())
        if not m:
            continue
        d = parse_date(m.group(1))
        if d and (data_date is None or d > data_date):
            data_date = d
    return data_date


def scrape(ticker, url):
    page_url = url.split("#")[0]
    fund_code = _fund_code_from_url(page_url)

    html = fetch_html(page_url)
    soup = BeautifulSoup(html, "lxml")
    data_date = _find_data_date(soup)
    if data_date is None:
        raise ValueError("could not find 資料日期 on fhtrust page")

    session = get_session()
    resp = session.get(
        API_URL,
        params={"fundID": fund_code, "qDate": data_date.strftime("%Y/%m/%d")},
        headers={"Referer": page_url, "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    result = payload.get("result") or []
    if not result:
        raise ValueError(f"fhtrust /api/assets returned no data for {fund_code} on {data_date}")
    item = result[0]

    net_asset = clean_number(item.get("pcf_FundNav"))

    holdings = []
    for row in item.get("detail") or []:
        if row.get("ftype") != "股票":
            continue
        code = (row.get("stockid") or "").strip()
        if not code:
            continue
        holdings.append(
            {
                "stock_code": code,
                "stock_name": (row.get("stockname") or "").strip(),
                "shares": clean_number(row.get("qshare")),
                "weight_pct": clean_number(row.get("prate_addaccint")),
            }
        )

    return {"net_asset": net_asset, "data_date": data_date, "holdings": holdings}
