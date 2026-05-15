"""Sync configured index data from TuShare to Supabase PostgreSQL."""

from __future__ import annotations

import traceback

from scheduled_tasks.config import infer_index_meta, load_settings
from scheduled_tasks.db import (
    connect,
    create_sync_run,
    finish_sync_run,
    upsert_index_meta,
    upsert_industry_weights,
    upsert_prices,
    upsert_valuations,
)
from scheduled_tasks.indices.daily import fetch_index_daily
from scheduled_tasks.indices.industry import fetch_index_industry_weights
from scheduled_tasks.indices.valuation import fetch_index_valuations

JOB_NAME = "sync_indices"


def _error_summary(code: str, error: BaseException) -> dict[str, str]:
    return {
        "code": code,
        "error": str(error),
        "type": error.__class__.__name__,
    }


def sync_indices() -> int:
    settings = load_settings()
    success_codes: list[str] = []
    failures: list[dict[str, str]] = []

    with connect(settings.database_url) as conn:
        run_id = create_sync_run(conn, JOB_NAME, settings.index_codes)
        for display_order, code in enumerate(settings.index_codes, start=10):
            try:
                print(f"[indices] syncing {code}")
                meta = infer_index_meta(code, display_order * 10)
                upsert_index_meta(conn, meta)

                prices = fetch_index_daily(code)
                if not prices:
                    raise RuntimeError("empty index daily prices")
                price_count = upsert_prices(conn, code, prices)

                valuations = fetch_index_valuations(code)
                valuation_count = upsert_valuations(conn, code, valuations)

                industry_weights = fetch_index_industry_weights(code)
                industry_count = upsert_industry_weights(conn, code, industry_weights)

                conn.commit()
                success_codes.append(code)
                print(
                    "[indices] synced "
                    f"{code}: prices={price_count}, valuations={valuation_count}, "
                    f"industry_weights={industry_count}"
                )
            except Exception as error:
                conn.rollback()
                failures.append(_error_summary(code, error))
                print(f"[indices] failed {code}: {error}")
                print(traceback.format_exc())

        finish_sync_run(conn, run_id, success_codes, failures)

    print(
        "[indices] sync finished: "
        f"success={len(success_codes)}, failures={len(failures)}"
    )
    return 1 if failures else 0


def main() -> None:
    raise SystemExit(sync_indices())


if __name__ == "__main__":
    main()
