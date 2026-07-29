import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scraper.utils import get_session

print("Taipei time now:", datetime.now(ZoneInfo("Asia/Taipei")))
print()

session = get_session()
for fund_id, ticker in [("399", "00982A"), ("500", "00992A")]:
    resp = session.post(
        "https://www.capitalfund.com.tw/CFWeb/api/etf/buyback",
        json={"fundId": fund_id, "date": None},
        headers={
            "Referer": f"https://www.capitalfund.com.tw/etf/product/detail/{fund_id}/portfolio",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    print(ticker, "status", resp.status_code)
    data = resp.json().get("data") or {}
    pcf = data.get("pcf") or {}
    print(ticker, "pcf:", json.dumps(pcf, ensure_ascii=False, indent=2))
    print(ticker, "top-level data keys:", list(data.keys()))
    for k in data.keys():
        if k != "pcf" and k != "stocks":
            print(" ", k, "=", data[k])
    print()
