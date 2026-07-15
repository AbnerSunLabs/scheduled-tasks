"""update_etf_daily_enrichment 单元测试（mock cursor；不连库）。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from scheduled_tasks.db import EnrichmentResult, update_etf_daily_enrichment


def test_update_etf_daily_enrichment_empty() -> None:
    conn = MagicMock()
    result = update_etf_daily_enrichment(conn, [])
    assert result == EnrichmentResult(updated_count=0, unmatched=[])
    conn.cursor.assert_not_called()


def test_update_etf_daily_enrichment_duplicate_keys_fail_fast() -> None:
    conn = MagicMock()
    rows = [
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 2),
            "amount": 1.0,
            "amount_source": "akshare",
        },
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 2),
            "amount": 2.0,
            "amount_source": "akshare",
        },
    ]
    with pytest.raises(ValueError, match="duplicate enrichment key"):
        update_etf_daily_enrichment(conn, rows)
    conn.cursor.assert_not_called()


def test_update_etf_daily_enrichment_unmatched_from_returning() -> None:
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchall.return_value = [
        {"etf_code": "510300", "trade_date": date(2024, 1, 2)},
    ]
    rows = [
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 2),
            "amount": 100.0,
            "amount_source": "akshare",
        },
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 3),
            "amount": 200.0,
            "amount_source": "akshare",
        },
    ]
    result = update_etf_daily_enrichment(conn, rows)
    assert result.updated_count == 1
    assert result.unmatched == [
        {"etf_code": "510300", "trade_date": "2024-01-03"},
    ]
    sql = cur.execute.call_args.args[0]
    assert "update public.etf_daily" in sql
    assert "amount_updated_at = now()" in sql
    set_clause = sql.split("set", 1)[1].split("from", 1)[0]
    assert "amount_updated_at" in set_clause
    # enrichment 不得刷新行级 updated_at（价格新鲜度）
    assert "updated_at = now()" not in set_clause.replace("amount_updated_at = now()", "")
    assert "insert" not in sql.lower()


def test_akshare_code_and_windows() -> None:
    from scheduled_tasks.etf.akshare_client import iter_date_windows, to_six_digit_code

    assert to_six_digit_code("510300") == "510300"
    assert to_six_digit_code("sh.510300") == "510300"
    assert to_six_digit_code("510300.SS") == "510300"
    assert to_six_digit_code("159915.SZ") == "159915"

    windows = iter_date_windows(date(2024, 11, 1), date(2025, 2, 1))
    assert windows[0] == (date(2024, 11, 1), date(2024, 12, 31))
    assert windows[-1][1] == date(2025, 2, 1)
    assert all((end - start).days + 1 <= 366 for start, end in windows)
