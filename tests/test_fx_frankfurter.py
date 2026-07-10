"""Frankfurter FX client / sync unit tests（默认不连远端）。"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from scheduled_tasks.fx.frankfurter_client import (
    build_pair_rows,
    fetch_usd_quotes_for_range,
)
from scheduled_tasks.jobs.sync_fx_rates_frankfurter import resolve_date_range


def test_build_pair_rows() -> None:
    rows = build_pair_rows(date(2024, 1, 2), usd_to_cny=7.0, usd_to_hkd=7.8)
    assert len(rows) == 3
    by_pair = {(r["from_currency"], r["to_currency"]): float(r["rate"]) for r in rows}
    assert by_pair[("USD", "CNY")] == pytest.approx(7.0)
    assert by_pair[("USD", "HKD")] == pytest.approx(7.8)
    assert by_pair[("HKD", "CNY")] == pytest.approx(7.0 / 7.8)


def test_resolve_date_range_incremental_with_existing() -> None:
    start, end = resolve_date_range(
        "incremental",
        lookback_days=5,
        start=None,
        end=date(2024, 1, 10),
        max_existing=date(2024, 1, 10),
    )
    assert end == date(2024, 1, 10)
    assert start == date(2024, 1, 6)


def test_resolve_date_range_full() -> None:
    start, end = resolve_date_range(
        "full",
        lookback_days=7,
        start=None,
        end=date(2024, 6, 1),
        max_existing=None,
    )
    assert start == date(2015, 1, 1)
    assert end == date(2024, 6, 1)


def test_fetch_usd_quotes_for_range_parses_timeseries() -> None:
    payload = {
        "amount": 1.0,
        "base": "USD",
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
        "rates": {
            "2024-01-02": {"CNY": 7.1, "HKD": 7.8},
            "2024-01-03": {"CNY": 7.2, "HKD": 7.81},
        },
    }
    with patch(
        "scheduled_tasks.fx.frankfurter_client._http_get_json",
        return_value=payload,
    ):
        rows = fetch_usd_quotes_for_range(date(2024, 1, 2), date(2024, 1, 3))
    assert len(rows) == 6
    assert rows[0]["rate_date"] == date(2024, 1, 2)
    assert rows[0]["from_currency"] == "USD"


def test_fetch_empty_rates_returns_empty_list() -> None:
    payload = {
        "amount": 1.0,
        "base": "USD",
        "start_date": "2024-01-01",
        "end_date": "2024-01-01",
        "rates": {},
    }
    with patch(
        "scheduled_tasks.fx.frankfurter_client._http_get_json",
        return_value=payload,
    ):
        rows = fetch_usd_quotes_for_range(date(2024, 1, 1), date(2024, 1, 1))
    assert rows == []


def test_upsert_fx_rates_sql_shape() -> None:
    from scheduled_tasks.db import upsert_fx_rates

    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    rows = build_pair_rows(date(2024, 1, 2), 7.0, 7.8)
    assert upsert_fx_rates(conn, rows) == 3
    assert cur.executemany.called
