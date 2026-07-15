"""PostgreSQL persistence helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


@dataclass
class EnrichmentResult:
    """UPDATE-only 成交额补数结果。"""

    updated_count: int
    unmatched: list[dict[str, Any]] = field(default_factory=list)


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
    """读取当前 ETF 池全表（禁止按 max(snapshot_date) 过滤）。表名：etf_pool。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            select etf_code, snapshot_date
            from public.etf_pool
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


def get_etf_hfq_scale(conn: Connection[dict[str, Any]], etf_code: str) -> float | None:
    """全历史首日 close/close_qfq，供 incremental / adj_check 固定后复权锚定。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            select close, close_qfq
            from public.etf_daily
            where etf_code = %s
              and close is not null
              and close_qfq is not null
              and close_qfq <> 0
            order by trade_date asc
            limit 1
            """,
            (etf_code,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return float(row["close"]) / float(row["close_qfq"])


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
                amount = coalesce(excluded.amount, etf_daily.amount),
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


def _enrichment_row_key(row: dict[str, Any]) -> tuple[str, date]:
    trade_date = row["trade_date"]
    if isinstance(trade_date, datetime):
        trade_date = trade_date.date()
    elif isinstance(trade_date, str):
        trade_date = date.fromisoformat(trade_date)
    return str(row["etf_code"]), trade_date


def update_etf_daily_enrichment(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> EnrichmentResult:
    """仅 UPDATE 已有主行情行的 amount / amount_source / amount_updated_at。

    - 禁止 INSERT / upsert；无匹配行计入 unmatched，不中断批次。
    - 禁止改 OHLC / volume / 复权列 / price_source / updated_at。
    - 输入 (etf_code, trade_date) 必须唯一，重复键立即 fail-fast（尚未执行 SQL）。
    """
    values = list(rows)
    if not values:
        return EnrichmentResult(updated_count=0, unmatched=[])

    input_keys: list[tuple[str, date]] = []
    seen: set[tuple[str, date]] = set()
    for row in values:
        key = _enrichment_row_key(row)
        if key in seen:
            raise ValueError(
                f"duplicate enrichment key before SQL: etf_code={key[0]} trade_date={key[1]}"
            )
        seen.add(key)
        input_keys.append(key)
        if "amount" not in row or "amount_source" not in row:
            raise ValueError("enrichment row requires amount and amount_source")

    with conn.cursor() as cur:
        cur.execute(
            """
            with incoming as (
              select *
              from unnest(
                %s::text[],
                %s::date[],
                %s::numeric[],
                %s::text[]
              ) as t(etf_code, trade_date, amount, amount_source)
            )
            update public.etf_daily as d
            set amount = i.amount,
                amount_source = i.amount_source,
                amount_updated_at = now()
            from incoming as i
            where d.etf_code = i.etf_code
              and d.trade_date = i.trade_date
            returning d.etf_code, d.trade_date
            """,
            (
                [k[0] for k in input_keys],
                [k[1] for k in input_keys],
                [row["amount"] for row in values],
                [row["amount_source"] for row in values],
            ),
        )
        returned = {(str(r["etf_code"]), r["trade_date"]) for r in cur.fetchall()}

    unmatched = [
        {"etf_code": code, "trade_date": trade_date.isoformat()}
        for code, trade_date in input_keys
        if (code, trade_date) not in returned
    ]
    return EnrichmentResult(updated_count=len(returned), unmatched=unmatched)


def upsert_trade_calendar(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """按 (market, cal_date) upsert 交易日历。"""
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into public.trade_calendar (market, cal_date, is_open, updated_at)
            values (%(market)s, %(cal_date)s, %(is_open)s, now())
            on conflict (market, cal_date) do update
            set is_open = excluded.is_open,
                updated_at = now()
            """,
            values,
        )
    return len(values)


def get_fx_max_rate_date(conn: Connection[dict[str, Any]]) -> date | None:
    with conn.cursor() as cur:
        cur.execute("select max(rate_date) as last_date from public.fx_rates")
        row = cur.fetchone()
    if not row or row["last_date"] is None:
        return None
    return row["last_date"]


def upsert_fx_rates(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """按 (rate_date, from_currency, to_currency) upsert 汇率。"""
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into public.fx_rates (
              rate_date, from_currency, to_currency, rate, source, updated_at
            ) values (
              %(rate_date)s, %(from_currency)s, %(to_currency)s, %(rate)s, %(source)s, now()
            )
            on conflict (rate_date, from_currency, to_currency) do update
            set rate = excluded.rate,
                source = excluded.source,
                updated_at = now()
            """,
            values,
        )
    return len(values)
