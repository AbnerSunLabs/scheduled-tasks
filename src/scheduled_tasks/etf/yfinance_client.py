"""ETF 日 K 拉取（Yahoo Finance / yfinance）。

GitHub Actions 海外 runner 上 BaoStock 会截断、东财/AkShare 会断连；
Yahoo 可稳定返回 A 股 ETF 全历史。Close 为不复权，Adj Close 为前复权；
后复权由首日因子推导。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

import pandas as pd
import yfinance as yf

T = TypeVar("T")

ADJUST_NONE = "raw"
ADJUST_QFQ = "qfq"
ADJUST_HFQ = "hfq"

DEFAULT_HISTORY_START = date(2004, 1, 1)
VOLUME_SHARE_TO_LOT = 100


def to_yahoo_symbol(etf_code: str) -> str:
    if not etf_code or len(etf_code) != 6 or not etf_code.isdigit():
        raise ValueError(f"invalid etf_code: {etf_code}")
    if etf_code.startswith("5"):
        return f"{etf_code}.SS"
    if etf_code.startswith("1"):
        return f"{etf_code}.SZ"
    raise ValueError(f"unsupported etf exchange prefix: {etf_code}")


def parse_trade_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    ts = pd.to_datetime(value)
    if getattr(ts, "tzinfo", None) is not None:
        ts = ts.tz_convert(None)
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
        except Exception as error:  # noqa: BLE001
            last_error = error
            if i + 1 >= attempts:
                break
            time.sleep(base_delay_sec * (i + 1))
    assert last_error is not None
    raise last_error


def _download_history(etf_code: str, start: date, end: date) -> pd.DataFrame:
    """一次下载 raw + Adj Close；end 对 yfinance 为开区间，故 +1 天。"""

    def _do() -> pd.DataFrame:
        symbol = to_yahoo_symbol(etf_code)
        end_exclusive = end + timedelta(days=1)
        df = yf.download(
            symbol,
            start=start.isoformat(),
            end=end_exclusive.isoformat(),
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if df is None or df.empty:
            return pd.DataFrame()
        # yfinance 多级列时压平
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
        df = df.reset_index()
        # Date 列名可能是 Date / Datetime
        date_col = "Date" if "Date" in df.columns else df.columns[0]
        df = df.rename(
            columns={
                date_col: "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adj_close",
                "Volume": "volume",
            }
        )
        needed = ["date", "open", "high", "low", "close", "adj_close"]
        missing = [c for c in needed if c not in df.columns]
        if missing:
            raise RuntimeError(f"{etf_code}: yfinance missing columns {missing}")
        df["date"] = df["date"].map(parse_trade_date)
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        return df.reset_index(drop=True)

    return retry_call(_do)


def fetch_kline_bundle(etf_code: str, start: date, end: date) -> pd.DataFrame:
    """返回含 raw OHLC + adj_close + volume 的 DataFrame。"""
    return _download_history(etf_code, start, end)


def fetch_ipo_date(etf_code: str) -> date:
    df = _download_history(etf_code, DEFAULT_HISTORY_START, date.today())
    if df.empty:
        raise RuntimeError(f"empty history for ipo probe: {etf_code}")
    return parse_trade_date(df["date"].iloc[0])


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", ""}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def build_three_adjustments(df: pd.DataFrame, etf_code: str) -> list[dict[str, Any]]:
    """
    从不复权 OHLC + Adj Close 生成三种价。
    qfq = Adj Close（及按因子缩放的 OHLC）；
    hfq = Adj Close * (C0 / Adj0)，使首日 hfq = 首日不复权价。
    """
    if df.empty:
        return []

    first = df.iloc[0]
    c0 = _to_float(first["close"])
    a0 = _to_float(first["adj_close"])
    if c0 is None or a0 is None or a0 == 0:
        raise RuntimeError(f"{etf_code}: invalid first-day close/adj_close for hfq")

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        trade_date = parse_trade_date(row["date"])
        open_ = _to_float(row["open"])
        high = _to_float(row["high"])
        low = _to_float(row["low"])
        close = _to_float(row["close"])
        adj = _to_float(row["adj_close"])
        if close is None or adj is None:
            continue
        if open_ is None or high is None or low is None:
            raise RuntimeError(
                f"{etf_code} {trade_date}: raw OHLC incomplete (close present but OHLC missing)"
            )
        if close == 0:
            raise RuntimeError(f"{etf_code} {trade_date}: close is zero")

        factor = adj / close  # 前复权因子（相对不复权）
        open_qfq = open_ * factor
        high_qfq = high * factor
        low_qfq = low * factor
        close_qfq = adj

        # 后复权：hfq = adj * (C0/A0) = close * factor * C0/A0
        scale_hfq = c0 / a0
        open_hfq = open_qfq * scale_hfq
        high_hfq = high_qfq * scale_hfq
        low_hfq = low_qfq * scale_hfq
        close_hfq = close_qfq * scale_hfq

        volume_shares = _to_float(row.get("volume"))
        volume_lots = (
            volume_shares / VOLUME_SHARE_TO_LOT if volume_shares is not None else None
        )

        rows.append(
            {
                "etf_code": etf_code,
                "trade_date": trade_date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume_lots,
                "amount": None,  # Yahoo 无成交额
                "open_qfq": open_qfq,
                "high_qfq": high_qfq,
                "low_qfq": low_qfq,
                "close_qfq": close_qfq,
                "open_hfq": open_hfq,
                "high_hfq": high_hfq,
                "low_hfq": low_hfq,
                "close_hfq": close_hfq,
                "price_source": "yfinance",
            }
        )
    return rows


def build_adj_only(df: pd.DataFrame, etf_code: str) -> list[dict[str, Any]]:
    """仅复权列（adj_check）；逻辑同 build_three_adjustments。"""
    full = build_three_adjustments(df, etf_code)
    return [
        {
            "etf_code": r["etf_code"],
            "trade_date": r["trade_date"],
            "open_qfq": r["open_qfq"],
            "high_qfq": r["high_qfq"],
            "low_qfq": r["low_qfq"],
            "close_qfq": r["close_qfq"],
            "open_hfq": r["open_hfq"],
            "high_hfq": r["high_hfq"],
            "low_hfq": r["low_hfq"],
            "close_hfq": r["close_hfq"],
        }
        for r in full
    ]


def fetch_anchor_close_qfq(etf_code: str, anchor: date) -> float | None:
    start = anchor - timedelta(days=7)
    df = _download_history(etf_code, start, anchor)
    if df.empty:
        return None
    hit = df[df["date"].map(parse_trade_date) == anchor]
    if hit.empty:
        return None
    return _to_float(hit.iloc[0]["adj_close"])
