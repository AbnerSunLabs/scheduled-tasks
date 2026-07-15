"""国内机：BaoStock 写入 trade_calendar(market='CN')。

禁止把日历塞进 sync_runs.meta；日历进正式表。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import connect, create_sync_run, finish_sync_run, upsert_trade_calendar
from scheduled_tasks.etf.baostock_client import fetch_cn_trade_calendar

JOB_NAME = "sync_trade_calendar_baostock"
SUMMARY_PATH = Path("artifacts/sync_trade_calendar_baostock_summary.json")


@dataclass
class CalendarSummary:
    status: str = "running"
    start: str | None = None
    end: str | None = None
    upserted: int = 0
    open_days: int = 0
    closed_days: int = 0
    cross_check: dict[str, Any] = field(default_factory=dict)
    error_summary: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "start": self.start,
            "end": self.end,
            "upserted": self.upserted,
            "open_days": self.open_days,
            "closed_days": self.closed_days,
            "cross_check": self.cross_check,
            "error_summary": self.error_summary,
        }


def write_summary(summary: CalendarSummary) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cross_check_etf_daily(conn: Any, start: date, end: date) -> dict[str, Any]:
    """与 etf_daily 交叉：开市日在主行情中缺席较多则记缺口。"""
    sql = """
    with open_days as (
      select cal_date
      from public.trade_calendar
      where market = 'CN'
        and is_open = true
        and cal_date between %s and %s
    ),
    etf_days as (
      select distinct trade_date
      from public.etf_daily
      where trade_date between %s and %s
    )
    select
      (select count(*) from open_days) as open_day_count,
      (select count(*) from etf_days) as etf_day_count,
      (
        select count(*)
        from open_days o
        left join etf_days e on e.trade_date = o.cal_date
        where e.trade_date is null
      ) as open_days_missing_in_etf_daily
    """
    with conn.cursor() as cur:
        cur.execute(sql, (start, end, start, end))
        row = cur.fetchone() or {}
    missing = int(row.get("open_days_missing_in_etf_daily") or 0)
    open_count = int(row.get("open_day_count") or 0)
    return {
        "open_day_count": open_count,
        "etf_day_count": int(row.get("etf_day_count") or 0),
        "open_days_missing_in_etf_daily": missing,
        "large_gap": bool(open_count > 0 and missing / open_count > 0.2),
    }


def run(start: date, end: date) -> CalendarSummary:
    settings = load_settings()
    summary = CalendarSummary(start=start.isoformat(), end=end.isoformat())
    with connect(settings.database_url) as conn:
        run_id = create_sync_run(
            conn,
            JOB_NAME,
            ("CN",),
            meta={"start": summary.start, "end": summary.end},
        )
        try:
            rows = fetch_cn_trade_calendar(start, end)
            summary.upserted = upsert_trade_calendar(conn, rows)
            summary.open_days = sum(1 for r in rows if r["is_open"])
            summary.closed_days = summary.upserted - summary.open_days
            conn.commit()
            summary.cross_check = cross_check_etf_daily(conn, start, end)
            summary.status = "success"
            finish_sync_run(
                conn,
                run_id,
                ["CN"],
                [],
                meta={
                    "start": summary.start,
                    "end": summary.end,
                    "upserted": summary.upserted,
                    "open_days": summary.open_days,
                    "closed_days": summary.closed_days,
                    "cross_check": summary.cross_check,
                    # 日历本体在 trade_calendar 表，禁止塞进 meta
                },
            )
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            summary.status = "failed"
            summary.error_summary = [
                {"code": "CN", "error": str(exc), "type": type(exc).__name__}
            ]
            finish_sync_run(conn, run_id, [], summary.error_summary)

    write_summary(summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="BaoStock CN trade calendar sync")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run(date.fromisoformat(args.start), date.fromisoformat(args.end))
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
