"""AKShare ETF 日成交额客户端（仅国内机使用）。"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd

AMOUNT_SOURCE = "akshare"
MAX_WINDOW_DAYS = 366
RETRY_BACKOFF_SECONDS = (2.0, 4.0, 8.0)

# 东财 WAF 对默认 python-requests UA 会 RST；生产 client 必须补 UA。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_UA_PATCHED = False


def ensure_requests_user_agent() -> bool:
    """为进程内 requests.Session 补默认 UA；返回是否生效。"""
    global _UA_PATCHED
    if _UA_PATCHED:
        return True
    try:
        import requests
    except ImportError:
        return False

    original_request = requests.Session.request
    if getattr(original_request, "_enrich_ua_patched", False):
        _UA_PATCHED = True
        return True

    def _patched(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("User-Agent", _UA)
        kwargs["headers"] = headers
        return original_request(self, method, url, *args, **kwargs)

    _patched._enrich_ua_patched = True  # type: ignore[attr-defined]
    requests.Session.request = _patched  # type: ignore[method-assign]
    _UA_PATCHED = True
    return True


def to_six_digit_code(code: str) -> str:
    """统一映射为 6 位 ETF 代码。"""
    raw = code.strip()
    if "." in raw:
        left, right = raw.split(".", 1)
        if left.lower() in {"sh", "sz"} and right.isdigit() and len(right) == 6:
            return right
        if left.isdigit() and len(left) == 6 and right.upper() in {"SS", "SZ"}:
            return left
    if raw.isdigit() and len(raw) == 6:
        return raw
    raise ValueError(f"unsupported etf code format: {code}")


def iter_date_windows(
    start: date,
    end: date,
    *,
    max_days: int = MAX_WINDOW_DAYS,
) -> list[tuple[date, date]]:
    """按自然年/最多 max_days 分段（含端点）。"""
    if end < start:
        return []
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        year_end = date(cursor.year, 12, 31)
        window_end = min(end, year_end, cursor + timedelta(days=max_days - 1))
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def _sleep_backoff(attempt_index: int) -> None:
    base = RETRY_BACKOFF_SECONDS[min(attempt_index, len(RETRY_BACKOFF_SECONDS) - 1)]
    jitter = base * 0.2 * (2 * random.random() - 1)
    time.sleep(max(0.0, base + jitter))


def fetch_etf_amount_hist(
    etf_code: str,
    start: date,
    end: date,
    *,
    sleep_between_windows: float = 0.35,
    fetch_fn: Callable[..., pd.DataFrame] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """分段拉取 ETF 日成交额；返回 (rows, window_failures)。

    rows 字段：etf_code, trade_date, amount, amount_source='akshare'
    单位：元。单窗口失败可记录并续跑，禁止单次五年大请求。
    """
    ensure_requests_user_agent()
    code = to_six_digit_code(etf_code)
    if fetch_fn is None:
        import akshare as ak

        def _default_fetch(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
            return ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start_date,
                                       end_date=end_date, adjust="")

        fetch_fn = _default_fetch

    rows_by_date: dict[date, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []

    for win_start, win_end in iter_date_windows(start, end):
        last_error: str | None = None
        ok = False
        for attempt in range(4):
            try:
                df = fetch_fn(
                    symbol=code,
                    start_date=win_start.strftime("%Y%m%d"),
                    end_date=win_end.strftime("%Y%m%d"),
                )
                if df is None or df.empty:
                    ok = True
                    break
                date_col = "日期" if "日期" in df.columns else "date"
                amount_col = "成交额" if "成交额" in df.columns else "amount"
                for _, series in df.iterrows():
                    trade_date = pd.Timestamp(series[date_col]).date()
                    amount = series[amount_col]
                    if pd.isna(amount):
                        continue
                    rows_by_date[trade_date] = {
                        "etf_code": code,
                        "trade_date": trade_date,
                        "amount": float(amount),
                        "amount_source": AMOUNT_SOURCE,
                    }
                ok = True
                break
            except Exception as exc:  # noqa: BLE001 — 窗口级失败需续跑
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 3:
                    _sleep_backoff(attempt)
        if not ok:
            failures.append(
                {
                    "etf_code": code,
                    "window_start": win_start.isoformat(),
                    "window_end": win_end.isoformat(),
                    "error": last_error or "unknown",
                }
            )
        if sleep_between_windows > 0:
            time.sleep(sleep_between_windows)

    rows = [rows_by_date[d] for d in sorted(rows_by_date)]
    return rows, failures


def optional_amount_sanity_sample(
    rows: Sequence[dict[str, Any]],
    ohlcv_rows: Sequence[dict[str, Any]],
    *,
    relative_tol: float = 0.15,
) -> dict[str, Any]:
    """可选抽检 amount ≈ close × volume × 100（volume 为手）。"""
    by_date = {r["trade_date"]: r for r in ohlcv_rows}
    checked = 0
    passed = 0
    for row in rows:
        ref = by_date.get(row["trade_date"])
        if not ref:
            continue
        close = ref.get("close")
        volume = ref.get("volume")
        if close is None or volume is None:
            continue
        expected = float(close) * float(volume) * 100.0
        if expected == 0:
            continue
        checked += 1
        rel = abs(float(row["amount"]) - expected) / abs(expected)
        if rel <= relative_tol:
            passed += 1
    return {
        "checked": checked,
        "passed": passed,
        "pass_rate": (passed / checked) if checked else None,
        "tolerance": relative_tol,
    }
