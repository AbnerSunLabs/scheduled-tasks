"""东方财富恒生指数日线。

接口（非公开 SLA，可能改版）：
``GET https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=100.HSI&klt=101``

``f51`` 是日期，``f53`` 是收盘。港股 16:00 收盘，当天 16:00 前不把最后一根当收盘。
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
SECID = "100.HSI"
DEFAULT_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
HK = ZoneInfo("Asia/Hong_Kong")
CLOSE_HOUR = 16
USER_AGENT = "scheduled-tasks-eastmoney-hsi/1.0"


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


def drop_unclosed_session(rows: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    """港股当天 16:00 前，最后一根还是盘中价。"""
    local = now.astimezone(HK)
    session_close = local.replace(hour=CLOSE_HOUR, minute=0, second=0, microsecond=0)
    if local >= session_close:
        return rows
    today = session_close.date()
    return [row for row in rows if row["trade_date"] < today]


def fetch_hsi_daily_closes(
    *,
    start: date,
    end: date,
    now: datetime | None = None,
    http_get_json: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """拉取恒生收盘，再按请求窗口和港股收盘时刻过滤。"""
    if end < start:
        return []
    query = urllib.parse.urlencode(
        {
            "secid": SECID,
            "klt": "101",
            "fqt": "1",
            "beg": "19900101",
            "end": "20500101",
            "lmt": "100000",
            "fields1": "f1",
            "fields2": "f51,f53",
        }
    )
    getter = http_get_json or _http_get_json
    rows = parse_hsi_klines(getter(f"{DEFAULT_URL}?{query}"))
    rows = [row for row in rows if start <= row["trade_date"] <= end]
    rows = drop_unclosed_session(rows, now=now or datetime.now(HK))
    if not rows:
        raise RuntimeError("eastmoney returned no HSI close in the requested window")
    return rows
