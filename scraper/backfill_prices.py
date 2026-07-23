"""One-off backfill of stock_price history for the stocks we currently
hold, so BIAS (which needs a 20-trading-day window) doesn't have to wait
a month of daily runs to accumulate. Not part of the daily pipeline --
run manually: `python -m scraper.backfill_prices`.

Neither exchange offers the ideal shape (per-stock history in one call
covering both), so this combines two different endpoints:

  TWSE (main board): /exchangeReport/STOCK_DAY?stockNo=X&date=YYYYMM01
      returns one *stock's* whole month at a time. No historical
      "all stocks on date X" endpoint exists (STOCK_DAY_ALL always
      returns only the latest day, regardless of a date param).

  TPEx (OTC): /www/zh-tw/afterTrading/dailyQuotes?date=YYY/MM/DD
      the mirror image: one *date's* all-OTC-stocks snapshot at a time,
      filtered down to the codes we care about. No per-stock history
      endpoint exists in TPEx's open API catalog.

Which exchange a given code belongs to isn't known upfront, so it's
determined once by checking membership in each exchange's "latest day,
all stocks" listing (the same endpoints scraper/prices.py already uses
for the daily run).
"""

import time
from datetime import date, timedelta

from scraper.db import get_conn, init_schema, save_stock_prices
from scraper.prices import TPEX_URL, TWSE_URL
from scraper.prices import _change_pct as change_pct
from scraper.prices import _get_with_retries as get_with_retries
from scraper.utils import clean_number, get_session, today_taipei

TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TPEX_DAILY_QUOTES_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"

BACKFILL_CALENDAR_DAYS = 40  # comfortably covers 20+ trading days incl. holidays


def get_held_stock_codes(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT stock_code FROM etf_holding")
        return {row[0] for row in cur.fetchall()}


def classify_exchange(session, codes):
    """Return (twse_codes, tpex_codes) by checking today's full listings."""
    import csv
    import io

    twse_resp = get_with_retries(session, TWSE_URL, timeout=60)
    rows = list(csv.reader(io.StringIO(twse_resp.content.decode("utf-8"))))
    twse_all = {row[1].strip() for row in rows[1:] if len(row) >= 2}

    tpex_resp = get_with_retries(session, TPEX_URL, timeout=60, verify=False)
    tpex_all = {(item.get("SecuritiesCompanyCode") or "").strip() for item in tpex_resp.json()}

    twse_codes = codes & twse_all
    tpex_codes = codes & tpex_all
    unknown = codes - twse_codes - tpex_codes
    if unknown:
        print(f"[CLASSIFY] {len(unknown)} code(s) not found on either exchange today: {sorted(unknown)}")
    return twse_codes, tpex_codes


def roc_date_slash(d):
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def months_covering(start, end):
    months = set()
    d = start.replace(day=1)
    while d <= end:
        months.add((d.year, d.month))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def backfill_twse(session, codes, start_date, end_date):
    rows = []  # (stock_code, trade_date, price, change_pct)
    months = sorted(months_covering(start_date, end_date))
    for i, code in enumerate(sorted(codes), 1):
        for year, month in months:
            date_param = f"{year}{month:02d}01"
            try:
                resp = session.get(
                    TWSE_STOCK_DAY_URL,
                    params={"response": "json", "date": date_param, "stockNo": code},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                print(f"[TWSE] {code} {year}-{month:02d}: request failed ({exc})")
                time.sleep(1)
                continue
            if data.get("stat") != "OK":
                time.sleep(0.3)
                continue
            for row in data.get("data", []):
                roc_y, m, d = row[0].split("/")
                trade_date = date(int(roc_y) + 1911, int(m), int(d))
                if not (start_date <= trade_date <= end_date):
                    continue
                price = clean_number(row[6])
                if price is None:
                    continue
                change = clean_number(row[7])
                rows.append((code, trade_date, price, change_pct(price, change)))
            time.sleep(0.3)
        if i % 20 == 0:
            print(f"[TWSE] {i}/{len(codes)} codes done")
    return rows


def backfill_tpex(session, codes, start_date, end_date):
    rows = []
    d = start_date
    n_days = (end_date - start_date).days + 1
    for i in range(n_days):
        day = start_date + timedelta(days=i)
        if day.weekday() >= 5:  # skip weekends, saves a request that would just come back empty
            continue
        try:
            resp = session.get(
                TPEX_DAILY_QUOTES_URL,
                params={"date": roc_date_slash(day), "type": "EW", "response": "json"},
                timeout=30,
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"[TPEx] {day}: request failed ({exc})")
            time.sleep(1)
            continue
        tables = data.get("tables") or []
        if not tables:
            time.sleep(0.3)
            continue
        for row in tables[0].get("data") or []:
            code = (row[0] or "").strip()
            if code not in codes:
                continue
            price = clean_number(row[2])
            if price is None:
                continue
            change = clean_number(row[3])
            rows.append((code, day, price, change_pct(price, change)))
        time.sleep(0.3)
    return rows


def main():
    conn = get_conn()
    init_schema(conn)

    codes = get_held_stock_codes(conn)
    print(f"[BACKFILL] {len(codes)} distinct held stock codes")

    session = get_session()
    twse_codes, tpex_codes = classify_exchange(session, codes)
    print(f"[BACKFILL] {len(twse_codes)} TWSE-listed, {len(tpex_codes)} TPEx-listed")

    end_date = today_taipei()
    start_date = end_date - timedelta(days=BACKFILL_CALENDAR_DAYS)

    twse_rows = backfill_twse(session, twse_codes, start_date, end_date)
    print(f"[TWSE] collected {len(twse_rows)} (code, date) price rows")

    tpex_rows = backfill_tpex(session, tpex_codes, start_date, end_date)
    print(f"[TPEx] collected {len(tpex_rows)} (code, date) price rows")

    by_date = {}
    for code, trade_date, price, chg in twse_rows + tpex_rows:
        by_date.setdefault(trade_date, []).append((code, price, chg))

    for trade_date in sorted(by_date):
        save_stock_prices(conn, trade_date, by_date[trade_date])
    total = sum(len(v) for v in by_date.values())
    print(f"[DONE] saved {total} stock_price rows across {len(by_date)} trading days")

    conn.close()


if __name__ == "__main__":
    main()
