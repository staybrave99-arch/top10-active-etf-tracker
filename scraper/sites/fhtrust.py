"""Parser for www.fhtrust.com.tw (復華投信 active ETFs).

Server-rendered plain HTML. Net asset value lives in a
"基金淨資產價值(元)" row of a fundInformationTable; individual holdings
live in a table with class "etfStockTable" under the "股票" heading.
"""

import re

from bs4 import BeautifulSoup

from scraper.utils import clean_number, fetch_html, parse_date


def scrape(ticker, url):
    url = url.split("#")[0]
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    net_asset = None
    for th in soup.find_all(["th"]):
        if "基金淨資產價值" in th.get_text():
            td = th.find_next_sibling("td")
            if td is not None:
                net_asset = clean_number(td.get_text())
                break

    data_date = None
    for m in re.finditer(r"資料日期[：:]\s*([0-9]{4}/[0-9]{1,2}/[0-9]{1,2})", html):
        d = parse_date(m.group(1))
        if d and (data_date is None or d > data_date):
            data_date = d

    holdings = []
    heading = soup.find(lambda tag: tag.name == "h2" and tag.get_text(strip=True) == "股票")
    table = None
    if heading is not None:
        section = heading.find_parent("section")
        if section is not None:
            table = section.find("table", class_="etfStockTable")
    if table is None:
        table = soup.find("table", class_="etfStockTable")

    if table is not None:
        for tr in table.select("tbody tr"):
            tds = tr.find_all("td")
            if len(tds) < 5:
                continue
            code = tds[0].get_text(strip=True)
            if not code:
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
