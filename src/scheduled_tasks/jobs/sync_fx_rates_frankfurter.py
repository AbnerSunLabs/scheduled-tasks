"""Sync FX rates from Frankfurter (ECB) into public.fx_rates."""

from __future__ import annotations

import argparse
import json
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import (
    connect,
    create_sync_run,
    finish_sync_run,
    get_fx_max_rate_date,
    upsert_fx_rates,
)
from scheduled_tasks.fx.frankfurter_client import fetch_usd_quotes_for_range

JOB_NAME = "sync_fx_rates_frankfurter"
SUMMARY_PATH = Path("artifacts/sync_fx_rates_summary.json")
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_FULL_START = date(2015, 1, 1)


@dataclass
class SyncSummary:
    mode: str
    status: str = "running"
    success_count: int = 0
    failure_count: int = 0
    start_date: str | None = None
    end_date: str | None = None
    upserted_rows: int = 0
    rate_dates: list[str] = field(default_factory=list)
    max_rate_date: str | None = None
    error_summary: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "upserted_rows": self.upserted_rows,
            "rate_dates": self.rate_dates,
            "max_rate_date": self.max_rate_date,
            "error_summary": self.error_summary,
        }


def resolve_date_range(
    mode: str,
    *,
    lookback_days: int,
    start: date | None,
    end: date | None,
    max_existing: date | None,
) -> tuple[date, date]:
    """解析 full / incremental 的拉取区间。"""
    today = date.today()
    end_date = end or today
    if mode == "full":
        start_date = start or DEFAULT_FULL_START
    elif mode == "incremental":
        if start is not None:
            start_date = start
        elif max_existing is not None:
            start_date = max_existing - timedelta(days=max(lookback_days - 1, 0))
        else:
            start_date = end_date - timedelta(days=max(lookback_days - 1, 0))
    else:
        raise ValueError(f"unsupported mode: {mode}")
    if end_date < start_date:
        raise ValueError("end date must be >= start date")
    return start_date, end_date


def run_sync(
    *,
    mode: str,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    start: date | None = None,
    end: date | None = None,
) -> SyncSummary:
    settings = load_settings()
    summary = SyncSummary(mode=mode)
    conn = connect(settings.database_url)
    run_id: int | None = None

    try:
        max_existing = get_fx_max_rate_date(conn)
        start_date, end_date = resolve_date_range(
            mode,
            lookback_days=lookback_days,
            start=start,
            end=end,
            max_existing=max_existing,
        )
        summary.start_date = start_date.isoformat()
        summary.end_date = end_date.isoformat()

        run_id = create_sync_run(
            conn,
            JOB_NAME,
            ("USD-CNY", "USD-HKD", "HKD-CNY"),
            meta={
                "mode": mode,
                "start_date": summary.start_date,
                "end_date": summary.end_date,
                "provider": "frankfurter",
            },
        )

        rows = fetch_usd_quotes_for_range(start_date, end_date)
        upserted = upsert_fx_rates(conn, rows)
        conn.commit()

        rate_dates = sorted({row["rate_date"].isoformat() for row in rows})
        summary.upserted_rows = upserted
        summary.rate_dates = rate_dates
        summary.success_count = len(rate_dates)
        summary.failure_count = 0
        summary.status = "success"
        summary.max_rate_date = rate_dates[-1] if rate_dates else None

        finish_sync_run(
            conn,
            run_id,
            success_codes=rate_dates,
            failures=[],
            meta={
                "upserted_rows": upserted,
                "max_rate_date": summary.max_rate_date,
                "empty_window": upserted == 0,
            },
        )
    except Exception as exc:  # noqa: BLE001 — job 顶层需落库失败态
        summary.status = "failed"
        summary.failure_count = 1
        summary.error_summary = [
            {
                "error": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
        ]
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        if run_id is not None:
            try:
                with connect(settings.database_url) as finish_conn:
                    finish_sync_run(
                        finish_conn,
                        run_id,
                        success_codes=[],
                        failures=[{"error": str(exc)}],
                        meta={"fatal": str(exc)},
                    )
            except Exception as finish_error:  # noqa: BLE001
                print(f"[fx] finish_sync_run after fatal failed: {finish_error}")
        raise
    finally:
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        conn.close()

    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync FX rates from Frankfurter")
    parser.add_argument(
        "--mode",
        choices=("full", "incremental"),
        default="incremental",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="incremental 回看交易日窗口（默认 7）",
    )
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    started = datetime.now().isoformat(timespec="seconds")
    print(f"[{started}] start {JOB_NAME} mode={args.mode}")
    summary = run_sync(
        mode=args.mode,
        lookback_days=args.lookback_days,
        start=args.start,
        end=args.end,
    )
    print(
        f"done status={summary.status} days={summary.success_count} "
        f"rows={summary.upserted_rows} max_date={summary.max_rate_date}"
    )


if __name__ == "__main__":
    main()
