"""A 股交易日探针单测（mock HTTP，不连远端）。"""

from datetime import date
from pathlib import Path
from unittest.mock import patch

from scheduled_tasks.market.ashare_trading_day import (
    TradingDayCheck,
    check_ashare_trading_day,
    is_trading_day_holiday_cn,
    write_github_output,
)


def test_weekend_not_trading_even_if_tiaoxiu() -> None:
    # 2026-10-10 调休上班（周六）仍非 A 股交易日
    off: set[date] = set()
    assert is_trading_day_holiday_cn(date(2026, 10, 10), off_days=off) is False


def test_weekday_holiday_closed() -> None:
    off = {date(2026, 10, 1)}
    assert is_trading_day_holiday_cn(date(2026, 10, 1), off_days=off) is False


def test_normal_weekday_open() -> None:
    assert is_trading_day_holiday_cn(date(2026, 7, 22), off_days=set()) is True


def test_primary_holiday_cn_open() -> None:
    with patch(
        "scheduled_tasks.market.ashare_trading_day.fetch_holiday_cn_off_days",
        return_value=set(),
    ):
        result = check_ashare_trading_day(date(2026, 7, 22))
    assert result == TradingDayCheck(
        cal_date="2026-07-22",
        should_run=True,
        is_trading_day="true",
        source="holiday-cn",
        error="",
    )


def test_primary_holiday_cn_closed() -> None:
    with patch(
        "scheduled_tasks.market.ashare_trading_day.fetch_holiday_cn_off_days",
        return_value={date(2026, 10, 1)},
    ):
        result = check_ashare_trading_day(date(2026, 10, 1))
    assert result.should_run is False
    assert result.is_trading_day == "false"
    assert result.source == "holiday-cn"


def test_fallback_tencent_when_primary_fails() -> None:
    with (
        patch(
            "scheduled_tasks.market.ashare_trading_day.fetch_holiday_cn_off_days",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "scheduled_tasks.market.ashare_trading_day.check_via_tencent",
            return_value=False,
        ),
    ):
        result = check_ashare_trading_day(date(2026, 10, 1))
    assert result.should_run is False
    assert result.is_trading_day == "false"
    assert result.source == "tencent"
    assert "holiday-cn" in result.error


def test_fail_open_when_all_sources_fail() -> None:
    with (
        patch(
            "scheduled_tasks.market.ashare_trading_day.fetch_holiday_cn_off_days",
            side_effect=RuntimeError("primary down"),
        ),
        patch(
            "scheduled_tasks.market.ashare_trading_day.check_via_tencent",
            side_effect=RuntimeError("backup down"),
        ),
    ):
        result = check_ashare_trading_day(date(2026, 7, 22))
    assert result.should_run is True
    assert result.is_trading_day == "unknown"
    assert result.source == "none"
    assert "primary down" in result.error
    assert "backup down" in result.error


def test_fallback_tencent_open() -> None:
    with (
        patch(
            "scheduled_tasks.market.ashare_trading_day.fetch_holiday_cn_off_days",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "scheduled_tasks.market.ashare_trading_day.check_via_tencent",
            return_value=True,
        ),
    ):
        result = check_ashare_trading_day(date(2026, 7, 22))
    assert result.should_run is True
    assert result.is_trading_day == "true"
    assert result.source == "tencent"


def test_fallback_tencent_inconclusive_fail_open() -> None:
    with (
        patch(
            "scheduled_tasks.market.ashare_trading_day.fetch_holiday_cn_off_days",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "scheduled_tasks.market.ashare_trading_day.check_via_tencent",
            return_value=None,
        ),
    ):
        result = check_ashare_trading_day(date(2026, 10, 1))
    assert result.should_run is True
    assert result.is_trading_day == "unknown"
    assert result.source == "none"
    assert "inconclusive" in result.error


def test_write_github_output_lowercase_should_run(tmp_path: Path, monkeypatch) -> None:
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    write_github_output(
        TradingDayCheck(
            cal_date="2026-07-22",
            should_run=True,
            is_trading_day="true",
            source="holiday-cn",
            error="",
        )
    )
    write_github_output(
        TradingDayCheck(
            cal_date="2026-10-01",
            should_run=False,
            is_trading_day="false",
            source="holiday-cn",
            error="",
        )
    )
    text = out.read_text(encoding="utf-8")
    assert "should_run=true\n" in text
    assert "should_run=false\n" in text
    assert "should_run=True" not in text
    assert "should_run=False" not in text
