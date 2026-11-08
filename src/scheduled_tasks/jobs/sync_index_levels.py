"""把跟踪指数的收盘写入 index_daily_metrics.close。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import (
    connect,
    create_sync_run,
    ensure_index_row,
    finish_sync_run,
    upsert_index_daily_metrics,
)
from scheduled_tasks.etf.csindex_client import fetch_index_daily_bars
from scheduled_tasks.etf.eastmoney_hsi_client import (
    PRICE_SOURCE as EASTMONEY_PRICE_SOURCE,
    fetch_eastmoney_index_closes,
)
from scheduled_tasks.etf.sina_forex_client import (
    PRICE_SOURCE as SINA_PRICE_SOURCE,
    fetch_usdcnh_daily_closes,
)

JOB_NAME = "sync_index_levels"
SUMMARY_PATH = Path("artifacts/sync_index_levels_summary.json")
DEFAULT_LOOKBACK_DAYS = 45

# 6 位中证样本走官网 perf。
INDEX_LEVEL_SPECS: tuple[tuple[str, str, str, str], ...] = (
    ("000905.SH", "中证500", "宽基", "csindex"),
    ("000922.SH", "中证红利", "策略", "csindex"),
    ("399989.SZ", "中证医疗", "行业", "csindex"),
    ("000991.SH", "全指医药", "行业", "csindex"),
    ("000990.SH", "全指消费", "行业", "csindex"),
    ("000942.SH", "内地消费", "行业", "csindex"),
    ("000815.SH", "中证细分食品饮料", "行业", "csindex"),
    ("399971.SZ", "中证传媒", "行业", "csindex"),
    ("000993.SH", "全指信息", "行业", "csindex"),
    ("399975.SZ", "证券公司", "行业", "csindex"),
    ("930955.CSI", "中证红利低波动100", "策略", "csindex"),
    # 恒生、恒生医疗不在中证样本。标普用指数表里已有的 SPX.OTH。
    ("HSI.HI", "恒生指数", "宽基", EASTMONEY_PRICE_SOURCE),
    # 创业板指不在中证官网样本，官网返回空。深市指数走东财 secid=0.399006。
    ("399006.SZ", "创业板指", "宽基", EASTMONEY_PRICE_SOURCE),
    ("HSHCI.HI", "恒生医疗", "行业", EASTMONEY_PRICE_SOURCE),
    ("000300.SH", "沪深300", "宽基", "csindex"),
    ("000852.SH", "中证1000", "宽基", "csindex"),
    ("SPX.OTH", "标普500", "宽基", EASTMONEY_PRICE_SOURCE),
    # indices.code 必须带交易所后缀。离岸没有沪深代码，用已有的 OTH。
    ("USDCNH.OTH", "美元兑人民币", "汇率", SINA_PRICE_SOURCE),
)


@dataclass
class SyncSummary:
    status: str = "success"
    run_id: int | None = None
    success_codes: list[str] = field(default_factory=list)
    failure_codes: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    row_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "job": JOB_NAME,
            "status": self.status,
            "run_id": self.run_id,
            "success_codes": self.success_codes,
            "failure_codes": self.failure_codes,
            "errors": self.errors,
            "row_counts": self.row_counts,
        }


def _window(today: date, lookback_days: int) -> tuple[date, date]:
    if lookback_days < 1:
        raise RuntimeError("lookback_days must be >= 1")
    return today - timedelta(days=lookback_days), today


def _without_start_placeholder(rows: list[dict[str, Any]], start: date) -> list[dict[str, Any]]:
    """起点早于发布日时，官网会把首日收盘贴在请求起点上，这根不能入库。"""
    if len(rows) < 2:
        return rows
    ordered = sorted(rows, key=lambda row: str(row["trade_date"]))
    first = date.fromisoformat(str(ordered[0]["trade_date"])[:10])
    second = date.fromisoformat(str(ordered[1]["trade_date"])[:10])
    if first == start and (second - first).days > 10:
        return ordered[1:]
    return ordered


def _close_row(index_code: str, trade_date: str, close: float, source: str) -> dict[str, Any]:
    # pe/pb 传 None，让既有估值 coalesce 保留，本任务只更新收盘。
    return {
        "index_code": index_code,
        "trade_date": trade_date,
        "close": close,
        "pe_ttm": None,
        "pb": None,
        "price_source": source,
        "valuation_source": None,
    }


def fetch_closes(
    index_code: str,
    source: str,
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    if source == EASTMONEY_PRICE_SOURCE:
        bars = fetch_eastmoney_index_closes(index_code, start=start, end=end)
        return [
            _close_row(index_code, str(bar["trade_date"]), float(bar["close"]), EASTMONEY_PRICE_SOURCE)
            for bar in bars
        ]
    if source == SINA_PRICE_SOURCE:
        bars = fetch_usdcnh_daily_closes(start=start, end=end)
        return [
            _close_row(index_code, str(bar["trade_date"]), float(bar["close"]), SINA_PRICE_SOURCE)
            for bar in bars
        ]
    if source == "csindex":
        bars = fetch_index_daily_bars(index_code, start=start, end=end)
        rows = [
            _close_row(index_code, str(bar["trade_date"]), float(bar["close"]), "csindex")
            for bar in bars
            if bar.get("close") is not None
        ]
        if not rows:
            raise RuntimeError(f"csindex returned no close for {index_code}")
        return _without_start_placeholder(rows, start)
    raise RuntimeError(f"unknown price source {source} for {index_code}")


def sync_all_index_levels(
    conn: Any,
    *,
    today: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> SyncSummary:
    summary = SyncSummary()
    start, end = _window(today or date.today(), lookback_days)
    for index_code, name, category, source in INDEX_LEVEL_SPECS:
        try:
            rows = fetch_closes(index_code, source, start=start, end=end)
            ensure_index_row(conn, code=index_code, name=name, category=category)
            written = upsert_index_daily_metrics(conn, rows)
            conn.commit()
            summary.success_codes.append(index_code)
            summary.row_counts[index_code] = written
        except Exception as exc:  # noqa: BLE001 — 单只失败只回滚这一只
            conn.rollback()
            summary.failure_codes.append(index_code)
            summary.errors.append({"index_code": index_code, "error": str(exc)})
    if summary.failure_codes and summary.success_codes:
        summary.status = "partial"
    elif summary.failure_codes:
        summary.status = "failed"
    return summary


def resolve_session_date(raw: str | None, *, today: date | None = None) -> date:
    """闹钟叫醒时写死的交易日。没带日期就用运行当天。"""
    text = (raw or "").strip()
    if not text:
        return today or date.today()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RuntimeError(f"SESSION_DATE must be YYYY-MM-DD, got {text}") from exc


def run(*, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> SyncSummary:
    settings = load_settings()
    session_day = resolve_session_date(os.environ.get("SESSION_DATE"))
    codes = tuple(spec[0] for spec in INDEX_LEVEL_SPECS)
    conn = connect(settings.database_url)
    summary = SyncSummary()
    try:
        run_id = create_sync_run(
            conn,
            JOB_NAME,
            codes,
            meta={
                "lookback_days": lookback_days,
                "index_count": len(codes),
                "session_date": session_day.isoformat(),
            },
        )
        summary = sync_all_index_levels(
            conn,
            today=session_day,
            lookback_days=lookback_days,
        )
        summary.run_id = run_id
        finish_sync_run(
            conn,
            run_id,
            success_codes=summary.success_codes,
            failures=summary.errors,
            meta=summary.to_dict(),
        )
    finally:
        conn.close()
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def main(_argv: list[str] | None = None) -> int:
    summary = run()
    print(
        f"{JOB_NAME} status={summary.status} "
        f"success={len(summary.success_codes)} failed={len(summary.failure_codes)}"
    )
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
