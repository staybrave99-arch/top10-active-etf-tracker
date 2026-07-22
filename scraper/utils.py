import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )
}

_session = requests.Session()
_session.headers.update(DEFAULT_HEADERS)

# Fund-company sites occasionally drop the connection or time out under
# transient load (seen intermittently across every site, not one in
# particular). Retry idempotent GET/POST reads a few times with backoff
# before giving up, so a single blip doesn't fail that ETF for the day.
_retry = Retry(
    total=3,
    backoff_factor=1.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET", "POST"],
)
_adapter = HTTPAdapter(max_retries=_retry)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)


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
