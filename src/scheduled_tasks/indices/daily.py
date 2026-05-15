"""Fetch index daily prices from TuShare."""

from __future__ import annotations

from scheduled_tasks.indices.date_utils import compact, effective_query_end_date, to_date
from scheduled_tasks.indices.types import IndexPricePoint
from scheduled_tasks.tushare_client import create_pro


def fetch_index_daily(code: str) -> list[IndexPricePoint]:
    symbol = code.strip().upper()
    pro = create_pro()
    df = pro.index_daily(
        ts_code=symbol,
        start_date="20000101",
        end_date=compact(effective_query_end_date()),
        fields="ts_code,trade_date,close",
    )

    if df is None or getattr(df, "empty", True):
        return []

    df = df.sort_values("trade_date").reset_index(drop=True)
    points: list[IndexPricePoint] = []
    for _, row in df.iterrows():
        try:
            close = float(row["close"])
        except (TypeError, ValueError, KeyError):
            continue
        if close <= 0:
            continue
        points.append(
            IndexPricePoint(
                trade_date=to_date(row.get("trade_date")),
                close=round(close, 4),
            )
        )
    return points
