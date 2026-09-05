"""One-off: dump the raw pcf date fields from capitalfund.com.tw's API to
figure out what date1/date2 actually mean -- the _diag_date_lag.py check
found 00982A/00992A's stored data_date running *ahead* of real calendar
time (e.g. 2026-09-07 when today is 2026-09-05), which the code currently
attributes to date2 being "the actual PCF data date". That assumption
looks wrong; this prints the raw payload to see what's really there.
"""
import json

from scraper.sites.capitalfund import API_URL, _fund_id_from_url
from scraper.utils import get_session

URLS = {
    "00982A": "https://www.capitalfund.com.tw/etf/product/detail/399/portfolio",
    "00992A": "https://www.capitalfund.com.tw/etf/product/detail/500/portfolio",
}


def main():
    session = get_session()
    for ticker, url in URLS.items():
        fund_id = _fund_id_from_url(url)
        resp = session.post(
            API_URL,
            json={"fundId": fund_id, "date": None},
            headers={"Referer": url, "Content-Type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json().get("data") or {}
        pcf = payload.get("pcf") or {}
        print(f"\n=== {ticker} (fundId={fund_id}) ===")
        print("pcf keys/values (excluding stocks):")
        print(json.dumps({k: v for k, v in pcf.items()}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
