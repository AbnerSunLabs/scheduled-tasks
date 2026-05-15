"""Fetch index industry weights from TuShare."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from scheduled_tasks.indices.date_utils import compact, to_date
from scheduled_tasks.indices.types import IndustryWeightRow
from scheduled_tasks.tushare_client import create_pro


def _candidate_index_codes(symbol: str) -> list[str]:
    if symbol == "000300.SH":
        return ["000300.SH", "399300.SZ"]
    return [symbol]


def _latest_weight_frame(pro: Any, symbol: str) -> Any:
    today = date.today()
    start = compact(today - timedelta(days=450))
    end = compact(today)

    for index_code in _candidate_index_codes(symbol):
        try:
            df = pro.index_weight(
                index_code=index_code,
                start_date=start,
                end_date=end,
                fields="index_code,con_code,trade_date,weight",
            )
        except Exception:
            continue
        if df is None or getattr(df, "empty", True):
            continue
        df = df.dropna(subset=["con_code", "trade_date", "weight"])
        if getattr(df, "empty", True):
            continue
        latest_trade_date = str(df["trade_date"].max())
        latest = df[df["trade_date"].astype(str) == latest_trade_date].copy()
        if latest.empty:
            continue
        return latest
    return None


def _sw_member_by_l1(pro: Any, l1_code: str) -> list[dict[str, str]]:
    try:
        df = pro.index_member_all(l1_code=l1_code)
    except Exception:
        return []
    if df is None or getattr(df, "empty", True):
        return []
    if "is_new" in df.columns:
        latest = df[df["is_new"].astype(str).str.upper() == "Y"]
        if not latest.empty:
            df = latest

    rows: list[dict[str, str]] = []
    for _, row in df.iterrows():
        ts_code = str(row.get("ts_code", "") or "").strip().upper()
        if not ts_code:
            continue
        item = {"ts_code": ts_code}
        for key in ("l1_name", "l2_name", "l3_name"):
            value = str(row.get(key, "") or "").strip()
            if value:
                item[key] = value
        rows.append(item)
    return rows


def _sw_industry_map(pro: Any) -> dict[str, dict[str, str]]:
    try:
        classify = pro.index_classify(level="L1", src="SW2021")
    except Exception:
        classify = None
    if classify is None or getattr(classify, "empty", True):
        return {}

    out: dict[str, dict[str, str]] = {}
    for _, row in classify.iterrows():
        l1_code = str(row.get("index_code", "") or "").strip().upper()
        if not l1_code:
            continue
        for member in _sw_member_by_l1(pro, l1_code):
            ts_code = member.pop("ts_code", "")
            if ts_code and ts_code not in out:
                out[ts_code] = member
    return out


def _normalize_rows(
    as_of_date: date, sw_level: str, weights: dict[str, float]
) -> list[IndustryWeightRow]:
    total = sum(v for v in weights.values() if v > 0)
    if total <= 0:
        return []
    rows = [
        IndustryWeightRow(
            as_of_date=as_of_date,
            sw_level=sw_level,
            industry_name=name,
            weight_pct=round(value * 100 / total, 4),
        )
        for name, value in weights.items()
        if value > 0
    ]
    return sorted(rows, key=lambda row: row.weight_pct, reverse=True)


def fetch_index_industry_weights(code: str) -> list[IndustryWeightRow]:
    symbol = code.strip().upper()
    pro = create_pro()
    weight_df = _latest_weight_frame(pro, symbol)
    if weight_df is None or getattr(weight_df, "empty", True):
        return []

    latest_trade_date = str(weight_df["trade_date"].max())
    if not re.match(r"^\d{8}$", latest_trade_date):
        return []
    as_of_date = to_date(latest_trade_date)
    industry_by_stock = _sw_industry_map(pro)
    sw1_weights: defaultdict[str, float] = defaultdict(float)
    sw2_weights: defaultdict[str, float] = defaultdict(float)
    sw3_weights: defaultdict[str, float] = defaultdict(float)

    for _, row in weight_df.iterrows():
        con_code = str(row.get("con_code", "") or "").strip().upper()
        industry = industry_by_stock.get(con_code)
        if not industry:
            continue
        try:
            weight = float(row.get("weight"))
        except (TypeError, ValueError):
            continue
        if weight <= 0:
            continue
        l1 = industry.get("l1_name")
        l2 = industry.get("l2_name")
        l3 = industry.get("l3_name")
        if l1:
            sw1_weights[l1] += weight
        if l2:
            sw2_weights[l2] += weight
        if l3:
            sw3_weights[l3] += weight

    return [
        *_normalize_rows(as_of_date, "sw1", sw1_weights),
        *_normalize_rows(as_of_date, "sw2", sw2_weights),
        *_normalize_rows(as_of_date, "sw3", sw3_weights),
    ]
