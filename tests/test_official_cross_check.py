"""官网校验 client / job 单元测试（mock；不连库）。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from scheduled_tasks.etf.csindex_client import fetch_index_daily_bars, to_csindex_code
from scheduled_tasks.etf.sse_client import fetch_etf_daily_bars
from scheduled_tasks.etf.yfinance_client import DEFAULT_HISTORY_START
from scheduled_tasks.jobs.sync_official_cross_check import (
    FULL_ETF_MAX_BARS,
    SyncSummary,
    compare_fields,
    cross_check_etf,
    main,
    values_mismatch,
)


def test_to_csindex_code() -> None:
    assert to_csindex_code("399989.SZ") == "399989"
    assert to_csindex_code("399989") == "399989"


def test_sse_fetch_parses_kline() -> None:
    payload = {
        "code": "512170",
        "kline": [
            [20260716, 0.33, 0.338, 0.324, 0.333, 100],
            [20260717, 0.331, 0.333, 0.317, 0.318, 100],
        ],
    }
    rows = fetch_etf_daily_bars("512170", max_bars=2, http_get_json=lambda _u: payload)
    assert len(rows) == 2
    assert rows[-1]["trade_date"] == date(2026, 7, 17)
    assert rows[-1]["close"] == 0.318
    assert rows[-1]["price_source"] == "sse"


def test_csindex_fetch_maps_peg_to_pe() -> None:
    payload = {
        "code": "200",
        "success": True,
        "data": [
            {
                "tradeDate": "2026-07-17",
                "indexCode": "399989",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 6276.19,
                "peg": 26.49,
            }
        ],
    }
    captured: list[str] = []

    def _capture(url: str) -> dict:
        captured.append(url)
        return payload

    rows = fetch_index_daily_bars(
        "399989.SZ",
        start=date(2026, 7, 1),
        end=date(2026, 7, 17),
        http_get_json=_capture,
    )
    assert len(rows) == 1
    assert rows[0]["close"] == 6276.19
    assert rows[0]["current_pe_ttm"] == 26.49
    assert "startDate=20260701" in captured[0]
    assert "endDate=20260717" in captured[0]


def test_csindex_raises_on_bad_code_without_success_field() -> None:
    with pytest.raises(RuntimeError, match="csindex index-perf failed"):
        fetch_index_daily_bars(
            "399989.SZ",
            start=date(2026, 7, 1),
            end=date(2026, 7, 17),
            http_get_json=lambda _u: {"code": 500, "msg": "boom"},
        )


def test_csindex_raises_on_success_false() -> None:
    with pytest.raises(RuntimeError, match="csindex index-perf failed"):
        fetch_index_daily_bars(
            "399989.SZ",
            start=date(2026, 7, 1),
            end=date(2026, 7, 17),
            http_get_json=lambda _u: {
                "code": "200",
                "success": False,
                "msg": "denied",
                "data": [],
            },
        )


def test_values_mismatch_semantics() -> None:
    assert values_mismatch(None, None, epsilon=0.001) is False
    assert values_mismatch(1.0, None, epsilon=0.001) is True
    assert values_mismatch(1.0, 1.0005, epsilon=0.001) is False
    assert values_mismatch(1.0, 1.01, epsilon=0.001) is True


def test_cross_check_etf_compare_only_does_not_update() -> None:
    conn = MagicMock()
    summary = SyncSummary()
    official = [
        {
            "etf_code": "512170",
            "trade_date": date(2026, 7, 17),
            "open": 0.331,
            "high": 0.333,
            "low": 0.317,
            "close": 0.400,
            "price_source": "sse",
        }
    ]
    with (
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.fetch_etf_daily_rows",
            return_value={
                date(2026, 7, 17): {
                    "open": 0.331,
                    "high": 0.333,
                    "low": 0.317,
                    "close": 0.318,
                }
            },
        ),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.update_etf_daily_ohlc_official"
        ) as upd,
    ):
        cross_check_etf(
            conn,
            "512170",
            official,
            epsilon=0.001,
            apply_official=False,
            summary=summary,
        )
    assert summary.etf_mismatch_count == 1
    assert summary.etf_applied == 0
    upd.assert_not_called()


def test_cross_check_etf_apply_official_updates() -> None:
    conn = MagicMock()
    summary = SyncSummary()
    official = [
        {
            "etf_code": "512170",
            "trade_date": date(2026, 7, 17),
            "open": 0.331,
            "high": 0.333,
            "low": 0.317,
            "close": 0.400,
            "price_source": "sse",
        }
    ]
    with (
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.fetch_etf_daily_rows",
            return_value={
                date(2026, 7, 17): {
                    "open": 0.331,
                    "high": 0.333,
                    "low": 0.317,
                    "close": 0.318,
                }
            },
        ),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.update_etf_daily_ohlc_official",
            return_value=1,
        ) as upd,
    ):
        cross_check_etf(
            conn,
            "512170",
            official,
            epsilon=0.001,
            apply_official=True,
            summary=summary,
        )
    assert summary.etf_applied == 1
    upd.assert_called_once()


def test_compare_fields_skips_absent_remote() -> None:
    diffs = compare_fields(
        {"close": 1.0, "open": 1.0},
        {"close": 1.0},
        ("open", "close"),
        epsilon=0.001,
    )
    assert diffs == []


def test_main_requires_yes_for_apply() -> None:
    assert main(["--apply-official"]) == 2


def test_main_source_error_without_mismatch_exits_zero() -> None:
    summary = SyncSummary(
        status="partial",
        etf_validated=10,
        source_errors=["sse:510050:Remote end closed connection without response"],
    )
    with patch(
        "scheduled_tasks.jobs.sync_official_cross_check.run",
        return_value=summary,
    ):
        assert main(["--from-pool"]) == 0


def test_main_mismatch_exits_nonzero() -> None:
    summary = SyncSummary(status="partial", etf_validated=10, etf_mismatch_count=1)
    with patch(
        "scheduled_tasks.jobs.sync_official_cross_check.run",
        return_value=summary,
    ):
        assert main(["--from-pool"]) == 1


def test_from_pool_skips_szse_and_checks_sse() -> None:
    sample_row = {
        "etf_code": "512170",
        "trade_date": date(2026, 7, 17),
        "open": 0.3,
        "high": 0.3,
        "low": 0.3,
        "close": 0.3,
        "price_source": "sse",
    }

    def _cross(_conn, _code, _rows, *, epsilon, apply_official, summary):
        del epsilon, apply_official
        summary.etf_validated += 1

    with (
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.load_settings",
            return_value=MagicMock(database_url="postgresql://x"),
        ),
        patch("scheduled_tasks.jobs.sync_official_cross_check.connect") as connect_m,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.fetch_etf_pool",
            return_value=[
                {"etf_code": "512170"},
                {"etf_code": "159915"},
                {"etf_code": "510300"},
            ],
        ),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.create_sync_run",
            return_value=1,
        ),
        patch("scheduled_tasks.jobs.sync_official_cross_check.finish_sync_run"),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.fetch_etf_daily_bars",
            return_value=[sample_row],
        ) as fetch_sse,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.cross_check_etf",
            side_effect=_cross,
        ) as cross_etf,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.write_summary"
        ),
    ):
        connect_m.return_value = MagicMock()
        from scheduled_tasks.jobs.sync_official_cross_check import run

        out = run(from_pool=True, skip_index=True, lookback_bars=5)
    assert out.etf_codes_checked == ["512170", "510300"]
    assert out.etf_codes_skipped_szse == ["159915"]
    assert fetch_sse.call_count == 2
    assert cross_etf.call_count == 2
    assert out.etf_validated == 2
    assert out.status == "success"
    assert out.index_validated == 0


def test_empty_official_data_marks_failed() -> None:
    with (
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.load_settings",
            return_value=MagicMock(database_url="postgresql://x"),
        ),
        patch("scheduled_tasks.jobs.sync_official_cross_check.connect") as connect_m,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.create_sync_run",
            return_value=1,
        ),
        patch("scheduled_tasks.jobs.sync_official_cross_check.finish_sync_run"),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.fetch_etf_daily_bars",
            return_value=[],
        ),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.write_summary"
        ),
    ):
        connect_m.return_value = MagicMock()
        from scheduled_tasks.jobs.sync_official_cross_check import run

        out = run(etf_code="512170", skip_index=True, lookback_bars=5)
    assert out.status == "failed"
    assert out.etf_validated == 0
    assert any("empty kline" in e for e in out.source_errors)


def test_full_mode_uses_deep_history_windows() -> None:
    sample_etf = [
        {
            "etf_code": "510300",
            "trade_date": date(2026, 7, 17),
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "price_source": "sse",
        }
    ]
    sample_idx = [
        {
            "index_code": "399989.SZ",
            "trade_date": date(2026, 7, 17),
            "close": 100.0,
            "current_pe_ttm": 20.0,
            "price_source": "csindex",
        }
    ]

    def _cross_etf(_conn, _code, _rows, *, epsilon, apply_official, summary):
        del epsilon, apply_official
        summary.etf_validated += 1

    def _cross_idx(_conn, _code, _rows, *, epsilon, pe_epsilon, apply_official, summary):
        del epsilon, pe_epsilon, apply_official
        summary.index_validated += 1
        summary.valuation_validated += 1

    with (
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.load_settings",
            return_value=MagicMock(database_url="postgresql://x"),
        ),
        patch("scheduled_tasks.jobs.sync_official_cross_check.connect") as connect_m,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.create_sync_run",
            return_value=1,
        ),
        patch("scheduled_tasks.jobs.sync_official_cross_check.finish_sync_run"),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.fetch_etf_daily_bars",
            return_value=sample_etf,
        ) as fetch_sse,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.fetch_index_daily_bars",
            return_value=sample_idx,
        ) as fetch_idx,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.cross_check_etf",
            side_effect=_cross_etf,
        ),
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.cross_check_index",
            side_effect=_cross_idx,
        ),
        patch("scheduled_tasks.jobs.sync_official_cross_check.write_summary"),
    ):
        connect_m.return_value = MagicMock()
        from scheduled_tasks.jobs.sync_official_cross_check import run

        out = run(etf_code="510300", mode="full", end=date(2026, 7, 17))
    assert out.status == "success"
    assert fetch_sse.call_args.kwargs["max_bars"] == FULL_ETF_MAX_BARS
    assert fetch_idx.call_args.kwargs["start"] == DEFAULT_HISTORY_START
    assert fetch_idx.call_args.kwargs["end"] == date(2026, 7, 17)
