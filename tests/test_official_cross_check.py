"""官网校验 client / job 单元测试（mock；不连库）。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from scheduled_tasks.etf.csindex_client import fetch_index_daily_bars, to_csindex_code
from scheduled_tasks.etf.sse_client import fetch_etf_daily_bars
from scheduled_tasks.jobs.sync_official_cross_check import (
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


def test_from_pool_skips_szse_and_checks_sse() -> None:
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
            return_value=[],
        ) as fetch_sse,
        patch(
            "scheduled_tasks.jobs.sync_official_cross_check.cross_check_etf"
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
    assert out.index_validated == 0
