"""Parser for www.fhtrust.com.tw (復華投信 active ETFs).

The static server-rendered page shows a "資料日期" and a NAV figure, but
that NAV turns out to be a stale/different number from the fund's real
current PCF (Portfolio Composition File) NAV, and the visible stock
table is capped at the top 10 holdings. The actual, complete, correct
data (matching what the browser shows once the page's JS runs) comes
from a separate JSON API, found via the browser's Network tab:

    https://www.fhtrust.com.tw/api/assets?fundID=<ETFxx>&qDate=<yyyy/mm/dd>

`fundID` is the short code embedded in the page URL itself (e.g.
"ETF23" for 00991A) -- no separate lookup needed. `qDate` should be the
PCF's actual publish date, which the "資料日期" text on the static page
usually names -- but that label can run ahead of the API (e.g. it
flips to "today" once the market closes, while today's PCF isn't
actually published until sometime after that), in which case the API
responds with one result item whose fields are all null rather than
an error. So querying walks backwards a few calendar days from the
labelled date until it finds one that actually has data.
"""

import re
from datetime import timedelta
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from scraper.utils import clean_number, fetch_html, get_session, parse_date

API_URL = "https://www.fhtrust.com.tw/api/assets"
MAX_DATE_LOOKBACK_DAYS = 6


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


def _fetch_assets(session, page_url, fund_code, query_date):
    resp = session.get(
        API_URL,
        params={"fundID": fund_code, "qDate": query_date.strftime("%Y/%m/%d")},
        headers={"Referer": page_url, "Accept": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    result = resp.json().get("result") or []
    if not result:
        return None
    item = result[0]
    if item.get("pcf_FundNav") is None:
        return None
    return item


def scrape(ticker, url):
    page_url = url.split("#")[0]
    fund_code = _fund_code_from_url(page_url)

    html = fetch_html(page_url)
    soup = BeautifulSoup(html, "lxml")
    labelled_date = _find_data_date(soup)
    if labelled_date is None:
        raise ValueError("could not find 資料日期 on fhtrust page")

    session = get_session()
    item = None
    data_date = None
    for offset in range(MAX_DATE_LOOKBACK_DAYS + 1):
        candidate = labelled_date - timedelta(days=offset)
        item = _fetch_assets(session, page_url, fund_code, candidate)
        if item is not None:
            data_date = candidate
            break

    if item is None:
        raise ValueError(
            f"fhtrust /api/assets returned no data for {fund_code} within "
            f"{MAX_DATE_LOOKBACK_DAYS} days of {labelled_date}"
        )

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
