"""PostgreSQL persistence helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


def connect(database_url: str) -> Connection[dict[str, Any]]:
    return psycopg.connect(database_url, row_factory=dict_row)


def create_sync_run(
    conn: Connection[dict[str, Any]],
    job_name: str,
    codes: tuple[str, ...],
    meta: dict[str, Any] | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into sync_runs (job_name, status, index_codes, meta)
            values (%s, 'running', %s, %s)
            returning id
            """,
            (job_name, list(codes), Jsonb(meta or {})),
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
    meta: dict[str, Any] | None = None,
) -> None:
    status = "success"
    if failures and success_codes:
        status = "partial"
    elif failures:
        status = "failed"

    with conn.cursor() as cur:
        if meta is None:
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
        else:
            cur.execute(
                """
                update sync_runs
                set status = %s,
                    finished_at = %s,
                    success_codes = %s,
                    success_count = %s,
                    failure_count = %s,
                    error_summary = %s,
                    meta = coalesce(meta, '{}'::jsonb) || %s
                where id = %s
                """,
                (
                    status,
                    datetime.now(UTC),
                    success_codes,
                    len(success_codes),
                    len(failures),
                    Jsonb(failures),
                    Jsonb(meta),
                    run_id,
                ),
            )
    conn.commit()


def fetch_etf_pool(
    conn: Connection[dict[str, Any]],
    excluded_codes: Sequence[str],
) -> list[dict[str, Any]]:
    """读取当前 ETF 池全表（禁止按 max(snapshot_date) 过滤）。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            select etf_code, snapshot_date
            from public.etf_pool_snapshots
            where etf_code <> all(%s)
            order by etf_code
            """,
            (list(excluded_codes),),
        )
        return list(cur.fetchall())


def get_etf_max_trade_date(conn: Connection[dict[str, Any]], etf_code: str) -> date | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select max(trade_date) as last_date
            from public.etf_daily
            where etf_code = %s
            """,
            (etf_code,),
        )
        row = cur.fetchone()
    if not row or row["last_date"] is None:
        return None
    return row["last_date"]


def get_etf_anchor_qfq(
    conn: Connection[dict[str, Any]], etf_code: str
) -> tuple[date, float | None] | None:
    """取该只最早交易日及对应 close_qfq，作为除权检测锚点。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            select trade_date, close_qfq
            from public.etf_daily
            where etf_code = %s
            order by trade_date asc
            limit 1
            """,
            (etf_code,),
        )
        row = cur.fetchone()
    if not row:
        return None
    close_qfq = row["close_qfq"]
    return row["trade_date"], float(close_qfq) if close_qfq is not None else None


def count_etf_rows(conn: Connection[dict[str, Any]], etf_code: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) as cnt from public.etf_daily where etf_code = %s",
            (etf_code,),
        )
        row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def existing_trade_dates(
    conn: Connection[dict[str, Any]],
    etf_code: str,
    trade_dates: Sequence[date],
) -> set[date]:
    if not trade_dates:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """
            select trade_date
            from public.etf_daily
            where etf_code = %s
              and trade_date = any(%s)
            """,
            (etf_code, list(trade_dates)),
        )
        return {row["trade_date"] for row in cur.fetchall()}


def upsert_etf_daily_bars(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """写入不复权 OHLCV + 复权列 + price_source；复权列非空才覆盖。"""
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into public.etf_daily (
              etf_code, trade_date,
              open, high, low, close, volume, amount,
              open_qfq, high_qfq, low_qfq, close_qfq,
              open_hfq, high_hfq, low_hfq, close_hfq,
              price_source, updated_at
            ) values (
              %(etf_code)s, %(trade_date)s,
              %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s, %(amount)s,
              %(open_qfq)s, %(high_qfq)s, %(low_qfq)s, %(close_qfq)s,
              %(open_hfq)s, %(high_hfq)s, %(low_hfq)s, %(close_hfq)s,
              %(price_source)s, now()
            )
            on conflict (etf_code, trade_date) do update
            set open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                amount = excluded.amount,
                open_qfq = coalesce(excluded.open_qfq, etf_daily.open_qfq),
                high_qfq = coalesce(excluded.high_qfq, etf_daily.high_qfq),
                low_qfq = coalesce(excluded.low_qfq, etf_daily.low_qfq),
                close_qfq = coalesce(excluded.close_qfq, etf_daily.close_qfq),
                open_hfq = coalesce(excluded.open_hfq, etf_daily.open_hfq),
                high_hfq = coalesce(excluded.high_hfq, etf_daily.high_hfq),
                low_hfq = coalesce(excluded.low_hfq, etf_daily.low_hfq),
                close_hfq = coalesce(excluded.close_hfq, etf_daily.close_hfq),
                price_source = excluded.price_source,
                updated_at = now()
            """,
            values,
        )
    return len(values)


def update_etf_adj_columns(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """仅 UPDATE 已有行的复权列；不改 price_source / 不复权 OHLCV。"""
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            update public.etf_daily
            set open_qfq = coalesce(%(open_qfq)s, open_qfq),
                high_qfq = coalesce(%(high_qfq)s, high_qfq),
                low_qfq = coalesce(%(low_qfq)s, low_qfq),
                close_qfq = coalesce(%(close_qfq)s, close_qfq),
                open_hfq = coalesce(%(open_hfq)s, open_hfq),
                high_hfq = coalesce(%(high_hfq)s, high_hfq),
                low_hfq = coalesce(%(low_hfq)s, low_hfq),
                close_hfq = coalesce(%(close_hfq)s, close_hfq),
                updated_at = now()
            where etf_code = %(etf_code)s
              and trade_date = %(trade_date)s
            """,
            values,
        )
    return len(values)
