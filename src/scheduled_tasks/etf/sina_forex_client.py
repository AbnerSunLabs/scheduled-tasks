"""新浪离岸美元兑人民币日线。

接口（非公开 SLA，可能改版）：
``GET https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/var/NewForexService.getDayKLine?symbol=fx_susdcnh``

正文是 JSONP，每根为 ``日期,开,低,高,收``。收盘按 1 美元计，不是银行牌价的 100 美元。
"""

from __future__ import annotations

import ssl
import urllib.request
from collections.abc import Callable
from datetime import date
from typing import Any

import certifi

PRICE_SOURCE = "sina"
SYMBOL = "fx_susdcnh"
DEFAULT_URL = (
    "https://vip.stock.finance.sina.com.cn/forex/api/jsonp.php/"
    f"var/NewForexService.getDayKLine?symbol={SYMBOL}"
)
USER_AGENT = "scheduled-tasks-sina-forex/1.0"


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _http_get_text(url: str, *, timeout: float = 60.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read().decode("utf-8")


def parse_usdcnh_kline(payload: str) -> list[dict[str, Any]]:
    """从 JSONP 正文取出按日期排序的收盘。高低字段顺序是低、高，这里只用收盘。"""
    start = payload.find('"')
    end = payload.rfind('"')
    if start < 0 or end <= start:
        raise RuntimeError("sina USDCNH kline has no quoted body")
    rows: list[dict[str, Any]] = []
    for part in payload[start + 1 : end].split("|"):
        bits = [bit.strip() for bit in part.strip().strip(",").split(",") if bit.strip()]
        if len(bits) < 5:
            continue
        try:
            trade_date = date.fromisoformat(bits[0])
            close = float(bits[4])
        except ValueError:
            continue
        if close <= 0:
            continue
        rows.append({"trade_date": trade_date.isoformat(), "close": close})
    if not rows:
        raise RuntimeError("sina USDCNH kline parsed no closes")
    rows.sort(key=lambda row: str(row["trade_date"]))
    return rows


def fetch_usdcnh_daily_closes(
    *,
    start: date,
    end: date,
    http_get_text: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """拉取窗口内的离岸美元兑人民币收盘。新浪一次返回全历史，这里再按日期切开。"""
    if end < start:
        return []
    getter = http_get_text or _http_get_text
    rows = [
        row
        for row in parse_usdcnh_kline(getter(DEFAULT_URL))
        if start.isoformat() <= str(row["trade_date"]) <= end.isoformat()
    ]
    if not rows:
        raise RuntimeError(f"sina returned no USDCNH close between {start} and {end}")
    return rows
