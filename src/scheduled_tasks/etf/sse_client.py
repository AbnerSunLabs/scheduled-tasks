"""上交所官网日 K（yunhq）— 官网校验源，非主写。

接口（非公开 SLA，可能改版）：
``GET http://yunhq.sse.com.cn:32041/v1/sh1/dayk/{code}?select=date,open,high,low,close,volume&begin=-N&end=-1``

``close`` 必须显式出现在 select 中；``last`` 在日 K 上常为 null。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import date, datetime
from typing import Any, TypeVar

import certifi

T = TypeVar("T")

PRICE_SOURCE = "sse"
DEFAULT_BASE = "http://yunhq.sse.com.cn:32041"
USER_AGENT = "scheduled-tasks-sse/1.0"
DEFAULT_LOOKBACK_BARS = 30


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


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 4,
    base_delay_sec: float = 1.5,
) -> T:
    last_error: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as error:  # noqa: BLE001
            last_error = error
            if i + 1 >= attempts:
                break
            time.sleep(base_delay_sec * (i + 1))
    assert last_error is not None
    raise last_error


def _http_get_json(url: str, *, timeout: float = 60.0) -> dict[str, Any]:
    def _do() -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
                "Referer": "https://www.sse.com.cn/",
                "Connection": "close",
            },
            method="GET",
        )
        # yunhq 日 K 目前仅提供 http；https 会 SSL 失败
        kwargs: dict[str, Any] = {"timeout": timeout}
        if url.startswith("https://"):
            kwargs["context"] = _ssl_context()
        try:
            with urllib.request.urlopen(req, **kwargs) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"sse http {exc.code} for {url}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise RuntimeError(f"sse network error: {exc}") from exc

    return retry_call(_do)


def fetch_etf_daily_bars(
    etf_code: str,
    *,
    max_bars: int = DEFAULT_LOOKBACK_BARS,
    http_get_json: Callable[[str], dict[str, Any]] | None = None,
    base_url: str = DEFAULT_BASE,
) -> list[dict[str, Any]]:
    """拉取上交所 ETF 不复权日 K；返回 open/high/low/close（volume 不用于比对）。"""
    code = etf_code.strip()
    if len(code) != 6 or not code.isdigit() or not code.startswith("5"):
        raise ValueError(f"sse client only supports SH ETF codes (5xxxxx), got: {etf_code}")
    if max_bars <= 0:
        return []

    getter = http_get_json or _http_get_json
    begin = -abs(int(max_bars))
    url = (
        f"{base_url.rstrip('/')}/v1/sh1/dayk/{code}"
        f"?select=date,open,high,low,close,volume&begin={begin}&end=-1"
    )
    payload = getter(url)
    kline = payload.get("kline") or []
    rows: list[dict[str, Any]] = []
    for item in kline:
        if not isinstance(item, (list, tuple)) or len(item) < 5:
            continue
        close = item[4]
        if close is None:
            continue
        try:
            close_f = float(close)
        except (TypeError, ValueError):
            continue
        if close_f <= 0:
            continue

        def _num(v: Any) -> float | None:
            if v is None or v == "":
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        rows.append(
            {
                "etf_code": code,
                "trade_date": parse_trade_date(item[0]),
                "open": _num(item[1]),
                "high": _num(item[2]),
                "low": _num(item[3]),
                "close": close_f,
                "price_source": PRICE_SOURCE,
            }
        )
    rows.sort(key=lambda r: r["trade_date"])
    return rows
