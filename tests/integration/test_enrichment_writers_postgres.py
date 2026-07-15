"""enrichment writer 可选集成测试。

权威库是 Supabase Postgres。本文件默认跳过；仅当显式设置
TEST_DATABASE_URL（本地 supabase start 的 54322，或专用测试项目）时执行。
禁止对生产 live 库跑这些测试。
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("psycopg")

TEST_DATABASE_URL = (os.environ.get("TEST_DATABASE_URL") or "").strip()
require_test_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="需要 TEST_DATABASE_URL（本地/专用测试 Supabase，非 production live）",
)

MIGRATION_ENRICHMENT = (
    Path(__file__).resolve().parents[2]
    / "src/scheduled_tasks/models/migrations"
    / "20260715_etf_daily_amount_enrichment_and_trade_calendar.sql"
)


def _connect():
    import psycopg
    from psycopg.rows import dict_row

    return psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row)


@require_test_db
def test_dual_writer_yfinance_then_akshare_preserves_price() -> None:
    from scheduled_tasks.db import update_etf_daily_enrichment, upsert_etf_daily_bars

    with _connect() as conn:
        conn.execute("begin")
        try:
            conn.execute(MIGRATION_ENRICHMENT.read_text(encoding="utf-8"))
            conn.execute(
                """
                create table if not exists public.etf_daily (
                  etf_code text not null,
                  trade_date date not null,
                  open numeric, high numeric, low numeric, close numeric not null,
                  volume numeric, amount numeric,
                  open_qfq numeric, high_qfq numeric, low_qfq numeric, close_qfq numeric,
                  open_hfq numeric, high_hfq numeric, low_hfq numeric, close_hfq numeric,
                  price_source text,
                  amount_source text,
                  amount_updated_at timestamptz,
                  updated_at timestamptz not null default now(),
                  primary key (etf_code, trade_date)
                )
                """
            )
            # 清理测试键
            conn.execute(
                "delete from public.etf_daily where etf_code = '510300' and trade_date = %s",
                (date(2024, 1, 2),),
            )
            upsert_etf_daily_bars(
                conn,
                [
                    {
                        "etf_code": "510300",
                        "trade_date": date(2024, 1, 2),
                        "open": 1.0,
                        "high": 1.1,
                        "low": 0.9,
                        "close": 1.05,
                        "volume": 10.0,
                        "amount": None,
                        "open_qfq": 1.0,
                        "high_qfq": 1.1,
                        "low_qfq": 0.9,
                        "close_qfq": 1.05,
                        "open_hfq": 1.0,
                        "high_hfq": 1.1,
                        "low_hfq": 0.9,
                        "close_hfq": 1.05,
                        "price_source": "yfinance",
                    }
                ],
            )
            before = conn.execute(
                "select close, price_source, updated_at from public.etf_daily "
                "where etf_code='510300' and trade_date=%s",
                (date(2024, 1, 2),),
            ).fetchone()

            result = update_etf_daily_enrichment(
                conn,
                [
                    {
                        "etf_code": "510300",
                        "trade_date": date(2024, 1, 2),
                        "amount": 12345.0,
                        "amount_source": "akshare",
                    }
                ],
            )
            assert result.updated_count == 1
            assert result.unmatched == []

            after = conn.execute(
                """
                select close, price_source, amount, amount_source, updated_at, amount_updated_at
                from public.etf_daily
                where etf_code='510300' and trade_date=%s
                """,
                (date(2024, 1, 2),),
            ).fetchone()
            assert float(after["close"]) == float(before["close"])
            assert after["price_source"] == "yfinance"
            assert float(after["amount"]) == 12345.0
            assert after["amount_source"] == "akshare"
            assert after["updated_at"] == before["updated_at"]
            assert after["amount_updated_at"] is not None
        finally:
            conn.execute("rollback")


@require_test_db
def test_akshare_then_yfinance_preserves_amount() -> None:
    from scheduled_tasks.db import update_etf_daily_enrichment, upsert_etf_daily_bars

    d = date(2024, 1, 3)
    with _connect() as conn:
        conn.execute("begin")
        try:
            conn.execute(
                "delete from public.etf_daily where etf_code = '510300' and trade_date = %s",
                (d,),
            )
            upsert_etf_daily_bars(
                conn,
                [
                    {
                        "etf_code": "510300",
                        "trade_date": d,
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1.0,
                        "amount": None,
                        "open_qfq": None,
                        "high_qfq": None,
                        "low_qfq": None,
                        "close_qfq": None,
                        "open_hfq": None,
                        "high_hfq": None,
                        "low_hfq": None,
                        "close_hfq": None,
                        "price_source": "yfinance",
                    }
                ],
            )
            update_etf_daily_enrichment(
                conn,
                [
                    {
                        "etf_code": "510300",
                        "trade_date": d,
                        "amount": 999.0,
                        "amount_source": "akshare",
                    }
                ],
            )
            upsert_etf_daily_bars(
                conn,
                [
                    {
                        "etf_code": "510300",
                        "trade_date": d,
                        "open": 2.0,
                        "high": 2.0,
                        "low": 2.0,
                        "close": 2.0,
                        "volume": 2.0,
                        "amount": None,
                        "open_qfq": None,
                        "high_qfq": None,
                        "low_qfq": None,
                        "close_qfq": None,
                        "open_hfq": None,
                        "high_hfq": None,
                        "low_hfq": None,
                        "close_hfq": None,
                        "price_source": "yfinance",
                    }
                ],
            )
            row = conn.execute(
                "select close, amount, amount_source from public.etf_daily "
                "where etf_code='510300' and trade_date=%s",
                (d,),
            ).fetchone()
            assert float(row["close"]) == 2.0
            assert float(row["amount"]) == 999.0
            assert row["amount_source"] == "akshare"
        finally:
            conn.execute("rollback")


@require_test_db
def test_partial_unmatched_does_not_insert() -> None:
    from scheduled_tasks.db import update_etf_daily_enrichment

    with _connect() as conn:
        conn.execute("begin")
        try:
            result = update_etf_daily_enrichment(
                conn,
                [
                    {
                        "etf_code": "510300",
                        "trade_date": date(1999, 1, 1),
                        "amount": 1.0,
                        "amount_source": "akshare",
                    }
                ],
            )
            assert result.updated_count == 0
            assert len(result.unmatched) == 1
            cnt = conn.execute(
                "select count(*) as c from public.etf_daily "
                "where etf_code='510300' and trade_date=%s",
                (date(1999, 1, 1),),
            ).fetchone()["c"]
            assert int(cnt) == 0
        finally:
            conn.execute("rollback")
