"""把场外基金单位净值写入 fund_nav_daily。当前同步 004752 与 502010。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import connect, create_sync_run, finish_sync_run, upsert_fund_nav_daily
from scheduled_tasks.etf.eastmoney_fund_nav import fetch_fund_nav
from scheduled_tasks.jobs.sync_index_levels import resolve_session_date

JOB_NAME = "sync_fund_nav"
FUND_CODES: tuple[str, ...] = ("004752", "502010")
SUMMARY_PATH = Path("artifacts/sync_fund_nav_summary.json")


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


def covers_session(rows: list[dict[str, Any]], session_day: date) -> bool:
    target = session_day.isoformat()
    return any(str(row.get("nav_date")) == target for row in rows)


def sync_fund_navs(conn: Any, *, session_day: date) -> SyncSummary:
    summary = SyncSummary()
    for fund_code in FUND_CODES:
        try:
            rows = fetch_fund_nav(fund_code)
            written = upsert_fund_nav_daily(conn, rows)
            conn.commit()
            summary.row_counts[fund_code] = written
            if covers_session(rows, session_day):
                summary.success_codes.append(fund_code)
            else:
                _mark_missing(summary, fund_code, session_day)
        except Exception as exc:  # noqa: BLE001 — 单只失败只回滚这一只
            conn.rollback()
            summary.failure_codes.append(fund_code)
            summary.errors.append({"fund_code": fund_code, "error": str(exc)})
    _set_status(summary)
    return summary


def _mark_missing(summary: SyncSummary, fund_code: str, session_day: date) -> None:
    summary.failure_codes.append(fund_code)
    summary.errors.append(
        {
            "fund_code": fund_code,
            "error": f"unit nav for {session_day.isoformat()} is not published yet",
        }
    )


def _set_status(summary: SyncSummary) -> None:
    if summary.failure_codes and summary.success_codes:
        summary.status = "partial"
    elif summary.failure_codes:
        summary.status = "failed"


def run() -> SyncSummary:
    settings = load_settings()
    session_day = resolve_session_date(os.environ.get("SESSION_DATE"))
    conn = connect(settings.database_url)
    summary = SyncSummary()
    try:
        run_id = create_sync_run(
            conn,
            JOB_NAME,
            FUND_CODES,
            meta={"session_date": session_day.isoformat(), "fund_count": len(FUND_CODES)},
        )
        summary = sync_fund_navs(conn, session_day=session_day)
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
