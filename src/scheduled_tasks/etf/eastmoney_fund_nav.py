"""天天基金单位净值脚本。

接口（非公开 SLA，可能改版）：
``GET https://fund.eastmoney.com/pingzhongdata/{code}.js``

``Data_netWorthTrend`` 的 ``x`` 是上海零点的毫秒时间戳，``y`` 是单位净值。
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.request
from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import certifi

PRICE_SOURCE = "eastmoney"
SCRIPT_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
USER_AGENT = "scheduled-tasks-eastmoney-fund-nav/1.0"
SHANGHAI = ZoneInfo("Asia/Shanghai")
_TREND = re.compile(r"Data_netWorthTrend\s*=\s*(\[[\s\S]*?\]);")


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _http_get_text(url: str, *, timeout: float = 60.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Referer": "https://fund.eastmoney.com/"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read().decode("utf-8")


def parse_fund_nav_script(script: str, fund_code: str) -> list[dict[str, Any]]:
    """取出单位净值。同一天重复出现时保留后面那条。"""
    match = _TREND.search(script)
    if not match:
        return []
    payload = json.loads(match.group(1))
    if not isinstance(payload, list):
        return []
    by_date: dict[str, dict[str, Any]] = {}
    for row in payload:
        if not isinstance(row, dict):
            continue
        point = _nav_point(fund_code, row.get("x"), row.get("y"))
        if point:
            by_date[str(point["nav_date"])] = point
    return [by_date[key] for key in sorted(by_date)]


def _nav_point(fund_code: str, raw_ms: Any, raw_nav: Any) -> dict[str, Any] | None:
    try:
        ms = float(raw_ms)
        nav = float(raw_nav)
    except (TypeError, ValueError):
        return None
    if nav <= 0:
        return None
    nav_date = datetime.fromtimestamp(ms / 1000, tz=SHANGHAI).date().isoformat()
    return {
        "fund_code": fund_code,
        "nav_date": nav_date,
        "unit_nav": nav,
        "source": PRICE_SOURCE,
    }


def fetch_fund_nav(
    fund_code: str,
    *,
    http_get_text: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    if not re.fullmatch(r"[0-9]{6}", fund_code):
        raise RuntimeError(f"fund code must be 6 digits, got {fund_code}")
    getter = http_get_text or _http_get_text
    rows = parse_fund_nav_script(getter(SCRIPT_URL.format(code=fund_code)), fund_code)
    if not rows:
        raise RuntimeError(f"eastmoney returned no unit nav for {fund_code}")
    return rows
