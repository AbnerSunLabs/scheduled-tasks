"""国内机：用 AKShare 补齐 etf_daily.amount（UPDATE-only）。

禁止跑在 GitHub 官方 runner / Supabase Edge。
不 INSERT 残缺行情行；不覆盖 OHLC / price_source / updated_at。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import (
    connect,
    create_sync_run,
    fetch_etf_amount_pending_dates,
    fetch_etf_pool,
    finish_sync_run,
    get_etf_max_trade_date,
    update_etf_daily_enrichment,
)
from scheduled_tasks.etf.akshare_client import (
    fetch_etf_amount_hist,
    new_akshare_give_up_state,
    to_six_digit_code,
)

JOB_NAME = "sync_etf_enrich_akshare"
EXCLUDED_CODES = frozenset({"512660", "159992"})
SUMMARY_PATH = Path("artifacts/sync_etf_enrich_akshare_summary.json")
DEFAULT_LOOKBACK_DAYS = 10
FULL_HISTORY_YEARS = 5


@dataclass
class EnrichSummary:
    mode: str
    status: str = "running"
    success_count: int = 0
    failure_count: int = 0
    codes: list[str] = field(default_factory=list)
    updated_count: int = 0
    unmatched_count: int = 0
    unmatched_sample: list[dict[str, str]] = field(default_factory=list)
    window_failures: list[dict[str, Any]] = field(default_factory=list)
    error_summary: list[dict[str, str]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "status": self.status,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "codes": self.codes,
            "updated_count": self.updated_count,
            "unmatched_count": self.unmatched_count,
            "unmatched_sample": self.unmatched_sample,
            "window_failures": self.window_failures,
            "error_summary": self.error_summary,
            "coverage": self.coverage,
        }


def write_summary(summary: EnrichSummary) -> None:
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
        code = to_six_digit_code(item.strip())
        if code in EXCLUDED_CODES:
            continue
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return tuple(codes)


def resolve_codes(conn: Any, codes_arg: tuple[str, ...] | None) -> list[str]:
    if codes_arg is not None:
        return list(codes_arg)
    pool = fetch_etf_pool(conn, excluded_codes=tuple(EXCLUDED_CODES))
    return [to_six_digit_code(str(row["etf_code"])) for row in pool]


def resolve_range(
    conn: Any,
    code: str,
    mode: str,
    *,
    today: date | None = None,
) -> tuple[date, date]:
    today = today or date.today()
    if mode == "full":
        return today - timedelta(days=365 * FULL_HISTORY_YEARS + 10), today
    last = get_etf_max_trade_date(conn, code)
    if last is None:
        return today - timedelta(days=DEFAULT_LOOKBACK_DAYS), today
    return last - timedelta(days=DEFAULT_LOOKBACK_DAYS), today


def compute_per_etf_amount_coverage(conn: Any, codes: list[str]) -> dict[str, Any]:
    """逐标的最近 250 行 amount 非空率；以请求 codes 为驱动（无主行情也进结果）。"""
    if not codes:
        return {
            "per_etf": [],
            "insufficient_history": [],
            "below_95_with_full_history": [],
            "missing_ohlcv": [],
        }
    sql = """
    with requested as (
      select unnest(%s::text[]) as etf_code
    ),
    ranked as (
      select etf_code, trade_date, amount,
             row_number() over (partition by etf_code order by trade_date desc) as rn
      from public.etf_daily
      where etf_code = any(%s)
    ),
    latest as (
      select * from ranked where rn <= 250
    ),
    agg as (
      select etf_code,
             count(*)::int as rows_in_denom,
             count(amount)::int as amount_nonnull,
             (count(amount)::float / nullif(count(*), 0)) as fill_rate
      from latest
      group by etf_code
    )
    select r.etf_code,
           coalesce(a.rows_in_denom, 0)::int as rows_in_denom,
           coalesce(a.amount_nonnull, 0)::int as amount_nonnull,
           a.fill_rate
    from requested r
    left join agg a on a.etf_code = r.etf_code
    order by r.etf_code
    """
    with conn.cursor() as cur:
        cur.execute(sql, (codes, codes))
        rows = list(cur.fetchall())

    per_etf: list[dict[str, Any]] = []
    insufficient: list[str] = []
    below_95: list[str] = []
    missing_ohlcv: list[str] = []
    for row in rows:
        code = str(row["etf_code"])
        denom = int(row["rows_in_denom"])
        fill_raw = row["fill_rate"]
        fill = float(fill_raw) if fill_raw is not None else 0.0
        item = {
            "etf_code": code,
            "rows_in_denom": denom,
            "amount_nonnull": int(row["amount_nonnull"]),
            "fill_rate": fill if denom > 0 else None,
        }
        per_etf.append(item)
        if denom == 0:
            missing_ohlcv.append(code)
        elif denom < 250:
            insufficient.append(code)
        elif fill < 0.95:
            below_95.append(code)
    return {
        "per_etf": per_etf,
        "insufficient_history": insufficient,
        "below_95_with_full_history": below_95,
        "missing_ohlcv": missing_ohlcv,
    }


def run(mode: str, codes_arg: tuple[str, ...] | None = None) -> EnrichSummary:
    if mode not in {"incremental", "full"}:
        raise ValueError(f"unsupported mode: {mode}")

    settings = load_settings()
    summary = EnrichSummary(mode=mode)
    run_id: int | None = None
    success_codes: list[str] = []
    failures: list[dict[str, str]] = []

    try:
        with connect(settings.database_url) as conn:
            codes = resolve_codes(conn, codes_arg)
            summary.codes = codes
            run_id = create_sync_run(conn, JOB_NAME, tuple(codes), meta={"mode": mode})

            all_unmatched: list[dict[str, Any]] = []
            # 东财熔断跨 ETF 共享，避免每只重新空等 5 分钟
            akshare_give_up = new_akshare_give_up_state()

            for code in codes:
                try:
                    start, end = resolve_range(conn, code, mode)
                    pending = fetch_etf_amount_pending_dates(conn, code, start, end)
                    rows, window_failures = fetch_etf_amount_hist(
                        code,
                        start,
                        end,
                        akshare_give_up=akshare_give_up,
                        expected_dates=set(pending),
                    )
                    summary.window_failures.extend(window_failures)
                    result = update_etf_daily_enrichment(conn, rows)
                    conn.commit()
                    summary.updated_count += result.updated_count
                    all_unmatched.extend(result.unmatched)
                    # 有窗口失败或零有效行 → 不进 success_codes（避免空响应误报成功）
                    if window_failures:
                        sample = window_failures[0].get("error", "unknown")
                        failures.append(
                            {
                                "code": code,
                                "error": (
                                    f"window_failures={len(window_failures)}; "
                                    f"sample={sample}"
                                ),
                                "type": "window_failure",
                            }
                        )
                    elif not rows:
                        failures.append(
                            {
                                "code": code,
                                "error": "empty_result",
                                "type": "empty_result",
                            }
                        )
                    elif mode == "full" and result.updated_count == 0:
                        # 远端有数据但库无主行情 / 全部 unmatched → 禁止假成功
                        failures.append(
                            {
                                "code": code,
                                "error": (
                                    f"zero_updates unmatched={len(result.unmatched)}"
                                ),
                                "type": "zero_updates",
                            }
                        )
                    else:
                        success_codes.append(code)
                except Exception as exc:  # noqa: BLE001
                    conn.rollback()
                    failures.append(
                        {"code": code, "error": str(exc), "type": type(exc).__name__}
                    )

            summary.coverage = compute_per_etf_amount_coverage(conn, codes)
            # full 回填后才用近 250 行 fill_rate / 无主行情门禁；incremental 只观测
            if mode == "full":
                gate_codes = list(
                    summary.coverage.get("below_95_with_full_history") or []
                ) + list(summary.coverage.get("missing_ohlcv") or [])
                if gate_codes:
                    gate_set = set(gate_codes)
                    success_codes[:] = [c for c in success_codes if c not in gate_set]
                    failed_codes = {f["code"] for f in failures}
                    for code in gate_codes:
                        if code in failed_codes:
                            continue
                        if code in (summary.coverage.get("missing_ohlcv") or []):
                            err, typ = "missing_ohlcv", "coverage"
                        else:
                            err, typ = "amount_fill_rate_below_95", "coverage"
                        failures.append({"code": code, "error": err, "type": typ})

            summary.success_count = len(success_codes)
            summary.failure_count = len(failures)
            summary.error_summary = failures
            summary.unmatched_count = len(all_unmatched)
            summary.unmatched_sample = [
                {"etf_code": u["etf_code"], "trade_date": str(u["trade_date"])}
                for u in all_unmatched[:50]
            ]
            summary.status = (
                "success"
                if not failures
                else ("partial" if success_codes else "failed")
            )

            finish_sync_run(
                conn,
                run_id,
                success_codes,
                failures,
                meta={
                    "mode": mode,
                    "updated_count": summary.updated_count,
                    "unmatched_count": summary.unmatched_count,
                    "unmatched": summary.unmatched_sample,
                    "window_failures": summary.window_failures[:100],
                    "coverage": summary.coverage,
                },
            )
    except Exception as exc:  # noqa: BLE001 — create_sync_run 已提交后须落库失败态
        summary.status = "failed"
        failures.append(
            {"code": "*", "error": str(exc), "type": type(exc).__name__}
        )
        summary.success_count = len(success_codes)
        summary.failure_count = len(failures)
        summary.error_summary = failures
        print(f"[enrich] fatal: {exc}")
        if run_id is not None:
            try:
                # 原连接可能已坏/已关，另开连接收口，避免 sync_runs 永久 running
                with connect(settings.database_url) as finish_conn:
                    finish_sync_run(
                        finish_conn,
                        run_id,
                        success_codes,
                        failures,
                        meta={"fatal": str(exc), "mode": mode},
                    )
            except Exception as finish_error:  # noqa: BLE001
                print(f"[enrich] finish_sync_run after fatal failed: {finish_error}")
    finally:
        write_summary(summary)

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AKShare ETF amount enrichment")
    parser.add_argument("--mode", choices=("incremental", "full"), default="incremental")
    parser.add_argument("--codes", default=None, help="逗号分隔 6 位码，如 510300,159915")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    codes = parse_codes_arg(args.codes)
    summary = run(args.mode, codes)
    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    # 与主行情 job 一致：partial / failed 均非 0，便于 workflow 告警
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
