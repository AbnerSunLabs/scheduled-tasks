"""Date helpers for TuShare sync."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("Asia/Shanghai")
MARKET_CLOSE_TIME = time(15, 0)


def to_iso_date(raw: object) -> str:
    value = str(raw or "").strip()
    if len(value) == 8:
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def to_date(raw: object) -> date:
    return date.fromisoformat(to_iso_date(raw))


def compact(dt: date) -> str:
    return dt.strftime("%Y%m%d")


def effective_query_end_date() -> date:
    now = datetime.now(MARKET_TZ)
    if now.time() < MARKET_CLOSE_TIME:
        return now.date() - timedelta(days=1)
    return now.date()
