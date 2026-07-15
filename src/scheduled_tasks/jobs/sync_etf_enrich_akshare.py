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
    fetch_etf_pool,
    finish_sync_run,
    get_etf_max_trade_date,
    update_etf_daily_enrichment,
)
from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist, to_six_digit_code

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
    """逐标的最近 250 行（或不足时实际行数）amount 非空率。"""
    sql = """
    with ranked as (
      select etf_code, trade_date, amount,
             row_number() over (partition by etf_code order by trade_date desc) as rn
      from public.etf_daily
      where etf_code = any(%s)
    ),
    latest as (
      select * from ranked where rn <= 250
    )
    select etf_code,
           count(*)::int as rows_in_denom,
           count(amount)::int as amount_nonnull,
           (count(amount)::float / nullif(count(*), 0)) as fill_rate
    from latest
    group by etf_code
    order by etf_code
    """
    with conn.cursor() as cur:
        cur.execute(sql, (codes,))
        rows = list(cur.fetchall())

    per_etf: list[dict[str, Any]] = []
    insufficient: list[str] = []
    below_95: list[str] = []
    for row in rows:
        code = str(row["etf_code"])
        denom = int(row["rows_in_denom"])
        fill = float(row["fill_rate"] or 0.0)
        item = {
            "etf_code": code,
            "rows_in_denom": denom,
            "amount_nonnull": int(row["amount_nonnull"]),
            "fill_rate": fill,
        }
        per_etf.append(item)
        if denom < 250:
            insufficient.append(code)
        elif fill < 0.95:
            below_95.append(code)
    return {
        "per_etf": per_etf,
        "insufficient_history": insufficient,
        "below_95_with_full_history": below_95,
    }


def run(mode: str, codes_arg: tuple[str, ...] | None = None) -> EnrichSummary:
    if mode not in {"incremental", "full"}:
        raise ValueError(f"unsupported mode: {mode}")

    settings = load_settings()
    summary = EnrichSummary(mode=mode)
    with connect(settings.database_url) as conn:
        codes = resolve_codes(conn, codes_arg)
        summary.codes = codes
        run_id = create_sync_run(conn, JOB_NAME, tuple(codes), meta={"mode": mode})

        all_unmatched: list[dict[str, Any]] = []
        success_codes: list[str] = []
        failures: list[dict[str, str]] = []

        for code in codes:
            try:
                start, end = resolve_range(conn, code, mode)
                rows, window_failures = fetch_etf_amount_hist(code, start, end)
                summary.window_failures.extend(window_failures)
                result = update_etf_daily_enrichment(conn, rows)
                conn.commit()
                summary.updated_count += result.updated_count
                all_unmatched.extend(result.unmatched)
                success_codes.append(code)
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                failures.append(
                    {"code": code, "error": str(exc), "type": type(exc).__name__}
                )

        summary.success_count = len(success_codes)
        summary.failure_count = len(failures)
        summary.error_summary = failures
        summary.unmatched_count = len(all_unmatched)
        summary.unmatched_sample = [
            {"etf_code": u["etf_code"], "trade_date": str(u["trade_date"])}
            for u in all_unmatched[:50]
        ]
        summary.coverage = compute_per_etf_amount_coverage(conn, codes)
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
    return 0 if summary.status != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
