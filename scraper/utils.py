import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import requests

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

_session = requests.Session()
_session.headers.update(DEFAULT_HEADERS)


def get_session():
    return _session


def fetch_html(url, **kwargs):
    kwargs.setdefault("timeout", 30)
    resp = _session.get(url, **kwargs)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def clean_number(value):
    """Strip thousands separators, %, and currency prefixes; return a Decimal."""
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "--", "N/A"):
        return None
    s = re.sub(r"^(NTD|TWD|新台幣)\s*", "", s, flags=re.IGNORECASE)
    s = s.replace(",", "").replace("%", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_date(value):
    """Parse common date formats (YYYY/MM/DD, YYYY-MM-DD, with optional time) into a date."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "T" in s:
        s = s.split("T")[0]
    s = s.replace("/", "-")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def today_taipei():
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Taipei")).date()
