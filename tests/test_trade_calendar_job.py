"""交易日历 job 的完整性校验与失败收口。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scheduled_tasks.jobs.sync_trade_calendar_baostock import run


def _connection_context(conn: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__enter__.return_value = conn
    context.__exit__.return_value = False
    return context


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [
            {"market": "CN", "cal_date": date(2026, 7, 1), "is_open": True},
            {"market": "CN", "cal_date": date(2026, 7, 3), "is_open": True},
        ],
        [
            {"market": "CN", "cal_date": date(2026, 7, 1), "is_open": True},
            {"market": "CN", "cal_date": date(2026, 7, 2), "is_open": True},
            {"market": "CN", "cal_date": date(2026, 7, 2), "is_open": False},
            {"market": "CN", "cal_date": date(2026, 7, 3), "is_open": True},
        ],
    ],
)
def test_run_rejects_incomplete_calendar(rows: list[dict[str, object]]) -> None:
    """空、缺日或重复日历不得写库并标记成功。"""
    conn = MagicMock()
    with (
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.load_settings",
            return_value=SimpleNamespace(database_url="postgresql://u:p@localhost/db"),
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.connect",
            return_value=_connection_context(conn),
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.create_sync_run",
            return_value=1,
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.fetch_cn_trade_calendar",
            return_value=rows,
        ),
        patch("scheduled_tasks.jobs.sync_trade_calendar_baostock.upsert_trade_calendar") as upsert,
        patch("scheduled_tasks.jobs.sync_trade_calendar_baostock.finish_sync_run") as finish,
        patch("scheduled_tasks.jobs.sync_trade_calendar_baostock.write_summary"),
    ):
        summary = run(date(2026, 7, 1), date(2026, 7, 3))

    assert summary.status == "failed"
    assert summary.error_summary[0]["type"] == "ValueError"
    upsert.assert_not_called()
    assert finish.call_args.args[2] == []


def test_run_accepts_complete_calendar() -> None:
    rows = [
        {"market": "CN", "cal_date": date(2026, 7, 1), "is_open": True},
        {"market": "CN", "cal_date": date(2026, 7, 2), "is_open": True},
        {"market": "CN", "cal_date": date(2026, 7, 3), "is_open": True},
    ]
    conn = MagicMock()
    with (
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.load_settings",
            return_value=SimpleNamespace(database_url="postgresql://u:p@localhost/db"),
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.connect",
            return_value=_connection_context(conn),
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.create_sync_run",
            return_value=1,
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.fetch_cn_trade_calendar",
            return_value=rows,
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.upsert_trade_calendar",
            return_value=3,
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.cross_check_etf_daily",
            return_value={"open_day_count": 3},
        ),
        patch("scheduled_tasks.jobs.sync_trade_calendar_baostock.finish_sync_run") as finish,
        patch("scheduled_tasks.jobs.sync_trade_calendar_baostock.write_summary"),
    ):
        summary = run(date(2026, 7, 1), date(2026, 7, 3))

    assert summary.status == "success"
    assert summary.upserted == 3
    assert finish.call_args.args[2] == ["CN"]


def test_run_finishes_with_fresh_connection_when_original_connection_is_lost() -> None:
    """原连接失效后仍须落失败态并写 summary。"""
    broken_conn = MagicMock()
    broken_conn.rollback.side_effect = RuntimeError("connection lost")
    finish_conn = MagicMock()
    contexts = iter([_connection_context(broken_conn), _connection_context(finish_conn)])

    with (
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.load_settings",
            return_value=SimpleNamespace(database_url="postgresql://u:p@localhost/db"),
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.connect",
            side_effect=lambda _url: next(contexts),
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.create_sync_run",
            return_value=42,
        ),
        patch(
            "scheduled_tasks.jobs.sync_trade_calendar_baostock.fetch_cn_trade_calendar",
            side_effect=RuntimeError("provider down"),
        ),
        patch("scheduled_tasks.jobs.sync_trade_calendar_baostock.finish_sync_run") as finish,
        patch("scheduled_tasks.jobs.sync_trade_calendar_baostock.write_summary") as write_summary,
    ):
        summary = run(date(2026, 7, 1), date(2026, 7, 3))

    assert summary.status == "failed"
    assert summary.error_summary[0]["error"] == "provider down"
    finish.assert_called_once()
    assert finish.call_args.args[0] is finish_conn
    assert finish.call_args.args[1] == 42
    write_summary.assert_called_once_with(summary)
