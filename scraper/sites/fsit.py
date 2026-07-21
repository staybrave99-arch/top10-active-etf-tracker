"""Parser for websys.fsit.com.tw (富邦投信 active ETFs).

Plain server-rendered ASP.NET HTML. "資料日期" and "基金淨資產(新台幣)"
appear as labelled text near the top; holdings are in a table under the
"股票" <h6> heading.
"""

import re

import urllib3
from bs4 import BeautifulSoup

from scraper.utils import clean_number, fetch_html, parse_date

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def scrape(ticker, url):
    html = fetch_html(url, verify=False)
    soup = BeautifulSoup(html, "lxml")

    data_date = None
    m = re.search(r"資料日期[：:]\s*([0-9]{4}/[0-9]{1,2}/[0-9]{1,2})", html)
    if m:
        data_date = parse_date(m.group(1))

    net_asset = None
    for p in soup.find_all("p"):
        if "基金淨資產" in p.get_text():
            sibling = p.find_next_sibling("p")
            if sibling is not None:
                net_asset = clean_number(sibling.get_text())
                break

    holdings = []
    heading = soup.find(lambda tag: tag.name == "h6" and tag.get_text(strip=True) == "股票")
    table = heading.find_next("table") if heading is not None else soup.find("table", class_="table1")

    if table is not None:
        for tr in table.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            code = tds[0].get_text(strip=True)
            if not re.fullmatch(r"[0-9A-Za-z]+", code):
                continue
            holdings.append(
                {
                    "stock_code": code,
                    "stock_name": tds[1].get_text(strip=True),
                    "shares": clean_number(tds[2].get_text()),
                    "weight_pct": clean_number(tds[4].get_text()),
                }
            )

    return {"net_asset": net_asset, "data_date": data_date, "holdings": holdings}
