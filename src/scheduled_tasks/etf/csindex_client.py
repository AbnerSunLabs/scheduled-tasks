"""中证指数官网行情 — 官网校验源，非主写。

接口（非公开 SLA，可能改版）：
``GET https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=399989&startDate=YYYYMMDD&endDate=YYYYMMDD``

响应 ``data[]`` 含 open/high/low/close；字段 ``peg`` 数值量级为市盈率（非传统 PEG），
本模块映射为 ``current_pe_ttm``。
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import certifi

PRICE_SOURCE = "csindex"
VALUATION_SOURCE = "csindex"
DEFAULT_BASE = "https://www.csindex.com.cn"
USER_AGENT = "scheduled-tasks-csindex/1.0"


def to_csindex_code(index_code: str) -> str:
    """``399989.SZ`` / ``399989`` → ``399989``。"""
    raw = index_code.strip().upper()
    if "." in raw:
        left, _suffix = raw.split(".", 1)
        raw = left
    if len(raw) != 6 or not raw.isdigit():
        raise ValueError(f"invalid index_code for csindex: {index_code}")
    return raw


def parse_trade_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _http_get_json(url: str, *, timeout: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Referer": "https://www.csindex.com.cn/",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch_index_daily_bars(
    index_code: str,
    *,
    start: date,
    end: date,
    http_get_json: Callable[[str], dict[str, Any]] | None = None,
    base_url: str = DEFAULT_BASE,
) -> list[dict[str, Any]]:
    """拉取中证指数日 OHLC；``index_code`` 可为 ``399989.SZ``。"""
    if end < start:
        return []
    code = to_csindex_code(index_code)
    getter = http_get_json or _http_get_json
    # 官网要求 YYYYMMDD；传 ISO 日期会返回空 data（仍 code=200）
    query = urllib.parse.urlencode(
        {
            "indexCode": code,
            "startDate": start.strftime("%Y%m%d"),
            "endDate": end.strftime("%Y%m%d"),
        }
    )
    url = f"{base_url.rstrip('/')}/csindex-home/perf/index-perf?{query}"
    payload = getter(url)
    if str(payload.get("code")) not in {"200", "0"} and not payload.get("success", True):
        raise RuntimeError(f"csindex index-perf failed: {payload.get('msg') or payload}")

    rows: list[dict[str, Any]] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        close = _num(item.get("close"))
        if close is None or close <= 0:
            continue
        td = parse_trade_date(item.get("tradeDate"))
        rows.append(
            {
                "index_code": index_code.strip().upper()
                if "." in index_code
                else f"{code}.SZ",
                "trade_date": td,
                "open": _num(item.get("open")),
                "high": _num(item.get("high")),
                "low": _num(item.get("low")),
                "close": close,
                # CSI 字段名 peg，量级为 PE
                "current_pe_ttm": _num(item.get("peg")),
                "price_source": PRICE_SOURCE,
            }
        )
    rows.sort(key=lambda r: r["trade_date"])
    return rows


def fetch_index_window(
    index_code: str,
    *,
    lookback_days: int = 45,
    end: date | None = None,
    http_get_json: Callable[[str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    end = end or date.today()
    start = end - timedelta(days=max(lookback_days, 1))
    return fetch_index_daily_bars(
        index_code, start=start, end=end, http_get_json=http_get_json
    )
