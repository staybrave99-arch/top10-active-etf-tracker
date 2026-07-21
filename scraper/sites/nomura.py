"""Parser for www.nomurafunds.com.tw (野村投信 active ETFs).

Also an Angular SPA with no server-rendered data. The real data comes
from a JSON API discovered in the site's main.js bundle:

    POST /API/ETFAPI/api/Fund/GetFundTradeInfoDate  -> latest available date
    POST /API/ETFAPI/api/Fund/GetFundTradeInfo       -> NAV + holdings, for a given date

The ticker itself ("00980A") is the FundNo the API expects, so no code
mapping is needed. The site's TLS certificate fails Python's stricter
validation (missing Subject Key Identifier), so requests are made with
verify=False.
"""

import urllib3

from scraper.utils import clean_number, get_session, parse_date

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_BASE = "https://www.nomurafunds.com.tw/API/ETFAPI/api/"


def _headers(ticker):
    return {
        "Content-Type": "application/json",
        "Referer": f"https://www.nomurafunds.com.tw/ETFWEB/product-description?fundNo={ticker}&tab=Shareholding",
    }


def scrape(ticker, url):
    session = get_session()
    headers = _headers(ticker)

    date_resp = session.post(
        API_BASE + "Fund/GetFundTradeInfoDate",
        json={"FundNo": ticker, "Type": 1, "Keyword": "", "Date": None},
        headers=headers,
        timeout=30,
        verify=False,
    )
    date_resp.raise_for_status()
    latest_date = (date_resp.json().get("Entries") or {}).get("LatestDate")

    info_resp = session.post(
        API_BASE + "Fund/GetFundTradeInfo",
        json={"FundNo": ticker, "Type": 1, "Keyword": "", "Date": latest_date},
        headers=headers,
        timeout=30,
        verify=False,
    )
    info_resp.raise_for_status()
    entries = info_resp.json().get("Entries") or {}

    net_asset = clean_number(entries.get("CAnceTotalAv"))
    data_date = parse_date(entries.get("CNavDtStr")) or parse_date(latest_date)

    holdings = []
    for item in entries.get("Stocks") or []:
        code = (item.get("CStockCode") or "").strip()
        if not code:
            continue
        holdings.append(
            {
                "stock_code": code,
                "stock_name": (item.get("CStockName") or "").strip(),
                "shares": clean_number(item.get("CQuantity")),
                "weight_pct": clean_number(item.get("CWeightsPct")),
            }
        )

    return {"net_asset": net_asset, "data_date": data_date, "holdings": holdings}
