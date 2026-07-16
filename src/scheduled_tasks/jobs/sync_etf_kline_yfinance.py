"""Sync ETF daily bars from Yahoo Finance into Supabase PostgreSQL.

海外 GitHub Actions runner 上 BaoStock / 东财 AkShare 不可用，故价格主源为 yfinance。
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import (
    connect,
    count_etf_rows,
    create_sync_run,
    existing_trade_dates,
    fetch_etf_pool,
    finish_sync_run,
    get_etf_anchor_qfq,
    get_etf_hfq_scale,
    get_etf_max_trade_date,
    update_etf_adj_columns,
    upsert_etf_daily_bars,
)
from scheduled_tasks.etf.yfinance_client import (
    DEFAULT_HISTORY_START,
    build_adj_only,
    build_three_adjustments,
    fetch_anchor_close_qfq,
    fetch_kline_bundle,
)

JOB_NAME = "sync_etf_kline_yfinance"
EXCLUDED_CODES = frozenset({"512660", "159992"})
EXPECTED_POOL_SIZE = 25
ETF_CODE_RE = re.compile(r"^\d{6}$")
SUMMARY_PATH = Path("artifacts/sync_etf_kline_summary.json")
DEFAULT_LOOKBACK_DAYS = 5
DEFAULT_ADJ_EPSILON = 0.001
PRICE_SOURCE = "yfinance"


@dataclass
class SyncSummary:
    mode: str
    status: str = "running"
    success_count: int = 0
    failure_count: int = 0
    pool_size: int = 0
    snapshot_date_min: str | None = None
    snapshot_date_max: str | None = None
    max_trade_date: str | None = None
    refreshed_codes: list[str] = field(default_factory=list)
    skipped_codes: list[str] = field(default_factory=list)
    detect_failed_codes: list[str] = field(default_factory=list)
    needs_full_codes: list[str] = field(default_factory=list)
    backfill_codes: list[str] = field(default_factory=list)
    # adj_check 时 Yahoo 多出、库内尚无主 K 的代码（仍刷已有日期复权，不整只失败）
    adj_gap_codes: list[str] = field(default_factory=list)
    error_summary: list[dict[str, str]] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    codes_source: str = "pool"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "pool_size": self.pool_size,
            "snapshot_date_min": self.snapshot_date_min,
            "snapshot_date_max": self.snapshot_date_max,
            "max_trade_date": self.max_trade_date,
            "refreshed_codes": self.refreshed_codes,
            "skipped_codes": self.skipped_codes,
            "detect_failed_codes": self.detect_failed_codes,
            "needs_full_codes": self.needs_full_codes,
            "backfill_codes": self.backfill_codes,
            "adj_gap_codes": self.adj_gap_codes,
            "error_summary": self.error_summary,
            "codes": self.codes,
            "codes_source": self.codes_source,
        }


def _error_summary(code: str, error: BaseException) -> dict[str, str]:
    return {
        "code": code,
        "error": str(error),
        "type": error.__class__.__name__,
    }


def write_summary(summary: SyncSummary) -> None:
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_codes_arg(raw: str | None) -> tuple[str, ...] | None:
    if raw is None or not raw.strip():
        return None
    codes: list[str] = []
    seen: set[str] = set()
    for item in raw.split(","):
        code = item.strip()
        if not code:
            continue
        if not ETF_CODE_RE.match(code):
            raise ValueError(f"invalid etf code: {code}")
        if code in EXCLUDED_CODES:
            raise ValueError(f"excluded etf code not allowed: {code}")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        raise ValueError("--codes is empty")
    return tuple(codes)


def resolve_pool_codes(
    conn: Any,
    cli_codes: tuple[str, ...] | None,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    if cli_codes is not None:
        meta = {
            "codes_source": "cli",
            "pool_size": len(cli_codes),
            "codes": list(cli_codes),
        }
        return cli_codes, meta

    rows = fetch_etf_pool(conn, excluded_codes=sorted(EXCLUDED_CODES))
    codes: list[str] = []
    seen: set[str] = set()
    snapshot_dates: list[date] = []
    for row in rows:
        code = str(row["etf_code"]).strip()
        if not ETF_CODE_RE.match(code):
            raise RuntimeError(f"invalid etf_code in pool: {code}")
        if code in EXCLUDED_CODES:
            raise RuntimeError(f"excluded code leaked into pool query: {code}")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
        if row.get("snapshot_date") is not None:
            snapshot_dates.append(row["snapshot_date"])

    if len(codes) != EXPECTED_POOL_SIZE:
        raise RuntimeError(
            f"expected {EXPECTED_POOL_SIZE} etf codes after exclusions, got {len(codes)}"
        )

    meta = {
        "codes_source": "pool",
        "pool_size": len(codes),
        "codes": codes,
        "snapshot_date_min": min(snapshot_dates).isoformat() if snapshot_dates else None,
        "snapshot_date_max": max(snapshot_dates).isoformat() if snapshot_dates else None,
    }
    return tuple(codes), meta


def _compute_start(
    *,
    last_date: date | None,
    lookback_days: int,
    cli_start: date | None,
    mode: str,
) -> date:
    if mode == "full" or last_date is None:
        start = DEFAULT_HISTORY_START
    else:
        start = max(DEFAULT_HISTORY_START, last_date - timedelta(days=lookback_days))
    if cli_start is not None:
        start = max(start, cli_start)
    return start


def _sync_full_or_incremental_one(
    conn: Any,
    etf_code: str,
    *,
    mode: str,
    lookback_days: int,
    cli_start: date | None,
    cli_end: date | None,
    summary: SyncSummary,
) -> int:
    last_date = get_etf_max_trade_date(conn, etf_code)
    if mode == "incremental" and last_date is None:
        summary.backfill_codes.append(etf_code)

    end = cli_end or date.today()
    start = _compute_start(
        last_date=last_date,
        lookback_days=lookback_days,
        cli_start=cli_start,
        mode=mode,
    )
    if start > end:
        print(f"[etf] {etf_code}: start {start} > end {end}, skip")
        return 0

    # incremental 近窗必须用库内全历史首日锚定，避免 hfq 随窗口漂移
    hfq_scale: float | None = None
    if mode == "incremental" and last_date is not None:
        hfq_scale = get_etf_hfq_scale(conn, etf_code)
        if hfq_scale is None:
            raise RuntimeError(
                f"{etf_code}: missing first-day close/close_qfq for hfq scale; run full first"
            )

    print(f"[etf] {etf_code}: last={last_date} range={start}→{end} mode={mode}")
    df = fetch_kline_bundle(etf_code, start, end)
    rows = build_three_adjustments(df, etf_code, hfq_scale=hfq_scale)

    # 有效区间内空结果视为拉取失败，避免写入 0 行却记 success
    if not rows:
        raise RuntimeError(
            f"{etf_code}: empty kline for range {start}→{end} (mode={mode}); "
            "possible yfinance outage or symbol mismatch"
        )

    if mode == "full":
        span_days = (end - start).days + 1
        if span_days >= 400 and len(rows) < 200:
            raise RuntimeError(
                f"{etf_code}: full range {start}→{end} only returned {len(rows)} bars "
                f"(span_days={span_days}); possible data truncation"
            )

    written = upsert_etf_daily_bars(conn, rows)
    if rows:
        max_d = max(r["trade_date"] for r in rows)
        if summary.max_trade_date is None or max_d.isoformat() > summary.max_trade_date:
            summary.max_trade_date = max_d.isoformat()
    return written


def _needs_adj_refresh(
    local_qfq: float | None,
    remote_qfq: float | None,
    epsilon: float,
) -> bool:
    if local_qfq is None:
        return True
    if remote_qfq is None:
        raise RuntimeError("remote anchor close_qfq is empty")
    if local_qfq == 0:
        return abs(remote_qfq - local_qfq) > 0
    return abs(remote_qfq - local_qfq) / abs(local_qfq) > epsilon


def _sync_adj_check_one(
    conn: Any,
    etf_code: str,
    *,
    force: bool,
    epsilon: float,
    cli_start: date | None,
    cli_end: date | None,
    summary: SyncSummary,
) -> str:
    def _finish_read(action: str) -> str:
        conn.commit()
        return action

    if count_etf_rows(conn, etf_code) == 0:
        summary.needs_full_codes.append(etf_code)
        return _finish_read("needs_full")

    anchor_info = get_etf_anchor_qfq(conn, etf_code)
    if anchor_info is None:
        summary.needs_full_codes.append(etf_code)
        return _finish_read("needs_full")

    anchor_date, local_qfq = anchor_info
    if not force:
        try:
            remote_qfq = fetch_anchor_close_qfq(etf_code, anchor_date)
            if not _needs_adj_refresh(local_qfq, remote_qfq, epsilon):
                summary.skipped_codes.append(etf_code)
                return _finish_read("skipped")
        except Exception as error:  # noqa: BLE001
            print(f"[etf] adj detect failed {etf_code}: {error}")
            summary.detect_failed_codes.append(etf_code)
            return _finish_read("detect_failed")

    # 后复权必须用库内全历史首日 scale；CLI --start 截窗时尤其不能用窗口首日重算
    hfq_scale = get_etf_hfq_scale(conn, etf_code)
    if hfq_scale is None:
        summary.needs_full_codes.append(etf_code)
        return _finish_read("needs_full")

    end = cli_end or date.today()
    start = DEFAULT_HISTORY_START
    if cli_start is not None:
        start = max(start, cli_start)
    if start > end:
        summary.skipped_codes.append(etf_code)
        return _finish_read("skipped")

    df = fetch_kline_bundle(etf_code, start, end)
    rows = build_adj_only(df, etf_code, hfq_scale=hfq_scale)
    if not rows:
        raise RuntimeError(
            f"{etf_code}: empty kline for adj_check range {start}→{end}"
        )

    dates = [r["trade_date"] for r in rows]
    existing = existing_trade_dates(conn, etf_code, dates)
    if not existing:
        summary.needs_full_codes.append(etf_code)
        return _finish_read("needs_full")

    # Yahoo 多出的交易日只记提示，已有主 K 的日期照常刷复权
    if len(existing) < len(dates):
        summary.adj_gap_codes.append(etf_code)
        print(
            f"[etf] adj_check {etf_code}: "
            f"{len(dates) - len(existing)} remote dates missing in etf_daily; "
            "refresh existing only"
        )
        rows = [r for r in rows if r["trade_date"] in existing]

    try:
        update_etf_adj_columns(conn, rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    summary.refreshed_codes.append(etf_code)
    return "refreshed"


def run_sync(args: argparse.Namespace) -> int:
    settings = load_settings()
    summary = SyncSummary(mode=args.mode)
    success_codes: list[str] = []
    failures: list[dict[str, str]] = []
    run_id: int | None = None
    exit_code = 0

    try:
        with connect(settings.database_url) as conn:
            cli_codes = parse_codes_arg(args.codes)
            codes, pool_meta = resolve_pool_codes(conn, cli_codes)
            summary.codes = list(codes)
            summary.codes_source = str(pool_meta.get("codes_source", "pool"))
            summary.pool_size = int(pool_meta.get("pool_size", len(codes)))
            summary.snapshot_date_min = pool_meta.get("snapshot_date_min")
            summary.snapshot_date_max = pool_meta.get("snapshot_date_max")

            meta: dict[str, Any] = {
                "mode": args.mode,
                "lookback_days": args.lookback_days,
                "adj_epsilon": args.adj_epsilon,
                "force": bool(args.force),
                "price_source": PRICE_SOURCE,
                **pool_meta,
            }
            run_id = create_sync_run(conn, JOB_NAME, codes, meta=meta)

            for etf_code in codes:
                try:
                    print(f"[etf] syncing {etf_code} mode={args.mode}")
                    if args.mode in {"full", "incremental"}:
                        written = _sync_full_or_incremental_one(
                            conn,
                            etf_code,
                            mode=args.mode,
                            lookback_days=args.lookback_days,
                            cli_start=args.start,
                            cli_end=args.end,
                            summary=summary,
                        )
                        conn.commit()
                        success_codes.append(etf_code)
                        print(f"[etf] synced {etf_code}: rows={written}")
                    else:
                        action = _sync_adj_check_one(
                            conn,
                            etf_code,
                            force=args.force,
                            epsilon=args.adj_epsilon,
                            cli_start=args.start,
                            cli_end=args.end,
                            summary=summary,
                        )
                        if action in {"detect_failed", "needs_full"}:
                            reason = (
                                "detect_failed"
                                if action == "detect_failed"
                                else "missing_primary_bars"
                            )
                            failures.append(
                                {
                                    "code": etf_code,
                                    "error": reason,
                                    "type": action,
                                }
                            )
                            print(f"[etf] adj_check {etf_code}: {action}")
                        else:
                            success_codes.append(etf_code)
                            print(f"[etf] adj_check {etf_code}: {action}")
                except Exception as error:
                    conn.rollback()
                    failures.append(_error_summary(etf_code, error))
                    print(f"[etf] failed {etf_code}: {error}")
                    print(traceback.format_exc())

            finish_meta: dict[str, Any] = {
                "mode": args.mode,
                "price_source": PRICE_SOURCE,
                "pool_size": summary.pool_size,
                "snapshot_date_min": summary.snapshot_date_min,
                "snapshot_date_max": summary.snapshot_date_max,
                "max_trade_date": summary.max_trade_date,
                "backfill_codes": summary.backfill_codes,
                "refreshed_codes": summary.refreshed_codes,
                "skipped_codes": summary.skipped_codes,
                "detect_failed_codes": summary.detect_failed_codes,
                "needs_full_codes": summary.needs_full_codes,
                "adj_gap_codes": summary.adj_gap_codes,
            }
            if run_id is not None:
                finish_sync_run(conn, run_id, success_codes, failures, meta=finish_meta)

    except Exception as error:
        exit_code = 1
        failures.append(_error_summary("*", error))
        print(f"[etf] fatal: {error}")
        print(traceback.format_exc())
        try:
            if run_id is not None:
                with connect(settings.database_url) as conn:
                    finish_sync_run(
                        conn,
                        run_id,
                        success_codes,
                        failures,
                        meta={"fatal": str(error)},
                    )
        except Exception as finish_error:  # noqa: BLE001
            print(f"[etf] finish_sync_run after fatal failed: {finish_error}")

    summary.success_count = len(success_codes)
    summary.failure_count = len(failures)
    summary.error_summary = failures
    if failures and success_codes:
        summary.status = "partial"
        exit_code = 1
    elif failures:
        summary.status = "failed"
        exit_code = 1
    else:
        summary.status = "success"
        exit_code = 0

    write_summary(summary)
    print(
        f"[etf] finished status={summary.status} "
        f"success={summary.success_count} failures={summary.failure_count}"
    )
    return exit_code


def _parse_date(value: str | None) -> date | None:
    if value is None or not value.strip():
        return None
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sync ETF daily kline from Yahoo Finance")
    parser.add_argument(
        "--mode",
        choices=("full", "incremental", "adj_check"),
        required=True,
        help="full=三种价全历史; incremental=近窗三种价; adj_check=除权检测后只刷复权列",
    )
    parser.add_argument("--start", type=str, default=None, help="全局 start 下限 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=None, help="end 日期 YYYY-MM-DD，默认今天")
    parser.add_argument(
        "--codes",
        type=str,
        default=None,
        help="逗号分隔 6 位 ETF 代码，跳过 25 只断言",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="incremental 近窗天数，默认 5",
    )
    parser.add_argument(
        "--adj-epsilon",
        type=float,
        default=DEFAULT_ADJ_EPSILON,
        help="除权判定相对阈值，默认 0.001",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="仅 adj_check：跳过检测，强制重刷 qfq/hfq",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.force and args.mode != "adj_check":
        parser.error("--force 仅可用于 --mode=adj_check")
    args.start = _parse_date(args.start)
    args.end = _parse_date(args.end)
    raise SystemExit(run_sync(args))


if __name__ == "__main__":
    main()
