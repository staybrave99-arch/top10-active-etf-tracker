"""Parser for www.capitalfund.com.tw (群益投信 active ETFs).

Angular SSR page; the holdings table is rendered as nested <div>s (not a
real <table>). Each row lives under div.pct-stock-table-tbody as
div.tr.show-for-medium with four child divs: code, name, weight%, shares.

The page does not expose an explicit "資料日期" label, so callers should
fall back to the scrape date for this source.
"""

from bs4 import BeautifulSoup

from scraper.utils import clean_number, fetch_html


def scrape(ticker, url):
    html = fetch_html(url)
    soup = BeautifulSoup(html, "lxml")

    net_asset = None
    for div in soup.find_all("div", class_="th"):
        if "基金淨資產價值" in div.get_text():
            td = div.find_next_sibling("div", class_="td")
            if td is not None:
                net_asset = clean_number(td.get_text())
                break

    holdings = []
    tbody = soup.find("div", class_="pct-stock-table-tbody")
    if tbody is not None:
        for row in tbody.select("div.tr.show-for-medium"):
            cells = row.find_all("div", recursive=False)
            if len(cells) < 4:
                continue
            code = cells[0].get_text(strip=True)
            if not code:
                continue
            holdings.append(
                {
                    "stock_code": code,
                    "stock_name": cells[1].get_text(strip=True),
                    "shares": clean_number(cells[3].get_text()),
                    "weight_pct": clean_number(cells[2].get_text()),
                }
            )

    return {"net_asset": net_asset, "data_date": None, "holdings": holdings}
