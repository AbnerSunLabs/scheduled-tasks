"""ETF 日 K 拉取（AkShare / 东财）。

GitHub Actions（海外 runner）上 BaoStock 历史 K 线会忽略 start_date、
只返回近约 122 根；改用 AkShare fund_etf_hist_em，三种复权均可拉全历史。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

import akshare as ak
import pandas as pd

T = TypeVar("T")

ADJUST_NONE = ""
ADJUST_QFQ = "qfq"
ADJUST_HFQ = "hfq"

# 无库内水位 / full 时的全局下限（上证 50ETF 约 2005 上市）
DEFAULT_HISTORY_START = date(2004, 1, 1)

_COL_MAP = {
    "日期": "date",
    "开盘": "open",
    "最高": "high",
    "最低": "low",
    "收盘": "close",
    "成交量": "volume",
    "成交额": "amount",
}


def format_em_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_trade_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10].replace("/", "-"), fmt).date()
        except ValueError:
            continue
    ts = pd.to_datetime(value)
    return ts.date()


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_sec: float = 1.5,
) -> T:
    last_error: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as error:  # noqa: BLE001 — 远端抖动统一重试
            last_error = error
            if i + 1 >= attempts:
                break
            time.sleep(base_delay_sec * (i + 1))
    assert last_error is not None
    raise last_error


def fetch_ipo_date(etf_code: str) -> date:
    """用全历史不复权最早交易日近似上市日（AkShare 无稳定 IPO 字段）。"""
    df = fetch_kline(etf_code, DEFAULT_HISTORY_START, date.today(), ADJUST_NONE)
    if df.empty:
        raise RuntimeError(f"empty history for ipo probe: {etf_code}")
    return parse_trade_date(df["date"].iloc[0])


def fetch_kline(
    etf_code: str,
    start: date,
    end: date,
    adjustflag: str,
) -> pd.DataFrame:
    """拉取日 K；adjustflag: '' 不复权 / 'qfq' / 'hfq'。

    东财成交量为「手」，与表约定一致，不再 ÷100。
    """

    def _do() -> pd.DataFrame:
        raw = ak.fund_etf_hist_em(
            symbol=etf_code,
            period="daily",
            start_date=format_em_date(start),
            end_date=format_em_date(end),
            adjust=adjustflag,
        )
        if raw is None or raw.empty:
            return pd.DataFrame(
                columns=["date", "open", "high", "low", "close", "volume", "amount"]
            )
        df = raw.rename(columns=_COL_MAP)
        missing = [c for c in ("date", "open", "high", "low", "close") if c not in df.columns]
        if missing:
            raise RuntimeError(f"{etf_code}: akshare missing columns {missing}")
        wanted = ("date", "open", "high", "low", "close", "volume", "amount")
        keep = [c for c in wanted if c in df.columns]
        df = df[keep].copy()
        df["date"] = df["date"].map(parse_trade_date)
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        # 过滤请求窗外的行（接口偶发多返回）
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        return df.reset_index(drop=True)

    return retry_call(_do)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", ""}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def dataframe_to_ohlc_map(df: pd.DataFrame) -> dict[date, dict[str, float]]:
    """按 date 索引 OHLC（缺任一则该日不入 map）。"""
    result: dict[date, dict[str, float]] = {}
    if df.empty:
        return result
    for _, row in df.iterrows():
        trade_date = parse_trade_date(row.get("date"))
        open_ = _to_float(row.get("open"))
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        if open_ is None or high is None or low is None or close is None:
            continue
        result[trade_date] = {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    return result


def merge_three_adjustments(
    raw_df: pd.DataFrame,
    qfq_df: pd.DataFrame,
    hfq_df: pd.DataFrame,
    etf_code: str,
) -> list[dict[str, Any]]:
    """
    以不复权 date 为主表 left join qfq/hfq。
    主表某日缺复权 OHLC → 抛错（禁止 silent 写 NULL）。
    """
    qfq_map = dataframe_to_ohlc_map(qfq_df)
    hfq_map = dataframe_to_ohlc_map(hfq_df)
    rows: list[dict[str, Any]] = []
    if raw_df.empty:
        return rows

    for _, row in raw_df.iterrows():
        trade_date = parse_trade_date(row.get("date"))
        open_ = _to_float(row.get("open"))
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        if close is None:
            continue
        if open_ is None or high is None or low is None:
            raise RuntimeError(
                f"{etf_code} {trade_date}: raw OHLC incomplete (close present but OHLC missing)"
            )

        q = qfq_map.get(trade_date)
        h = hfq_map.get(trade_date)
        if q is None or h is None:
            raise RuntimeError(f"{etf_code} {trade_date}: missing qfq/hfq row for primary bar date")

        rows.append(
            {
                "etf_code": etf_code,
                "trade_date": trade_date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": _to_float(row.get("volume")),
                "amount": _to_float(row.get("amount")),
                "open_qfq": q["open"],
                "high_qfq": q["high"],
                "low_qfq": q["low"],
                "close_qfq": q["close"],
                "open_hfq": h["open"],
                "high_hfq": h["high"],
                "low_hfq": h["low"],
                "close_hfq": h["close"],
                "price_source": "akshare",
            }
        )
    return rows


def merge_adj_only(
    qfq_df: pd.DataFrame,
    hfq_df: pd.DataFrame,
    etf_code: str,
) -> list[dict[str, Any]]:
    """仅合并 qfq/hfq（adj_check 用）；以 qfq 日期为主，hfq 必须齐全。"""
    qfq_map = dataframe_to_ohlc_map(qfq_df)
    hfq_map = dataframe_to_ohlc_map(hfq_df)
    if not qfq_map:
        return []
    rows: list[dict[str, Any]] = []
    for trade_date, q in sorted(qfq_map.items()):
        h = hfq_map.get(trade_date)
        if h is None:
            raise RuntimeError(f"{etf_code} {trade_date}: missing hfq row during adj-only merge")
        rows.append(
            {
                "etf_code": etf_code,
                "trade_date": trade_date,
                "open_qfq": q["open"],
                "high_qfq": q["high"],
                "low_qfq": q["low"],
                "close_qfq": q["close"],
                "open_hfq": h["open"],
                "high_hfq": h["high"],
                "low_hfq": h["low"],
                "close_hfq": h["close"],
            }
        )
    return rows


def fetch_anchor_close_qfq(etf_code: str, anchor: date) -> float | None:
    """拉取锚点日 close_qfq；无数据返回 None。"""
    # 稍扩窗，避免单日节假日空结果
    start = anchor - timedelta(days=5)
    df = fetch_kline(etf_code, start, anchor, ADJUST_QFQ)
    ohlc = dataframe_to_ohlc_map(df)
    point = ohlc.get(anchor)
    if point is None:
        return None
    return point["close"]
