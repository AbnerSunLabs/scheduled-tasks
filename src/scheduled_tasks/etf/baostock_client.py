"""BaoStock 会话与 ETF 日 K 拉取。"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

import baostock as bs
import pandas as pd

T = TypeVar("T")

# Baostock volume 单位为股；表约定为手
VOLUME_SHARE_TO_LOT = 100

ADJUST_NONE = "3"
ADJUST_QFQ = "2"
ADJUST_HFQ = "1"


def to_baostock_code(etf_code: str) -> str:
    """6 位代码 → Baostock 代码（5→sh. / 1→sz.）。"""
    if not etf_code or len(etf_code) != 6 or not etf_code.isdigit():
        raise ValueError(f"invalid etf_code: {etf_code}")
    if etf_code.startswith("5"):
        return f"sh.{etf_code}"
    if etf_code.startswith("1"):
        return f"sz.{etf_code}"
    raise ValueError(f"unsupported etf exchange prefix: {etf_code}")


def format_bs_date(d: date) -> str:
    return d.strftime("%Y-%m-%d")


def parse_bs_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


@contextmanager
def baostock_session():
    """登录 Baostock，退出时 logout。"""
    result = bs.login()
    if result.error_code != "0":
        raise RuntimeError(f"baostock login failed: {result.error_msg}")
    try:
        yield bs
    finally:
        bs.logout()


def _query_to_dataframe(rs: Any) -> pd.DataFrame:
    if rs.error_code != "0":
        raise RuntimeError(f"baostock query failed: {rs.error_msg}")
    rows: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        rows.append(rs.get_row_data())
    if not rows:
        return pd.DataFrame(columns=list(rs.fields))
    return pd.DataFrame(rows, columns=rs.fields)


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_sec: float = 1.0,
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


def fetch_ipo_date(bs_api: Any, etf_code: str) -> date:
    """查询上市日；必须精确匹配 code，禁止取全表第一行。"""

    def _do() -> date:
        code = to_baostock_code(etf_code)
        rs = bs_api.query_stock_basic(code=code)
        df = _query_to_dataframe(rs)
        if df.empty:
            raise RuntimeError(f"empty stock_basic for {etf_code}")
        if "code" not in df.columns:
            raise RuntimeError(f"stock_basic missing code column for {etf_code}")

        # BaoStock 偶发忽略 code 过滤返回全表；必须精确匹配，禁止 iloc[0]
        matched = df[df["code"].astype(str).str.strip() == code]
        if matched.empty:
            raise RuntimeError(f"stock_basic returned {len(df)} rows but none matched {code}")
        row = matched.iloc[0]
        ipo_raw = None
        for key in ("ipoDate", "ipo_date", "listDate", "ipoData"):
            if key in matched.columns and str(row[key]).strip():
                ipo_raw = str(row[key]).strip()
                break
        if not ipo_raw or ipo_raw in {"", "None"}:
            raise RuntimeError(f"missing ipoDate for {etf_code}")
        return parse_bs_date(ipo_raw)

    return retry_call(_do)


def _iter_date_chunks(start: date, end: date, *, max_days: int = 180):
    """将长区间切成不超过 max_days 的子区间，规避 BaoStock 长查询截断。"""
    if start > end:
        return
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def fetch_kline(
    bs_api: Any,
    etf_code: str,
    start: date,
    end: date,
    adjustflag: str,
) -> pd.DataFrame:
    """拉取日 K；长区间按约半年分段后合并，避免远端静默截断。"""

    frames: list[pd.DataFrame] = []
    for chunk_start, chunk_end in _iter_date_chunks(start, end):

        def _do(
            cs: date = chunk_start,
            ce: date = chunk_end,
        ) -> pd.DataFrame:
            code = to_baostock_code(etf_code)
            fields = "date,code,open,high,low,close,volume,amount,tradestatus"
            rs = bs_api.query_history_k_data_plus(
                code,
                fields,
                start_date=format_bs_date(cs),
                end_date=format_bs_date(ce),
                frequency="d",
                adjustflag=adjustflag,
            )
            return _query_to_dataframe(rs)

        chunk_df = retry_call(_do)
        if not chunk_df.empty:
            frames.append(chunk_df)

    if not frames:
        return pd.DataFrame(
            columns=[
                "date",
                "code",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "tradestatus",
            ]
        )

    df = pd.concat(frames, ignore_index=True)
    if "date" in df.columns:
        df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date")
        df = df.reset_index(drop=True)
    return df


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
    """按 date 索引 OHLC（缺任一则该日不入 map）。

    与主行情一致：tradestatus 存在且不为 '1' 的停牌日跳过，
    避免 adj_check 远端日期集含停牌日而误判 needs_full。
    """
    result: dict[date, dict[str, float]] = {}
    if df.empty:
        return result
    for _, row in df.iterrows():
        tradestatus = str(row.get("tradestatus", "")).strip()
        if tradestatus and tradestatus != "1":
            continue
        raw_date = str(row.get("date", "")).strip()
        if not raw_date:
            continue
        trade_date = parse_bs_date(raw_date)
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
    volume 股→手（÷100）；amount 元直接写。
    """
    qfq_map = dataframe_to_ohlc_map(qfq_df)
    hfq_map = dataframe_to_ohlc_map(hfq_df)
    rows: list[dict[str, Any]] = []
    if raw_df.empty:
        return rows

    for _, row in raw_df.iterrows():
        tradestatus = str(row.get("tradestatus", "")).strip()
        if tradestatus and tradestatus != "1":
            continue
        raw_date = str(row.get("date", "")).strip()
        if not raw_date:
            continue
        trade_date = parse_bs_date(raw_date)
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

        volume_shares = _to_float(row.get("volume"))
        amount = _to_float(row.get("amount"))
        volume_lots = volume_shares / VOLUME_SHARE_TO_LOT if volume_shares is not None else None

        rows.append(
            {
                "etf_code": etf_code,
                "trade_date": trade_date,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume_lots,
                "amount": amount,
                "open_qfq": q["open"],
                "high_qfq": q["high"],
                "low_qfq": q["low"],
                "close_qfq": q["close"],
                "open_hfq": h["open"],
                "high_hfq": h["high"],
                "low_hfq": h["low"],
                "close_hfq": h["close"],
                "price_source": "baostock",
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


def fetch_anchor_close_qfq(bs_api: Any, etf_code: str, anchor: date) -> float | None:
    """拉取锚点日 close_qfq；无数据返回 None。"""
    df = fetch_kline(bs_api, etf_code, anchor, anchor, ADJUST_QFQ)
    ohlc = dataframe_to_ohlc_map(df)
    point = ohlc.get(anchor)
    if point is None:
        return None
    return point["close"]
