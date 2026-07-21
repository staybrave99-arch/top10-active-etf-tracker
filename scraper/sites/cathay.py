"""Parser for www.cathaysite.com.tw (國泰投信 active ETFs).

This is an Angular SPA; the rendered HTML carries no fund data at all.
The real data comes from a JSON API on a separate host, discovered by
reading the site's main.js bundle:

    https://cwapi.cathaysite.com.tw/api/ETF/GetETFAssets            -> NAV + as-of date
    https://cwapi.cathaysite.com.tw/api/ETF/GetIndexStockWeights    -> stock code/name/weight
    https://cwapi.cathaysite.com.tw/api/ETF/GetETFList              -> maps stockCode -> internal fundCode

The API's own "fundCode" is a short internal slug (e.g. "EA" for
00400A), not the public ticker, so it must be resolved via GetETFList
first. GetIndexStockWeights does not carry share counts, so "shares" is
always None for this source.
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
        params={"fundCode": fund_code, "status": 1},
        headers=headers,
        timeout=30,
    )
    assets_resp.raise_for_status()
    assets = assets_resp.json().get("result") or {}
    net_asset = clean_number(assets.get("fundNav"))
    data_date = parse_date(assets.get("preDate"))

    weights_resp = session.get(
        API_BASE + "api/ETF/GetIndexStockWeights",
        params={"fundCode": fund_code, "status": 1},
        headers=headers,
        timeout=30,
    )
    weights_resp.raise_for_status()
    weights = weights_resp.json().get("result") or {}

    holdings = []
    for item in weights.get("stockWeights") or []:
        code = (item.get("stockCode") or "").strip()
        if not code:
            continue
        holdings.append(
            {
                "stock_code": code,
                "stock_name": (item.get("stockName") or "").strip(),
                "shares": None,
                "weight_pct": clean_number(item.get("weights")),
            }
        )

    if data_date is None:
        data_date = parse_date(weights.get("date"))

    return {"net_asset": net_asset, "data_date": data_date, "holdings": holdings}
