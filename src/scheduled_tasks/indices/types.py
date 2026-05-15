"""Index sync domain types."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class IndexPricePoint:
    trade_date: date
    close: float


@dataclass(frozen=True)
class IndexValuationPoint:
    trade_date: date
    pe_ttm: float | None
    pb: float | None
    source: str = "index_dailybasic"


@dataclass(frozen=True)
class IndustryWeightRow:
    as_of_date: date
    sw_level: str
    industry_name: str
    weight_pct: float
