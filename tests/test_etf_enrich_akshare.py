"""sync_etf_enrich_akshare 顶层 fatal 收口与退出码（mock；不连库）。"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scheduled_tasks.db import EnrichmentResult
from scheduled_tasks.jobs.sync_etf_enrich_akshare import EnrichSummary, main, run


def test_run_finishes_sync_run_on_fatal_after_create() -> None:
    """create_sync_run 已提交后，coverage 异常须另开连接 finish，避免永久 running。"""
    conn = MagicMock()
    finish_conn = MagicMock()
    connect_calls = iter([conn, finish_conn])

    def fake_connect(_url: str):
        c = next(connect_calls)
        cm = MagicMock()
        cm.__enter__.return_value = c
        cm.__exit__.return_value = False
        return cm

    with (
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.load_settings",
            return_value=SimpleNamespace(database_url="postgresql://u:p@localhost/db"),
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.connect",
            side_effect=fake_connect,
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.create_sync_run",
            return_value=42,
        ) as mock_create,
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.resolve_range",
            return_value=(MagicMock(), MagicMock()),
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.fetch_etf_amount_pending_dates",
            return_value=[],
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.fetch_etf_amount_hist",
            return_value=(
                [
                    {
                        "etf_code": "510300",
                        "trade_date": date(2026, 7, 15),
                        "amount": 1.0,
                        "amount_source": "akshare",
                    }
                ],
                [],
            ),
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.update_etf_daily_enrichment",
            return_value=EnrichmentResult(updated_count=1, unmatched=[]),
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.compute_per_etf_amount_coverage",
            side_effect=RuntimeError("coverage boom"),
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.finish_sync_run",
        ) as mock_finish,
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.write_summary",
        ),
    ):
        summary = run("incremental", codes_arg=("510300",))

    mock_create.assert_called_once()
    assert summary.status == "failed"
    assert any(f.get("code") == "*" for f in summary.error_summary)
    mock_finish.assert_called_once()
    args, kwargs = mock_finish.call_args
    assert args[0] is finish_conn
    assert args[1] == 42
    assert args[2] == ["510300"]
    assert kwargs["meta"]["fatal"] == "coverage boom"


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        ("success", 0),
        ("partial", 1),
        ("failed", 1),
    ],
)
def test_main_exit_code_matches_kline_job(status: str, expected_exit: int) -> None:
    """partial 与 failed 均非 0，与主行情 job / workflow 告警一致。"""
    summary = EnrichSummary(mode="incremental", status=status)
    with patch(
        "scheduled_tasks.jobs.sync_etf_enrich_akshare.run",
        return_value=summary,
    ):
        assert main(["--mode", "incremental"]) == expected_exit


def _run_enrich_with_mocks(
    *,
    fetch_side_effect: object,
    coverage: dict | None = None,
    mode: str = "incremental",
    enrichment: EnrichmentResult | None = None,
) -> object:
    """跑 run()：mock 掉 DB/上游，仅验证成功判定。"""
    conn = MagicMock()
    cm = MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    default_enrichment = enrichment or EnrichmentResult(
        updated_count=1,
        unmatched=[],
    )

    with (
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.load_settings",
            return_value=SimpleNamespace(database_url="postgresql://u:p@localhost/db"),
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.connect",
            return_value=cm,
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.create_sync_run",
            return_value=1,
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.resolve_range",
            return_value=(MagicMock(), MagicMock()),
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.fetch_etf_amount_pending_dates",
            return_value=[],
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.fetch_etf_amount_hist",
            side_effect=fetch_side_effect,
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.update_etf_daily_enrichment",
            return_value=default_enrichment,
        ),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.compute_per_etf_amount_coverage",
            return_value=coverage
            or {
                "per_etf": [],
                "insufficient_history": [],
                "below_95_with_full_history": [],
                "missing_ohlcv": [],
            },
        ),
        patch("scheduled_tasks.jobs.sync_etf_enrich_akshare.finish_sync_run"),
        patch("scheduled_tasks.jobs.sync_etf_enrich_akshare.write_summary"),
        patch(
            "scheduled_tasks.jobs.sync_etf_enrich_akshare.new_akshare_give_up_state",
            return_value=MagicMock(),
        ),
    ):
        return run(mode, codes_arg=("510300", "159915"))


def test_run_window_failures_not_success() -> None:
    """任一窗口失败 → 该 ETF 不进 success_codes。"""
    from datetime import date

    rows = [
        {
            "etf_code": "510300",
            "trade_date": date(2026, 7, 15),
            "amount": 1.0,
            "amount_source": "akshare",
        }
    ]
    window_fail = [
        {
            "etf_code": "510300",
            "window_start": "2024-01-01",
            "window_end": "2024-12-31",
            "error": "akshare=empty_result; baostock_fallback=empty_result",
        }
    ]

    def fetch(code: str, *_a: object, **_k: object):
        if code == "510300":
            return rows, window_fail
        return (
            [
                {
                    "etf_code": "159915",
                    "trade_date": date(2026, 7, 15),
                    "amount": 2.0,
                    "amount_source": "akshare",
                }
            ],
            [],
        )

    summary = _run_enrich_with_mocks(fetch_side_effect=fetch)
    assert summary.status == "partial"
    assert summary.success_count == 1
    assert summary.failure_count == 1
    assert summary.error_summary[0]["code"] == "510300"
    assert summary.error_summary[0]["type"] == "window_failure"


def test_run_coverage_below_95_not_success() -> None:
    """full 模式下 fill_rate < 95% → failure；incremental 只观测不门禁。"""
    from datetime import date

    def fetch(code: str, *_a: object, **_k: object):
        return (
            [
                {
                    "etf_code": code,
                    "trade_date": date(2026, 7, 15),
                    "amount": 1.0,
                    "amount_source": "akshare",
                }
            ],
            [],
        )

    coverage = {
        "per_etf": [],
        "insufficient_history": [],
        "below_95_with_full_history": ["510300"],
        "missing_ohlcv": [],
    }
    # incremental：coverage 不进 failures
    inc = _run_enrich_with_mocks(
        fetch_side_effect=fetch,
        coverage=coverage,
        mode="incremental",
    )
    assert inc.status == "success"
    assert inc.failure_count == 0

    full = _run_enrich_with_mocks(
        fetch_side_effect=fetch,
        coverage=coverage,
        mode="full",
    )
    assert full.status == "partial"
    assert full.success_count == 1
    assert full.failure_count == 1
    assert full.error_summary[0]["code"] == "510300"
    assert full.error_summary[0]["type"] == "coverage"


def test_run_full_zero_updates_not_success() -> None:
    """full 模式：有拉取行但 updated=0（全 unmatched）→ failure。"""

    def fetch(code: str, *_a: object, **_k: object):
        return (
            [
                {
                    "etf_code": code,
                    "trade_date": date(2026, 7, 15),
                    "amount": 1.0,
                    "amount_source": "akshare",
                }
            ],
            [],
        )

    summary = _run_enrich_with_mocks(
        fetch_side_effect=fetch,
        mode="full",
        enrichment=EnrichmentResult(
            updated_count=0,
            unmatched=[
                {"etf_code": "510300", "trade_date": "2026-07-15"},
            ],
        ),
    )
    assert summary.status == "failed"
    assert summary.success_count == 0
    assert summary.failure_count == 2
    assert all(f["type"] == "zero_updates" for f in summary.error_summary)


def test_run_incremental_zero_updates_still_observes() -> None:
    """incremental：零更新不门禁（主行情可能尚未写入）。"""

    def fetch(code: str, *_a: object, **_k: object):
        return (
            [
                {
                    "etf_code": code,
                    "trade_date": date(2026, 7, 15),
                    "amount": 1.0,
                    "amount_source": "akshare",
                }
            ],
            [],
        )

    summary = _run_enrich_with_mocks(
        fetch_side_effect=fetch,
        mode="incremental",
        enrichment=EnrichmentResult(
            updated_count=0,
            unmatched=[{"etf_code": "510300", "trade_date": "2026-07-15"}],
        ),
    )
    assert summary.status == "success"
    assert summary.success_count == 2
