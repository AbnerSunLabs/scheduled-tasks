"""官网双源校验：上交所 ETF + 中证指数 vs 库内主写。

默认只比对、不写库；``--apply-official`` 才对 mismatch 行 UPDATE。
缺日不 INSERT（补缺仍归 yfinance / 红色火箭）。
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import (
    connect,
    create_sync_run,
    fetch_etf_daily_rows,
    fetch_etf_pool,
    fetch_etf_valuation_snapshot,
    fetch_index_daily_prices,
    finish_sync_run,
    update_etf_daily_ohlc_official,
    update_etf_valuation_pe_official,
    update_index_daily_close_official,
)
from scheduled_tasks.etf.csindex_client import fetch_index_window
from scheduled_tasks.etf.sse_client import fetch_etf_daily_bars

JOB_NAME = "sync_official_cross_check"
SUMMARY_PATH = Path("artifacts/sync_official_cross_check_summary.json")

DEFAULT_ETF_CODE = "512170"
DEFAULT_INDEX_CODE = "399989.SZ"
DEFAULT_EPSILON = 0.001
# PE 口径跨源常差约 2 点；默认 3.0 滤噪声，收紧用 --pe-epsilon
DEFAULT_PE_EPSILON = 3.0
DEFAULT_LOOKBACK_BARS = 30
DEFAULT_INDEX_LOOKBACK_DAYS = 45
MAX_MISMATCH_SAMPLES = 50
# 全池连打 yunhq 易断连；标的间稍作间隔
INTER_SYMBOL_DELAY_SEC = 0.8
# 与 yfinance job 一致：池断言排除黑名单
EXCLUDED_POOL_CODES = frozenset({"512660", "159992"})

ETF_COMPARE_FIELDS = ("open", "high", "low", "close")


@dataclass
class SyncSummary:
    status: str = "running"
    success_count: int = 0
    failure_count: int = 0
    etf_code: str = DEFAULT_ETF_CODE
    index_code: str = DEFAULT_INDEX_CODE
    apply_official: bool = False
    from_pool: bool = False
    etf_codes_checked: list[str] = field(default_factory=list)
    etf_codes_skipped_szse: list[str] = field(default_factory=list)
    etf_official_fetched: int = 0
    etf_validated: int = 0
    etf_mismatch_count: int = 0
    etf_missing_in_db: int = 0
    etf_applied: int = 0
    index_official_fetched: int = 0
    index_validated: int = 0
    index_mismatch_count: int = 0
    index_missing_in_db: int = 0
    index_applied: int = 0
    valuation_validated: int = 0
    valuation_mismatch_count: int = 0
    valuation_applied: int = 0
    source_errors: list[str] = field(default_factory=list)
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_name": JOB_NAME,
            "status": self.status,
            "etf_code": self.etf_code,
            "index_code": self.index_code,
            "apply_official": self.apply_official,
            "from_pool": self.from_pool,
            "etf_codes_checked": self.etf_codes_checked,
            "etf_codes_skipped_szse": self.etf_codes_skipped_szse,
            "etf_official_fetched": self.etf_official_fetched,
            "etf_validated": self.etf_validated,
            "etf_mismatch_count": self.etf_mismatch_count,
            "etf_missing_in_db": self.etf_missing_in_db,
            "etf_applied": self.etf_applied,
            "index_official_fetched": self.index_official_fetched,
            "index_validated": self.index_validated,
            "index_mismatch_count": self.index_mismatch_count,
            "index_missing_in_db": self.index_missing_in_db,
            "index_applied": self.index_applied,
            "valuation_validated": self.valuation_validated,
            "valuation_mismatch_count": self.valuation_mismatch_count,
            "valuation_applied": self.valuation_applied,
            "source_errors": self.source_errors,
            "mismatches": self.mismatches,
            "error": self.error,
        }


def values_mismatch(a: Any, b: Any, *, epsilon: float) -> bool:
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    try:
        return abs(float(a) - float(b)) > epsilon
    except (TypeError, ValueError):
        return True


def _jsonable_num(value: Any) -> Any:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def compare_fields(
    db_row: dict[str, Any],
    remote: dict[str, Any],
    fields: tuple[str, ...],
    *,
    epsilon: float,
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for name in fields:
        if name not in remote:
            continue
        if values_mismatch(db_row.get(name), remote.get(name), epsilon=epsilon):
            diffs.append(
                {
                    "field": name,
                    "db": _jsonable_num(db_row.get(name)),
                    "official": _jsonable_num(remote.get(name)),
                }
            )
    return diffs


def _append_mismatch(summary: SyncSummary, item: dict[str, Any]) -> None:
    if len(summary.mismatches) < MAX_MISMATCH_SAMPLES:
        summary.mismatches.append(item)


def cross_check_etf(
    conn: Any,
    etf_code: str,
    official_rows: list[dict[str, Any]],
    *,
    epsilon: float,
    apply_official: bool,
    summary: SyncSummary,
) -> None:
    summary.etf_official_fetched = len(official_rows)
    if not official_rows:
        return
    dates = [r["trade_date"] for r in official_rows]
    db_by_date = fetch_etf_daily_rows(conn, etf_code, dates)
    to_apply: list[dict[str, Any]] = []
    for remote in official_rows:
        td = remote["trade_date"]
        db_row = db_by_date.get(td)
        if db_row is None:
            summary.etf_missing_in_db += 1
            continue
        summary.etf_validated += 1
        diffs = compare_fields(db_row, remote, ETF_COMPARE_FIELDS, epsilon=epsilon)
        if not diffs:
            continue
        summary.etf_mismatch_count += 1
        _append_mismatch(
            summary,
            {
                "kind": "etf_daily",
                "etf_code": etf_code,
                "trade_date": td.isoformat(),
                "diffs": diffs,
            },
        )
        if apply_official:
            to_apply.append(remote)
    if apply_official and to_apply:
        summary.etf_applied = update_etf_daily_ohlc_official(conn, to_apply)


def cross_check_index(
    conn: Any,
    index_code: str,
    official_rows: list[dict[str, Any]],
    *,
    epsilon: float,
    pe_epsilon: float,
    apply_official: bool,
    summary: SyncSummary,
) -> None:
    summary.index_official_fetched = len(official_rows)
    if not official_rows:
        return
    dates = [r["trade_date"] for r in official_rows]
    db_by_date = fetch_index_daily_prices(conn, index_code, dates)
    to_apply: list[dict[str, Any]] = []
    for remote in official_rows:
        td = remote["trade_date"]
        db_row = db_by_date.get(td)
        if db_row is None:
            summary.index_missing_in_db += 1
            continue
        summary.index_validated += 1
        # 中证官网 close 通常两位小数；库内可能有更高精度噪声
        db_close = db_row.get("close")
        off_close = remote.get("close")
        db_cmp = None if db_close is None else round(float(db_close), 2)
        off_cmp = None if off_close is None else round(float(off_close), 2)
        if values_mismatch(db_cmp, off_cmp, epsilon=epsilon):
            summary.index_mismatch_count += 1
            _append_mismatch(
                summary,
                {
                    "kind": "index_daily_prices",
                    "index_code": index_code,
                    "trade_date": td.isoformat(),
                    "diffs": [
                        {
                            "field": "close",
                            "db": _jsonable_num(db_close),
                            "official": _jsonable_num(off_close),
                            "db_rounded": db_cmp,
                            "official_rounded": off_cmp,
                        }
                    ],
                },
            )
            if apply_official:
                to_apply.append(
                    {
                        "index_code": index_code,
                        "trade_date": td,
                        "close": remote["close"],
                    }
                )
    if apply_official and to_apply:
        summary.index_applied = update_index_daily_close_official(conn, to_apply)

    # 估值：用官网窗口最新一日 PE vs 快照
    latest_with_pe = next(
        (r for r in reversed(official_rows) if r.get("current_pe_ttm") is not None),
        None,
    )
    if latest_with_pe is None:
        return
    existing = fetch_etf_valuation_snapshot(conn, index_code)
    if existing is None:
        return
    summary.valuation_validated = 1
    if values_mismatch(
        existing.get("current_pe_ttm"),
        latest_with_pe.get("current_pe_ttm"),
        epsilon=pe_epsilon,
    ):
        summary.valuation_mismatch_count = 1
        _append_mismatch(
            summary,
            {
                "kind": "etf_valuation",
                "tracking_index_code": index_code,
                "trade_date": latest_with_pe["trade_date"].isoformat(),
                "diffs": [
                    {
                        "field": "current_pe_ttm",
                        "db": _jsonable_num(existing.get("current_pe_ttm")),
                        "official": _jsonable_num(
                            latest_with_pe.get("current_pe_ttm")
                        ),
                    }
                ],
            },
        )
        if apply_official:
            summary.valuation_applied = update_etf_valuation_pe_official(
                conn,
                tracking_index_code=index_code,
                trade_date=latest_with_pe["trade_date"],
                current_pe_ttm=float(latest_with_pe["current_pe_ttm"]),
            )


def write_summary(path: Path, summary: SyncSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def _default(obj: Any) -> Any:
        if isinstance(obj, date):
            return obj.isoformat()
        # psycopg Decimal 等
        try:
            from decimal import Decimal

            if isinstance(obj, Decimal):
                return float(obj)
        except Exception:  # noqa: BLE001
            pass
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    path.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, default=_default)
        + "\n"
    )


def run(
    *,
    etf_code: str = DEFAULT_ETF_CODE,
    index_code: str = DEFAULT_INDEX_CODE,
    mode: str = "incremental",
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    index_lookback_days: int = DEFAULT_INDEX_LOOKBACK_DAYS,
    epsilon: float = DEFAULT_EPSILON,
    pe_epsilon: float = DEFAULT_PE_EPSILON,
    apply_official: bool = False,
    from_pool: bool = False,
    skip_index: bool = False,
    end: date | None = None,
) -> SyncSummary:
    settings = load_settings()
    summary = SyncSummary(
        etf_code=etf_code,
        index_code="" if skip_index and from_pool else index_code,
        apply_official=apply_official,
        from_pool=from_pool,
    )
    end = end or date.today()
    # full：上交所 begin 用较大窗口；中证用较长日历跨度
    etf_bars = 2000 if mode == "full" else lookback_bars
    idx_days = 4000 if mode == "full" else index_lookback_days

    conn = connect(settings.database_url)
    run_id: int | None = None
    try:
        if from_pool:
            pool_rows = fetch_etf_pool(conn, excluded_codes=sorted(EXCLUDED_POOL_CODES))
            all_codes = [r["etf_code"] for r in pool_rows]
            sse_codes = [c for c in all_codes if c.startswith("5")]
            szse_codes = [c for c in all_codes if not c.startswith("5")]
            summary.etf_codes_skipped_szse = szse_codes
            etf_codes = sse_codes
            summary.etf_code = ",".join(etf_codes) if etf_codes else ""
        else:
            etf_codes = [etf_code]

        summary.etf_codes_checked = list(etf_codes)
        tracked_codes = list(etf_codes)
        if not skip_index:
            tracked_codes.append(index_code)

        run_id = create_sync_run(
            conn,
            JOB_NAME,
            tracked_codes,
            meta={
                "source": "official_cross_check",
                "mode": mode,
                "apply_official": apply_official,
                "from_pool": from_pool,
                "skip_index": skip_index,
                "epsilon": epsilon,
                "lookback_bars": None if mode == "full" else lookback_bars,
                "end": end.isoformat(),
                "skipped_szse": summary.etf_codes_skipped_szse,
            },
        )

        for idx, code in enumerate(etf_codes):
            try:
                etf_rows = fetch_etf_daily_bars(code, max_bars=etf_bars)
                cross_check_etf(
                    conn,
                    code,
                    etf_rows,
                    epsilon=epsilon,
                    apply_official=apply_official,
                    summary=summary,
                )
            except Exception as exc:  # noqa: BLE001
                summary.source_errors.append(f"sse:{code}:{exc}")
                summary.failure_count += 1
            if idx + 1 < len(etf_codes):
                time.sleep(INTER_SYMBOL_DELAY_SEC)

        if not skip_index:
            try:
                index_rows = fetch_index_window(
                    index_code, lookback_days=idx_days, end=end
                )
                for row in index_rows:
                    row["index_code"] = index_code
                cross_check_index(
                    conn,
                    index_code,
                    index_rows,
                    epsilon=epsilon,
                    pe_epsilon=pe_epsilon,
                    apply_official=apply_official,
                    summary=summary,
                )
            except Exception as exc:  # noqa: BLE001
                summary.source_errors.append(f"csindex:{exc}")
                summary.failure_count += 1

        mismatch_total = (
            summary.etf_mismatch_count
            + summary.index_mismatch_count
            + summary.valuation_mismatch_count
        )
        if summary.source_errors and summary.etf_validated == 0 and summary.index_validated == 0:
            summary.status = "failed"
            summary.success_count = 0
        elif mismatch_total > 0 or summary.source_errors:
            summary.status = "partial"
            summary.success_count = 1 if (
                summary.etf_validated or summary.index_validated
            ) else 0
            summary.failure_count = max(summary.failure_count, 1)
        else:
            summary.status = "success"
            summary.success_count = 1

        failures: list[dict[str, str]] = [
            {"code": "source", "error": e} for e in summary.source_errors
        ]
        if mismatch_total > 0 and not failures:
            failures = [
                {
                    "code": "mismatch",
                    "error": (
                        f"etf={summary.etf_mismatch_count} "
                        f"index={summary.index_mismatch_count} "
                        f"valuation={summary.valuation_mismatch_count}"
                    ),
                }
            ]
        success_codes = [] if summary.status == "failed" else list(tracked_codes)
        finish_sync_run(
            conn,
            run_id,
            success_codes=success_codes,
            failures=failures,
            meta={
                "status": summary.status,
                "apply_official": apply_official,
                "from_pool": from_pool,
                "etf_codes_checked": summary.etf_codes_checked,
                "etf_codes_skipped_szse": summary.etf_codes_skipped_szse,
                "etf_mismatch_count": summary.etf_mismatch_count,
                "index_mismatch_count": summary.index_mismatch_count,
                "valuation_mismatch_count": summary.valuation_mismatch_count,
                "etf_missing_in_db": summary.etf_missing_in_db,
                "index_missing_in_db": summary.index_missing_in_db,
                "etf_applied": summary.etf_applied,
                "index_applied": summary.index_applied,
                "valuation_applied": summary.valuation_applied,
                "mismatch_samples": summary.mismatches[:20],
            },
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        summary.status = "failed"
        summary.error = str(exc)
        summary.failure_count = 1
        conn.rollback()
        if run_id is not None:
            try:
                finish_sync_run(
                    conn,
                    run_id,
                    success_codes=[],
                    failures=[
                        {
                            "code": "job",
                            "error": str(exc),
                        }
                    ],
                    meta={
                        "status": "failed",
                        "error": str(exc),
                        "trace": traceback.format_exc()[-2000:],
                    },
                )
                conn.commit()
            except Exception:  # noqa: BLE001
                conn.rollback()
    finally:
        conn.close()

    write_summary(SUMMARY_PATH, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Official cross-check: SSE ETF + CSI index vs DB."
    )
    parser.add_argument("--etf-code", default=DEFAULT_ETF_CODE)
    parser.add_argument("--index-code", default=DEFAULT_INDEX_CODE)
    parser.add_argument(
        "--from-pool",
        action="store_true",
        help="Validate all SSE (5xxxxx) ETFs in etf_pool; SZSE codes are skipped.",
    )
    parser.add_argument(
        "--skip-index",
        action="store_true",
        help="Skip CSI index/PE check (recommended with --from-pool).",
    )
    parser.add_argument(
        "--mode", choices=("incremental", "full"), default="incremental"
    )
    parser.add_argument("--lookback-bars", type=int, default=DEFAULT_LOOKBACK_BARS)
    parser.add_argument(
        "--index-lookback-days", type=int, default=DEFAULT_INDEX_LOOKBACK_DAYS
    )
    parser.add_argument("--epsilon", type=float, default=DEFAULT_EPSILON)
    parser.add_argument(
        "--pe-epsilon",
        type=float,
        default=DEFAULT_PE_EPSILON,
        help="PE TTM absolute tolerance (default 3.0; CSI vs hongsehuojian often ~2).",
    )
    parser.add_argument(
        "--apply-official",
        action="store_true",
        help="UPDATE mismatch rows from official sources (default: compare-only).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required together with --apply-official to confirm writes.",
    )
    args = parser.parse_args(argv)

    if args.apply_official and not args.yes:
        print("refusing --apply-official without --yes")
        return 2

    skip_index = args.skip_index or args.from_pool
    summary = run(
        etf_code=args.etf_code,
        index_code=args.index_code,
        mode=args.mode,
        lookback_bars=args.lookback_bars,
        index_lookback_days=args.index_lookback_days,
        epsilon=args.epsilon,
        pe_epsilon=args.pe_epsilon,
        apply_official=args.apply_official,
        from_pool=args.from_pool,
        skip_index=skip_index,
    )
    print(
        f"{JOB_NAME} status={summary.status} "
        f"etf_checked={len(summary.etf_codes_checked)} "
        f"etf_mismatch={summary.etf_mismatch_count} "
        f"index_mismatch={summary.index_mismatch_count} "
        f"valuation_mismatch={summary.valuation_mismatch_count} "
        f"skipped_szse={len(summary.etf_codes_skipped_szse)} "
        f"applied={summary.apply_official}"
    )
    if summary.status == "failed":
        return 1
    # 价差才算准确性失败；个别源瞬时断连且已有有效比对 → 不让 GHA 红灯
    if (
        summary.etf_mismatch_count
        or summary.index_mismatch_count
        or summary.valuation_mismatch_count
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
