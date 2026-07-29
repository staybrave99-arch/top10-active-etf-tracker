"""Parser for www.capitalfund.com.tw (群益投信 active ETFs).

The Angular page only server-renders the top 10 holdings; the rest are
loaded into the component's memory on page load and merely hidden
behind a client-side "展開全部" (expand all) toggle -- no additional
network call happens when it's clicked. That full list comes from a
JSON API discovered by reading the site's lazy-loaded chunk for this
page (chunk 44 of main.5239e75b20e72285.js at the time of writing):

    POST https://www.capitalfund.com.tw/CFWeb/api/etf/buyback
    body: {"fundId": "<numeric id from the URL>", "date": null}

The real API host ("/CFWeb") isn't the page's own origin -- it's only
known at runtime via a config file the Angular app fetches itself:
GET /assets/conf/app.json -> {"apiUrl": "https://www.capitalfund.com.tw/CFWeb", ...}
"""

from urllib.parse import urlsplit

from scraper.utils import clean_number, get_session, parse_date

API_URL = "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback"


def _fund_id_from_url(url):
    parts = [p for p in urlsplit(url).path.split("/") if p]
    # .../etf/product/detail/<fundId>/portfolio
    return parts[parts.index("detail") + 1]


def scrape(ticker, url):
    fund_id = _fund_id_from_url(url)
    session = get_session()

    resp = session.post(
        API_URL,
        json={"fundId": fund_id, "date": None},
        headers={"Referer": url, "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json().get("data") or {}

    pcf = payload.get("pcf") or {}
    net_asset = clean_number(pcf.get("nav"))
    # date1 is when the report was generated (effectively "today" if you're
    # looking before the site's ~21:00 daily refresh) -- date2 is the actual
    # PCF data date. Before the refresh, date1 != date2 and using date1
    # mislabels yesterday's still-current data as today's.
    data_date = parse_date(pcf.get("date2")) or parse_date(pcf.get("date1"))

    holdings = []
    for row in payload.get("stocks") or []:
        code = str(row.get("stocNo") or "").strip()
        if not code:
            continue
        holdings.append(
            {
                "stock_code": code,
                "stock_name": (row.get("stocName") or "").strip(),
                "shares": clean_number(row.get("share")),
                "weight_pct": clean_number(row.get("weightRound")),
            }
        )

    return {"net_asset": net_asset, "data_date": data_date, "holdings": holdings}
