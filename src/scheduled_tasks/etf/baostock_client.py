"""BaoStock 客户端（交易日历 + ETF 成交额回退）。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

AMOUNT_SOURCE = "baostock"


def to_baostock_code(etf_code: str) -> str:
    raw = etf_code.strip()
    if not (raw.isdigit() and len(raw) == 6):
        raise ValueError(f"invalid etf_code: {etf_code}")
    if raw.startswith(("5", "6")):
        return f"sh.{raw}"
    if raw.startswith(("0", "1", "3")):
        return f"sz.{raw}"
    raise ValueError(f"unsupported exchange prefix: {etf_code}")


def fetch_cn_trade_calendar(
    start: date,
    end: date,
    *,
    query_fn: Callable[[str, str], Any] | None = None,
) -> list[dict[str, Any]]:
    """拉取全国 A 股交易日历，映射为 market='CN'。

    BaoStock query_trade_dates 无 exchange 字段，禁止臆造 SSE/SZSE 两套。
    """
    if end < start:
        return []

    owns_login = False
    if query_fn is None:
        import baostock as bs

        login = bs.login()
        if getattr(login, "error_code", "0") not in ("0", 0, None):
            raise RuntimeError(f"baostock login failed: {login.error_msg}")
        owns_login = True

        def _default_query(start_date: str, end_date: str) -> Any:
            return bs.query_trade_dates(start_date=start_date, end_date=end_date)

        query_fn = _default_query

    try:
        rs = query_fn(start.isoformat(), end.isoformat())
        error_code = getattr(rs, "error_code", "0")
        if error_code not in ("0", 0, None):
            raise RuntimeError(f"query_trade_dates failed: {getattr(rs, 'error_msg', rs)}")

        rows: list[dict[str, Any]] = []
        while rs.next():
            item = rs.get_row_data()
            # 字段顺序：calendar_date, is_trading_day
            cal_date = date.fromisoformat(str(item[0]))
            is_open = str(item[1]) in {"1", "true", "True"}
            rows.append({"market": "CN", "cal_date": cal_date, "is_open": is_open})
        return rows
    finally:
        if owns_login:
            import baostock as bs

            bs.logout()


def fetch_etf_amount_window(
    etf_code: str,
    start: date,
    end: date,
    *,
    query_fn: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """拉取单窗口 ETF 日成交额（单位：元）；amount_source='baostock'。"""
    if end < start:
        return []
    code = etf_code.strip()
    bs_code = to_baostock_code(code)

    owns_login = False
    if query_fn is None:
        import baostock as bs

        login = bs.login()
        if getattr(login, "error_code", "0") not in ("0", 0, None):
            raise RuntimeError(f"baostock login failed: {login.error_msg}")
        owns_login = True

        def _default_query(symbol: str, start_date: str, end_date: str) -> Any:
            return bs.query_history_k_data_plus(
                symbol,
                "date,amount",
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3",
            )

        query_fn = _default_query

    try:
        rs = query_fn(bs_code, start.isoformat(), end.isoformat())
        error_code = getattr(rs, "error_code", "0")
        if error_code not in ("0", 0, None):
            raise RuntimeError(
                f"baostock amount query failed: {getattr(rs, 'error_msg', rs)}"
            )
        rows: list[dict[str, Any]] = []
        while rs.next():
            item = rs.get_row_data()
            # fields: date, amount
            raw_amount = item[1] if len(item) > 1 else ""
            if raw_amount in {"", None}:
                continue
            try:
                amount = float(raw_amount)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "etf_code": code,
                    "trade_date": date.fromisoformat(str(item[0])),
                    "amount": amount,
                    "amount_source": AMOUNT_SOURCE,
                }
            )
        return rows
    finally:
        if owns_login:
            import baostock as bs

            bs.logout()
