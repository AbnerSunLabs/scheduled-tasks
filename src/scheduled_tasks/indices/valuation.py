"""Fetch index valuation points from TuShare."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from scheduled_tasks.indices.date_utils import compact, effective_query_end_date, to_date
from scheduled_tasks.indices.types import IndexValuationPoint
from scheduled_tasks.tushare_client import create_pro

INDEX_WEIGHT_LOOKBACK_DAYS = 450
DAILY_BASIC_LOOKBACK_DAYS = 20


def _pick_number(row: Any, *names: str) -> float | None:
    for name in names:
        try:
            value = row.get(name)
        except AttributeError:
            continue
        if value is None:
            continue
        try:
            out = float(value)
        except (TypeError, ValueError):
            continue
        if math.isnan(out) or out <= 0:
            continue
        return out
    return None


def _latest_weight_frame(pro: Any, symbol: str, end_date: date) -> Any:
    start = compact(end_date - timedelta(days=INDEX_WEIGHT_LOOKBACK_DAYS))
    end = compact(end_date)
    try:
        df = pro.index_weight(
            index_code=symbol,
            start_date=start,
            end_date=end,
            fields="index_code,con_code,trade_date,weight",
        )
    except Exception:
        return None

    if df is None or getattr(df, "empty", True):
        return None
    df = df.dropna(subset=["con_code", "trade_date", "weight"])
    if getattr(df, "empty", True):
        return None
    latest_trade_date = str(df["trade_date"].max())
    latest = df[df["trade_date"].astype(str) == latest_trade_date].copy()
    if latest.empty:
        return None
    return latest


def _weighted_harmonic_ratio(rows: list[tuple[float, float]]) -> float | None:
    denom = 0.0
    weight_sum = 0.0
    for weight, ratio in rows:
        if weight <= 0 or ratio <= 0:
            continue
        denom += weight / ratio
        weight_sum += weight
    if denom <= 0 or weight_sum <= 0:
        return None
    return weight_sum / denom


def _batched(values: list[str], size: int) -> list[list[str]]:
    return [values[i : i + size] for i in range(0, len(values), size)]


def _latest_daily_basic(pro: Any, codes: list[str], end_date: date) -> Any:
    frames = []
    start = compact(end_date - timedelta(days=DAILY_BASIC_LOOKBACK_DAYS))
    end = compact(end_date)
    for batch in _batched(codes, 80):
        try:
            df = pro.daily_basic(
                ts_code=",".join(batch),
                start_date=start,
                end_date=end,
                fields="ts_code,trade_date,pe_ttm,pb",
            )
        except Exception:
            continue
        if df is not None and not getattr(df, "empty", True):
            frames.append(df)
    if not frames:
        return None

    import pandas as pd

    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return None
    return df.sort_values(["ts_code", "trade_date"]).drop_duplicates("ts_code", keep="last")


def _fallback_current_valuation_from_members(
    pro: Any, symbol: str, end_date: date
) -> list[IndexValuationPoint]:
    weight_df = _latest_weight_frame(pro, symbol, end_date)
    if weight_df is None or getattr(weight_df, "empty", True):
        return []

    weights: dict[str, float] = {}
    for _, row in weight_df.iterrows():
        con_code = str(row.get("con_code", "") or "").strip().upper()
        if not con_code:
            continue
        weight = _pick_number(row, "weight")
        if weight is None:
            continue
        weights[con_code] = weights.get(con_code, 0.0) + weight

    if not weights:
        return []

    basic_df = _latest_daily_basic(pro, list(weights.keys()), end_date)
    if basic_df is None or getattr(basic_df, "empty", True):
        return []

    latest_trade_date = str(basic_df["trade_date"].max())
    pe_rows: list[tuple[float, float]] = []
    pb_rows: list[tuple[float, float]] = []
    for _, row in basic_df.iterrows():
        ts_code = str(row.get("ts_code", "") or "").strip().upper()
        weight = weights.get(ts_code)
        if weight is None:
            continue
        pe_ttm = _pick_number(row, "pe_ttm", "pe")
        pb = _pick_number(row, "pb")
        if pe_ttm is not None:
            pe_rows.append((weight, pe_ttm))
        if pb is not None:
            pb_rows.append((weight, pb))

    pe_ttm = _weighted_harmonic_ratio(pe_rows)
    pb = _weighted_harmonic_ratio(pb_rows)
    if pe_ttm is None and pb is None:
        return []

    return [
        IndexValuationPoint(
            trade_date=to_date(latest_trade_date),
            pe_ttm=round(pe_ttm, 4) if pe_ttm is not None else None,
            pb=round(pb, 4) if pb is not None else None,
            source="member_daily_basic_fallback",
        )
    ]


def fetch_index_valuations(code: str) -> list[IndexValuationPoint]:
    symbol = code.strip().upper()
    pro = create_pro()
    query_end_date = effective_query_end_date()
    df = pro.index_dailybasic(
        ts_code=symbol,
        start_date="20000101",
        end_date=compact(query_end_date),
        fields="ts_code,trade_date,pe_ttm,pb",
    )

    if df is None or getattr(df, "empty", True):
        return _fallback_current_valuation_from_members(pro, symbol, query_end_date)

    df = df.sort_values("trade_date").reset_index(drop=True)
    points: list[IndexValuationPoint] = []
    for _, row in df.iterrows():
        pe_ttm = _pick_number(row, "pe_ttm", "pe")
        pb = _pick_number(row, "pb")
        if pe_ttm is None and pb is None:
            continue
        points.append(
            IndexValuationPoint(
                trade_date=to_date(row.get("trade_date")),
                pe_ttm=round(pe_ttm, 4) if pe_ttm is not None else None,
                pb=round(pb, 4) if pb is not None else None,
            )
        )

    if not points:
        return _fallback_current_valuation_from_members(pro, symbol, query_end_date)

    latest_point_date = points[-1].trade_date
    if latest_point_date < query_end_date:
        fallback_points = _fallback_current_valuation_from_members(pro, symbol, query_end_date)
        if fallback_points and fallback_points[-1].trade_date > latest_point_date:
            points.append(fallback_points[-1])
    return points
