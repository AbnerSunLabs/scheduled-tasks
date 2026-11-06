"""东方财富指数日线。

接口（非公开 SLA，可能改版）：
``GET https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=100.HSI&klt=101``

``f51`` 是日期，``f53`` 是收盘。恒生、恒生医疗、创业板指、标普 500 分别用
``100.HSI``、``124.HSHCI``、``0.399006``、``100.SPX``。
港股 16:00（香港）、A 股 15:00（上海）、标普现金盘 16:00（美东）之前，不把当天最后一根当收盘。
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import certifi

PRICE_SOURCE = "eastmoney"
DEFAULT_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HK = ZoneInfo("Asia/Hong_Kong")
NY = ZoneInfo("America/New_York")
SH = ZoneInfo("Asia/Shanghai")
CLOSE_HOUR = 16
USER_AGENT = "scheduled-tasks-eastmoney-hsi/1.0"
# secid、收盘时区、收盘钟点。创业板指不在中证官网样本里；标普按美东收盘，不沿用港股。
INDEX_SESSIONS: dict[str, tuple[str, ZoneInfo, int]] = {
    "HSI.HI": ("100.HSI", HK, CLOSE_HOUR),
    "HSHCI.HI": ("124.HSHCI", HK, CLOSE_HOUR),
    "399006.SZ": ("0.399006", SH, 15),
    "SPX.OTH": ("100.SPX", NY, CLOSE_HOUR),
}


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _http_get_json(url: str, *, timeout: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("eastmoney HSI kline was not an object")
    return payload


def parse_hsi_klines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """从日线字符串取出按日期排序的收盘。只请求了日期和收盘两列。"""
    data = payload.get("data") or {}
    lines = data.get("klines") if isinstance(data, dict) else None
    rows: list[dict[str, Any]] = []
    for line in lines or []:
        bits = str(line).split(",")
        if len(bits) < 2:
            continue
        rows.append({"trade_date": date.fromisoformat(bits[0]), "close": float(bits[1])})
    rows.sort(key=lambda row: row["trade_date"])
    return rows


def drop_unclosed_session(
    rows: list[dict[str, Any]],
    *,
    now: datetime,
    zone: ZoneInfo = HK,
    close_hour: int = CLOSE_HOUR,
) -> list[dict[str, Any]]:
    """收盘时刻前，最后一根还是盘中价。时区按该指数的交易时段。"""
    local = now.astimezone(zone)
    session_close = local.replace(hour=close_hour, minute=0, second=0, microsecond=0)
    if local >= session_close:
        return rows
    return [row for row in rows if row["trade_date"] < local.date()]


def _kline_url(secid: str) -> str:
    query = urllib.parse.urlencode(
        {
            "secid": secid,
            "klt": "101",
            "fqt": "1",
            "beg": "19900101",
            "end": "20500101",
            "lmt": "100000",
            "fields1": "f1",
            "fields2": "f51,f53",
        }
    )
    return f"{DEFAULT_URL}?{query}"


def fetch_eastmoney_index_closes(
    index_code: str,
    *,
    start: date,
    end: date,
    now: datetime | None = None,
    http_get_json: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按指数代码拉取收盘，并丢掉尚未收盘的当天。"""
    session = INDEX_SESSIONS.get(index_code)
    if session is None:
        raise RuntimeError(f"no eastmoney session for {index_code}")
    if end < start:
        return []
    secid, zone, hour = session
    getter = http_get_json or _http_get_json
    rows = parse_hsi_klines(getter(_kline_url(secid)))
    rows = [row for row in rows if start <= row["trade_date"] <= end]
    rows = drop_unclosed_session(rows, now=now or datetime.now(zone), zone=zone, close_hour=hour)
    if not rows:
        raise RuntimeError(f"eastmoney returned no close for {index_code}")
    return rows


def fetch_hsi_daily_closes(
    *,
    start: date,
    end: date,
    now: datetime | None = None,
    http_get_json: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """拉取恒生收盘。其它东财指数走 ``fetch_eastmoney_index_closes``。"""
    return fetch_eastmoney_index_closes(
        "HSI.HI", start=start, end=end, now=now, http_get_json=http_get_json
    )
