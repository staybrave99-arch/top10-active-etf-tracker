"""Parser for www.cathaysite.com.tw (國泰投信 active ETFs).

This is an Angular SPA; the rendered HTML carries no fund data at all.
The real data comes from a JSON API on a separate host, discovered by
reading the site's lazy-loaded route chunk (not main.js -- the ETF
detail page's component code, and therefore its API calls, live in a
separate webpack chunk fetched at runtime):

    https://cwapi.cathaysite.com.tw/api/ETF/GetETFAssets            -> NAV + as-of date
    https://cwapi.cathaysite.com.tw/api/ETF/GetETFDetailStockList   -> stock code/name/shares/weight
    https://cwapi.cathaysite.com.tw/api/ETF/GetETFList              -> maps stockCode -> internal fundCode

The API's own "fundCode" is a short internal slug (e.g. "EA" for
00400A), not the public ticker, so it must be resolved via GetETFList
first. GetETFDetailStockList additionally requires a "SearchDate"
(the NAV as-of date from GetETFAssets, formatted yyyy-MM-dd) -- without
it the endpoint returns no data at all.
"""

from scraper.utils import clean_number, get_session, parse_date

API_BASE = "https://cwapi.cathaysite.com.tw/"

# Fallback in case GetETFList pagination ever fails to surface a ticker.
KNOWN_FUND_CODES = {"00400A": "EA"}


def _headers(ticker):
    return {
        "Referer": f"https://www.cathaysite.com.tw/ETF/detail/{ticker}?tab=etf3",
        "Accept": "application/json, text/plain, */*",
    }


def _resolve_fund_code(session, ticker):
    for page in range(1, 11):
        resp = session.get(
            API_BASE + "api/ETF/GetETFList",
            params={"status": 1, "pageSize": 100, "pageIndex": page},
            headers=_headers(ticker),
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result") or []
        for item in result:
            if item.get("stockCode") == ticker:
                return item.get("fundCode")
        if page >= (data.get("totalPage") or 1):
            break
    return KNOWN_FUND_CODES.get(ticker)


def scrape(ticker, url):
    session = get_session()
    fund_code = _resolve_fund_code(session, ticker)
    if not fund_code:
        raise ValueError(f"could not resolve cathaysite internal fundCode for {ticker}")

    headers = _headers(ticker)

    assets_resp = session.get(
        API_BASE + "api/ETF/GetETFAssets",
        params={"FundCode": fund_code},
        headers=headers,
        timeout=30,
    )
    assets_resp.raise_for_status()
    assets = assets_resp.json().get("result") or {}
    net_asset = clean_number(assets.get("fundNav"))
    pre_date = assets.get("preDate")
    data_date = parse_date(pre_date)

    search_date = pre_date.replace("/", "-") if pre_date else None
    stock_resp = session.get(
        API_BASE + "api/ETF/GetETFDetailStockList",
        params={"FundCode": fund_code, "SearchDate": search_date},
        headers=headers,
        timeout=30,
    )
    stock_resp.raise_for_status()
    stock_list = stock_resp.json().get("result") or []

    holdings = []
    for item in stock_list:
        code = (item.get("stockCode") or "").strip()
        if not code:
            continue
        holdings.append(
            {
                "stock_code": code,
                "stock_name": (item.get("stockName") or "").strip(),
                "shares": clean_number(item.get("volumn")),
                "weight_pct": clean_number(item.get("weights")),
            }
        )

    return {"net_asset": net_asset, "data_date": data_date, "holdings": holdings}
