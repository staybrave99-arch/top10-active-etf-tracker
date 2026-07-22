"""Daily closing price + change% lookup for individual stock holdings.

None of the six fund-company sites reliably expose a stock's price or
day change, so this is sourced independently from the two official
Taiwan exchanges, keyed by stock code:

    TWSE (Taiwan Stock Exchange, main board):
        https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json
        (despite the query string, this actually returns CSV)

    TPEx (Taipei Exchange, OTC board):
        https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes
        (its TLS cert fails Python's strict validation -- same class of
        issue as some fund sites -- so verify=False is used here too)

Together these two cover every holding code seen across all 8 ETFs.
Change% isn't published directly by either exchange -- only the
absolute NTD change -- so it's derived as change / (close - change).
"""

import csv
import io
import time
from decimal import ROUND_HALF_UP, Decimal

import requests
import urllib3

from scraper.utils import clean_number, get_session

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TWSE_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"


def _change_pct(price, change):
    if price is None or change is None:
        return None
    prev_close = price - change
    if prev_close == 0:
        return None
    return ((change / prev_close) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _get_with_retries(session, url, *, retries=3, backoff=3, **kwargs):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise last_exc


def fetch_price_lookup():
    """Return {stock_code: (price, change_pct)} covering TWSE + TPEx."""
    session = get_session()
    lookup = {}

    twse_resp = _get_with_retries(session, TWSE_URL, timeout=60)
    rows = list(csv.reader(io.StringIO(twse_resp.content.decode("utf-8"))))
    for row in rows[1:]:
        if len(row) < 10:
            continue
        code = row[1].strip()
        price = clean_number(row[8])
        if price is None:
            continue
        change = clean_number(row[9])
        lookup[code] = (price, _change_pct(price, change))

    tpex_resp = _get_with_retries(session, TPEX_URL, timeout=60, verify=False)
    for item in tpex_resp.json():
        code = (item.get("SecuritiesCompanyCode") or "").strip()
        if not code or code in lookup:
            continue
        price = clean_number(item.get("Close"))
        if price is None:
            continue
        change = clean_number(item.get("Change"))
        lookup[code] = (price, _change_pct(price, change))

    return lookup
