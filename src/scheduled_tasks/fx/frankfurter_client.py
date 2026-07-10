"""Frankfurter (ECB) FX client — stdlib only, no API key."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from decimal import Decimal
from typing import Any

DEFAULT_BASE_URL = "https://api.frankfurter.dev/v1"
# 驾驶舱基础币种三角：以 USD 为锚推导
QUOTE_CURRENCIES = ("CNY", "HKD")
SOURCE = "frankfurter"


def build_pair_rows(
    rate_date: date,
    usd_to_cny: float | Decimal,
    usd_to_hkd: float | Decimal,
    *,
    source: str = SOURCE,
) -> list[dict[str, Any]]:
    """由 USD 锚点生成 USD/CNY、USD/HKD、HKD/CNY 三对正向汇率。"""
    cny = Decimal(str(usd_to_cny))
    hkd = Decimal(str(usd_to_hkd))
    if cny <= 0 or hkd <= 0:
        raise ValueError("USD quote rates must be positive")
    hkd_to_cny = (cny / hkd).quantize(Decimal("0.00000001"))
    return [
        {
            "rate_date": rate_date,
            "from_currency": "USD",
            "to_currency": "CNY",
            "rate": cny,
            "source": source,
        },
        {
            "rate_date": rate_date,
            "from_currency": "USD",
            "to_currency": "HKD",
            "rate": hkd,
            "source": source,
        },
        {
            "rate_date": rate_date,
            "from_currency": "HKD",
            "to_currency": "CNY",
            "rate": hkd_to_cny,
            "source": source,
        },
    ]


def _http_get_json(url: str, *, timeout: float = 30.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "scheduled-tasks-fx/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Frankfurter HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Frankfurter network error: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Frankfurter response is not a JSON object")
    return payload


def fetch_usd_quotes_for_range(
    start: date,
    end: date,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """拉取 [start, end] 内每个有报价日的 USD→CNY/HKD，并展开为三对行。"""
    if end < start:
        raise ValueError("end must be >= start")
    to_param = ",".join(QUOTE_CURRENCIES)
    path = f"{start.isoformat()}..{end.isoformat()}"
    query = urllib.parse.urlencode({"from": "USD", "to": to_param})
    url = f"{base_url.rstrip('/')}/{path}?{query}"
    payload = _http_get_json(url)

    rates_by_day = payload.get("rates")
    rows: list[dict[str, Any]] = []

    # 单日接口返回 {"date","rates":{CNY:..}}；区间返回 {"rates":{"YYYY-MM-DD":{...}}}
    if isinstance(rates_by_day, dict):
        if not rates_by_day:
            # 区间内无 ECB 报价日（极端假期窗口）→ 空结果，由 job 记 success+0
            return []
        first_val = next(iter(rates_by_day.values()))
        if isinstance(first_val, dict):
            for day_str, quotes in sorted(rates_by_day.items()):
                rows.extend(_rows_from_usd_quotes(date.fromisoformat(day_str), quotes))
            return rows

    day_str = payload.get("date")
    if isinstance(day_str, str) and isinstance(rates_by_day, dict):
        return _rows_from_usd_quotes(date.fromisoformat(day_str), rates_by_day)

    raise RuntimeError(f"Unexpected Frankfurter payload keys: {sorted(payload)}")


def _rows_from_usd_quotes(rate_date: date, quotes: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        usd_to_cny = float(quotes["CNY"])
        usd_to_hkd = float(quotes["HKD"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Missing CNY/HKD quotes on {rate_date}: {quotes}") from exc
    return build_pair_rows(rate_date, usd_to_cny, usd_to_hkd)
