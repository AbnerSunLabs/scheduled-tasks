"""同花顺扶摇 A 股指数日线收盘（REST，非网页）。"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

FUYAO_PRICE_SOURCE = "fuyao"
_BASE_URL = "https://fuyao.aicubes.cn"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
HttpGet = Callable[[str, dict[str, str]], dict[str, Any]]


def _default_http_get(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = Request(url, headers=headers)
    with urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("fuyao index historical response is not an object")
    return payload


def _to_shanghai_date(date_ms: int) -> date:
    return datetime.fromtimestamp(date_ms / 1000, tz=_SHANGHAI).date()


def _parse_bars(payload: dict[str, Any], thscode: str) -> list[dict[str, Any]]:
    if payload.get("code") != 0:
        raise RuntimeError(
            f"fuyao index historical failed for {thscode}: {payload.get('message')}"
        )
    data = payload.get("data") or {}
    items = data.get("item") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError(f"fuyao index historical returned no bars for {thscode}")
    bars: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or item.get("close_price") is None:
            continue
        bars.append(
            {
                "trade_date": _to_shanghai_date(int(item["date_ms"])).isoformat(),
                "close": float(item["close_price"]),
            }
        )
    if not bars:
        raise RuntimeError(f"fuyao index historical has no close for {thscode}")
    return bars


def fetch_index_daily_closes(
    thscode: str,
    *,
    start: date,
    end: date,
    api_key: str,
    http_get_json: HttpGet | None = None,
) -> list[dict[str, Any]]:
    """拉取单只指数日线收盘。官方只支持近 5 年、单代码、interval=1d。"""
    code = thscode.strip().upper()
    key = api_key.strip()
    if not code or not key:
        raise RuntimeError("fuyao thscode and api key are required")
    if end < start:
        raise RuntimeError("fuyao end must not be earlier than start")
    query = urlencode(
        {
            "thscode": code,
            "interval": "1d",
            "start": int(datetime.combine(start, datetime.min.time(), _SHANGHAI).timestamp() * 1000),
            "end": int(datetime.combine(end, datetime.max.time(), _SHANGHAI).timestamp() * 1000),
        }
    )
    getter = http_get_json or _default_http_get
    payload = getter(
        f"{_BASE_URL}/api/a-share-index/prices/historical?{query}",
        {"X-api-key": key, "Accept": "application/json"},
    )
    return _parse_bars(payload, code)
