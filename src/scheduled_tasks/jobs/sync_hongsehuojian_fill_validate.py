"""从红色火箭补缺 + 校验 ETF（默认标的：医疗 ETF + 中证医疗）。

写入语义：
- ETF：缺失 (code, trade_date) → INSERT；已有行只比对不 UPDATE
- 指数估值：写入 ``index_valuation``（当日 PE + 5y/10y 均值），按指数 upsert 刷新
- 指数日指标：upsert ``index_daily_metrics`` 的 PE/PB 历史（valuation-only / incremental / full）
- 行业权重：红色火箭主源刷新 ``index_industry_weights``（删旧写新）
- 指数日线表已删除，不再写 ``index_daily_prices``
"""

from __future__ import annotations

import argparse
import json
import math
import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from scheduled_tasks.config import load_settings
from scheduled_tasks.db import (
    connect,
    create_sync_run,
    ensure_index_row,
    existing_trade_dates,
    fetch_etf_daily_rows,
    fetch_index_valuation,
    finish_sync_run,
    insert_etf_daily_bars_ignore_conflict,
    replace_index_industry_weights,
    upsert_index_daily_metrics,
    upsert_index_valuation,
)
from scheduled_tasks.etf.hongsehuojian_client import (
    fetch_etf_daily_bundle,
    fetch_index_daily_metrics_bundle,
    fetch_index_industry_weights,
    fetch_index_pe_snapshot,
)

JOB_NAME = "sync_hongsehuojian_fill_validate"
SUMMARY_PATH = Path("artifacts/sync_hongsehuojian_fill_validate_summary.json")

# 默认 allowlist
DEFAULT_ETF_CODE = "512170"
DEFAULT_INDEX_CODE = "399989.SZ"
DEFAULT_INDEX_NAME = "中证医疗"
DEFAULT_EPSILON = 0.001
MAX_MISMATCH_SAMPLES = 50

INDEX_DISPLAY_NAMES: dict[str, str] = {
    "399989.SZ": "中证医疗",
    "000300.SH": "沪深300",
}

ETF_COMPARE_FIELDS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_qfq",
    "high_qfq",
    "low_qfq",
    "close_qfq",
    "open_hfq",
    "high_hfq",
    "low_hfq",
    "close_hfq",
)

VALUATION_COMPARE_FIELDS = (
    "current_pe_ttm",
    "pe_ttm_avg_5y",
    "pe_ttm_avg_10y",
)


@dataclass
class SyncSummary:
    status: str = "running"
    success_count: int = 0
    failure_count: int = 0
    etf_code: str = DEFAULT_ETF_CODE
    index_code: str = DEFAULT_INDEX_CODE
    etf_fetched: int = 0
    etf_filled: int = 0
    etf_validated: int = 0
    etf_mismatch_count: int = 0
    index_price_fetched: int = 0
    index_price_filled: int = 0
    index_price_validated: int = 0
    index_price_mismatch_count: int = 0
    valuation_upserted: bool = False
    valuation_trade_date: str | None = None
    valuation_current_pe_ttm: float | None = None
    valuation_pe_ttm_avg_5y: float | None = None
    valuation_pe_ttm_avg_10y: float | None = None
    valuation_mismatch_count: int = 0
    index_metric_rows: int = 0
    industry_weight_rows: int = 0
    industry_weight_as_of: str | None = None
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    error_summary: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "etf_code": self.etf_code,
            "index_code": self.index_code,
            "etf_fetched": self.etf_fetched,
            "etf_filled": self.etf_filled,
            "etf_validated": self.etf_validated,
            "etf_mismatch_count": self.etf_mismatch_count,
            "index_price_fetched": self.index_price_fetched,
            "index_price_filled": self.index_price_filled,
            "index_price_validated": self.index_price_validated,
            "index_price_mismatch_count": self.index_price_mismatch_count,
            "valuation_upserted": self.valuation_upserted,
            "valuation_trade_date": self.valuation_trade_date,
            "valuation_current_pe_ttm": self.valuation_current_pe_ttm,
            "valuation_pe_ttm_avg_5y": self.valuation_pe_ttm_avg_5y,
            "valuation_pe_ttm_avg_10y": self.valuation_pe_ttm_avg_10y,
            "valuation_mismatch_count": self.valuation_mismatch_count,
            "index_metric_rows": self.index_metric_rows,
            "industry_weight_rows": self.industry_weight_rows,
            "industry_weight_as_of": self.industry_weight_as_of,
            "mismatches": self.mismatches,
            "error_summary": self.error_summary,
        }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def values_mismatch(
    left: Any,
    right: Any,
    *,
    epsilon: float,
) -> bool:
    """两侧均非空且数值差 > epsilon（或一侧空一侧非空）视为不一致。

    两侧皆空 → 一致；不把「库空 / 远端有」当成 mismatch（缺字段由其他链路补）。
    """
    a = _as_float(left)
    b = _as_float(right)
    if a is None and b is None:
        return False
    if a is None or b is None:
        return False
    if math.isnan(a) or math.isnan(b):
        return False
    return abs(a - b) > epsilon


def compare_etf_row(
    db_row: dict[str, Any],
    remote_row: dict[str, Any],
    *,
    epsilon: float,
) -> list[dict[str, Any]]:
    diffs: list[dict[str, Any]] = []
    for field_name in ETF_COMPARE_FIELDS:
        db_val = db_row.get(field_name)
        remote_val = remote_row.get(field_name)
        if values_mismatch(db_val, remote_val, epsilon=epsilon):
            diffs.append(
                {
                    "field": field_name,
                    "db": _as_float(db_val),
                    "remote": _as_float(remote_val),
                }
            )
    return diffs


def fill_and_validate_etf(
    conn: Any,
    etf_code: str,
    remote_rows: list[dict[str, Any]],
    *,
    epsilon: float,
    summary: SyncSummary,
) -> None:
    if not remote_rows:
        raise RuntimeError(f"empty remote ETF bars for {etf_code}")
    summary.etf_fetched = len(remote_rows)
    dates = [row["trade_date"] for row in remote_rows]
    existing = existing_trade_dates(conn, etf_code, dates)
    to_fill = [row for row in remote_rows if row["trade_date"] not in existing]
    to_validate = [row for row in remote_rows if row["trade_date"] in existing]

    if to_fill:
        insert_etf_daily_bars_ignore_conflict(conn, to_fill)
        summary.etf_filled = len(to_fill)

    if not to_validate:
        return

    db_by_date = fetch_etf_daily_rows(
        conn, etf_code, [row["trade_date"] for row in to_validate]
    )
    summary.etf_validated = len(to_validate)
    for remote in to_validate:
        td = remote["trade_date"]
        db_row = db_by_date.get(td)
        if db_row is None:
            continue
        diffs = compare_etf_row(db_row, remote, epsilon=epsilon)
        if not diffs:
            continue
        summary.etf_mismatch_count += 1
        if len(summary.mismatches) < MAX_MISMATCH_SAMPLES:
            summary.mismatches.append(
                {
                    "kind": "etf_daily",
                    "code": etf_code,
                    "trade_date": td.isoformat(),
                    "diffs": diffs,
                }
            )


def refresh_index_valuation_snapshot(
    conn: Any,
    index_code: str,
    remote: dict[str, Any],
    *,
    epsilon: float,
    summary: SyncSummary,
) -> None:
    """刷新 index_valuation；若库中已有旧快照则先比对再 upsert。"""
    existing = fetch_index_valuation(conn, index_code)
    if existing is not None:
        diffs: list[dict[str, Any]] = []
        for field_name in VALUATION_COMPARE_FIELDS:
            if values_mismatch(existing.get(field_name), remote.get(field_name), epsilon=epsilon):
                diffs.append(
                    {
                        "field": field_name,
                        "db": _as_float(existing.get(field_name)),
                        "remote": _as_float(remote.get(field_name)),
                    }
                )
        if diffs:
            summary.valuation_mismatch_count = 1
            if len(summary.mismatches) < MAX_MISMATCH_SAMPLES:
                summary.mismatches.append(
                    {
                        "kind": "index_valuation",
                        "code": index_code,
                        "trade_date": remote["trade_date"].isoformat()
                        if isinstance(remote["trade_date"], date)
                        else str(remote["trade_date"]),
                        "diffs": diffs,
                        "note": "snapshot refreshed after compare",
                    }
                )

    upsert_index_valuation(conn, remote)
    summary.valuation_upserted = True
    td = remote["trade_date"]
    summary.valuation_trade_date = td.isoformat() if isinstance(td, date) else str(td)
    summary.valuation_current_pe_ttm = _as_float(remote.get("current_pe_ttm"))
    summary.valuation_pe_ttm_avg_5y = _as_float(remote.get("pe_ttm_avg_5y"))
    summary.valuation_pe_ttm_avg_10y = _as_float(remote.get("pe_ttm_avg_10y"))


def refresh_index_daily_metrics(
    conn: Any,
    index_code: str,
    *,
    summary: SyncSummary,
    time_interval: str = "last_10_years",
    include_prices: bool = True,
    max_price_bars: int | None = None,
) -> None:
    """拉取并 upsert 指数 PE/PB（及收盘）日序列到 index_daily_metrics。"""
    rows = fetch_index_daily_metrics_bundle(
        index_code,
        time_interval=time_interval,
        include_prices=include_prices,
        max_price_bars=max_price_bars,
    )
    summary.index_metric_rows = upsert_index_daily_metrics(conn, rows)


def refresh_index_industry_weights(
    conn: Any,
    index_code: str,
    remote_rows: list[dict[str, Any]],
    *,
    summary: SyncSummary,
) -> None:
    """以红色火箭为主源整表替换该指数行业权重。"""
    n = replace_index_industry_weights(conn, index_code, remote_rows)
    summary.industry_weight_rows = n
    if remote_rows:
        dates = sorted({r["as_of_date"] for r in remote_rows})
        summary.industry_weight_as_of = dates[-1].isoformat()


def run(
    *,
    etf_code: str = DEFAULT_ETF_CODE,
    index_code: str = DEFAULT_INDEX_CODE,
    epsilon: float = DEFAULT_EPSILON,
    end: date | None = None,
    mode: str = "incremental",
    lookback_bars: int = 30,
) -> SyncSummary:
    if mode not in {"incremental", "full", "valuation-only"}:
        raise ValueError(f"unsupported mode: {mode}")
    settings = load_settings()
    summary = SyncSummary(etf_code=etf_code, index_code=index_code)
    codes = (etf_code, index_code)
    conn = connect(settings.database_url)
    run_id: int | None = None
    max_bars = None if mode == "full" else lookback_bars
    try:
        run_id = create_sync_run(
            conn,
            JOB_NAME,
            codes,
            meta={
                "source": "hongsehuojian",
                "mode": mode,
                "lookback_bars": lookback_bars if mode == "incremental" else None,
                "epsilon": epsilon,
                "end": (end or date.today()).isoformat(),
            },
        )
        ensure_index_row(
            conn,
            code=index_code,
            name=INDEX_DISPLAY_NAMES.get(index_code, DEFAULT_INDEX_NAME),
        )

        if mode != "valuation-only":
            remote_etf = fetch_etf_daily_bundle(etf_code, end=end, max_bars=max_bars)
            fill_and_validate_etf(conn, etf_code, remote_etf, epsilon=epsilon, summary=summary)

            remote_weights = fetch_index_industry_weights(index_code)
            refresh_index_industry_weights(
                conn, index_code, remote_weights, summary=summary
            )

        # valuation-only 也拉近期收盘，避免「最新收盘」停在更早交易日。
        price_bars = None if mode == "full" else (lookback_bars if mode == "incremental" else 60)
        refresh_index_daily_metrics(
            conn,
            index_code,
            summary=summary,
            include_prices=True,
            max_price_bars=price_bars,
        )
        remote_val = fetch_index_pe_snapshot(index_code)
        refresh_index_valuation_snapshot(
            conn, index_code, remote_val, epsilon=epsilon, summary=summary
        )

        conn.commit()
        summary.success_count = 1
        summary.status = "success"
        finish_sync_run(
            conn,
            run_id,
            success_codes=[etf_code, index_code],
            failures=[],
            meta=summary.to_dict(),
        )
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        summary.failure_count = 1
        summary.status = "failed"
        summary.error_summary.append(
            {"error": str(exc), "traceback": traceback.format_exc()[-2000:]}
        )
        if run_id is not None:
            finish_sync_run(
                conn,
                run_id,
                success_codes=[],
                failures=[{"code": etf_code, "error": str(exc)}],
                meta=summary.to_dict(),
            )
        raise
    finally:
        conn.close()
        SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        SUMMARY_PATH.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fill missing + validate ETF/index bars from hongsehuojian."
    )
    parser.add_argument("--etf-code", default=DEFAULT_ETF_CODE)
    parser.add_argument("--index-code", default=DEFAULT_INDEX_CODE)
    parser.add_argument("--epsilon", type=float, default=DEFAULT_EPSILON)
    parser.add_argument(
        "--mode",
        choices=("incremental", "full", "valuation-only"),
        default="incremental",
        help="incremental=近 N 根(默认快)；full=全历史；valuation-only=只刷估值快照+日序列",
    )
    parser.add_argument(
        "--lookback-bars",
        type=int,
        default=30,
        help="incremental 模式下每个序列拉取的最近 bar 数（默认 30）",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="K 线终点日 YYYY-MM-DD（默认今天）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run(
        etf_code=args.etf_code,
        index_code=args.index_code,
        epsilon=args.epsilon,
        end=args.end,
        mode=args.mode,
        lookback_bars=args.lookback_bars,
    )
    print(
        f"{JOB_NAME} status={summary.status} "
        f"etf_filled={summary.etf_filled} etf_mismatch={summary.etf_mismatch_count} "
        f"idx_px_filled={summary.index_price_filled} "
        f"val_pe={summary.valuation_current_pe_ttm} "
        f"avg5y={summary.valuation_pe_ttm_avg_5y} avg10y={summary.valuation_pe_ttm_avg_10y} "
        f"metric_rows={summary.index_metric_rows} "
        f"industry_rows={summary.industry_weight_rows} as_of={summary.industry_weight_as_of}"
    )
    return 0 if summary.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
