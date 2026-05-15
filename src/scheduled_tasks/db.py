"""PostgreSQL persistence helpers."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from scheduled_tasks.config import IndexMeta
from scheduled_tasks.indices.types import (
    IndexPricePoint,
    IndexValuationPoint,
    IndustryWeightRow,
)


def connect(database_url: str) -> Connection[dict[str, Any]]:
    return psycopg.connect(database_url, row_factory=dict_row)


def create_sync_run(conn: Connection[dict[str, Any]], job_name: str, codes: tuple[str, ...]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into sync_runs (job_name, status, index_codes)
            values (%s, 'running', %s)
            returning id
            """,
            (job_name, list(codes)),
        )
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise RuntimeError("failed to create sync run")
    return int(row["id"])


def finish_sync_run(
    conn: Connection[dict[str, Any]],
    run_id: int,
    success_codes: list[str],
    failures: list[dict[str, str]],
) -> None:
    status = "success"
    if failures and success_codes:
        status = "partial"
    elif failures:
        status = "failed"

    with conn.cursor() as cur:
        cur.execute(
            """
            update sync_runs
            set status = %s,
                finished_at = %s,
                success_codes = %s,
                success_count = %s,
                failure_count = %s,
                error_summary = %s
            where id = %s
            """,
            (
                status,
                datetime.now(UTC),
                success_codes,
                len(success_codes),
                len(failures),
                Jsonb(failures),
                run_id,
            ),
        )
    conn.commit()


def upsert_index_meta(conn: Connection[dict[str, Any]], meta: IndexMeta) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into indices (code, name, category, display_order)
            values (%(code)s, %(name)s, %(category)s, %(display_order)s)
            on conflict (code) do update
            set name = excluded.name,
                category = excluded.category,
                display_order = excluded.display_order,
                updated_at = now()
            """,
            asdict(meta),
        )


def upsert_prices(
    conn: Connection[dict[str, Any]],
    code: str,
    points: Iterable[IndexPricePoint],
) -> int:
    rows = [(code, point.trade_date, point.close) for point in points]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into index_daily_prices (index_code, trade_date, close)
            values (%s, %s, %s)
            on conflict (index_code, trade_date) do update
            set close = excluded.close,
                updated_at = now()
            """,
            rows,
        )
    return len(rows)


def upsert_valuations(
    conn: Connection[dict[str, Any]], code: str, points: Iterable[IndexValuationPoint]
) -> int:
    rows = [(code, point.trade_date, point.pe_ttm, point.pb, point.source) for point in points]
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into index_daily_valuations (index_code, trade_date, pe_ttm, pb, source)
            values (%s, %s, %s, %s, %s)
            on conflict (index_code, trade_date) do update
            set pe_ttm = excluded.pe_ttm,
                pb = excluded.pb,
                source = excluded.source,
                updated_at = now()
            """,
            rows,
        )
    return len(rows)


def upsert_industry_weights(
    conn: Connection[dict[str, Any]], code: str, rows: Iterable[IndustryWeightRow]
) -> int:
    values = [
        (code, row.as_of_date, row.sw_level, row.industry_name, row.weight_pct)
        for row in rows
    ]
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into index_industry_weights
              (index_code, as_of_date, sw_level, industry_name, weight_pct)
            values (%s, %s, %s, %s, %s)
            on conflict (index_code, as_of_date, sw_level, industry_name) do update
            set weight_pct = excluded.weight_pct,
                updated_at = now()
            """,
            values,
        )
    return len(values)
