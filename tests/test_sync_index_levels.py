"""跟踪指数收盘落库：中证样本走官方 perf，创业板R 走扶摇 REST。"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scheduled_tasks.etf.fuyao_index_client import fetch_index_daily_closes
from scheduled_tasks.etf.sina_forex_client import parse_usdcnh_kline
from scheduled_tasks.jobs.sync_index_levels import (
    _without_start_placeholder,
    sync_all_index_levels,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_fuyao_parses_daily_close_in_shanghai() -> None:
    date_ms = int(datetime(2026, 10, 20, tzinfo=_SHANGHAI).timestamp() * 1000)

    def fake_get(url: str, headers: dict[str, str]) -> dict:
        assert "thscode=399606.SZ" in url
        assert headers["X-api-key"] == "test-key"
        return {
            "code": 0,
            "data": {"item": [{"date_ms": date_ms, "close_price": 3061.17}]},
        }

    bars = fetch_index_daily_closes(
        "399606.sz",
        start=date(2026, 10, 19),
        end=date(2026, 10, 20),
        api_key="test-key",
        http_get_json=fake_get,
    )
    assert bars == [{"trade_date": "2026-10-20", "close": 3061.17}]


def test_fuyao_rejects_non_zero_code() -> None:
    with pytest.raises(RuntimeError, match="failed"):
        fetch_index_daily_closes(
            "399606.SZ",
            start=date(2026, 10, 1),
            end=date(2026, 10, 20),
            api_key="test-key",
            http_get_json=lambda _url, _headers: {"code": 2001, "message": "bad key"},
        )


def test_sina_kline_uses_the_close_after_low_and_high() -> None:
    payload = 'var x=("2026-10-26,6.6692,6.6660,6.6698,6.6676,|2026-10-23,6.6716,6.6677,6.6737,6.6690");'
    assert parse_usdcnh_kline(payload) == [
        {"trade_date": "2026-10-23", "close": 6.669},
        {"trade_date": "2026-10-26", "close": 6.6676},
    ]


def test_drops_close_stamped_on_the_request_start() -> None:
    rows = [
        {"trade_date": "1996-09-14", "close": 1000.0},
        {"trade_date": "2004-12-31", "close": 1000.0},
    ]
    kept = _without_start_placeholder(rows, date(1996, 9, 14))
    assert [row["trade_date"] for row in kept] == ["2004-12-31"]


def test_keeps_the_real_bar_when_the_window_starts_on_a_trading_day() -> None:
    rows = [
        {"trade_date": "2026-10-19", "close": 6800.0},
        {"trade_date": "2026-10-20", "close": 6814.0},
    ]
    kept = _without_start_placeholder(rows, date(2026, 10, 19))
    assert len(kept) == 2


def test_sync_writes_close_only_and_keeps_valuation_null(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[dict] = []

    def fake_csi(index_code: str, *, start: date, end: date) -> list[dict]:
        assert end >= start
        return [{"trade_date": "2026-10-19", "close": 6800.66 if index_code == "000905.SH" else 11630.21}]

    def fake_fuyao(thscode: str, *, start: date, end: date, api_key: str) -> list[dict]:
        assert thscode == "399606.SZ"
        assert api_key == "test-key"
        return [{"trade_date": "2026-10-20", "close": 3061.17}]

    def fake_usdcnh(*, start: date, end: date) -> list[dict]:
        assert end >= start
        return [{"trade_date": "2026-10-20", "close": 6.6676}]

    monkeypatch.setattr("scheduled_tasks.jobs.sync_index_levels.fetch_index_daily_bars", fake_csi)
    monkeypatch.setattr("scheduled_tasks.jobs.sync_index_levels.fetch_index_daily_closes", fake_fuyao)
    monkeypatch.setattr("scheduled_tasks.jobs.sync_index_levels.fetch_usdcnh_daily_closes", fake_usdcnh)
    monkeypatch.setattr("scheduled_tasks.jobs.sync_index_levels.ensure_index_row", lambda *args, **kwargs: None)

    class Conn:
        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    def capture(_conn: Conn, rows: list[dict]) -> int:
        written.extend(rows)
        return len(rows)

    monkeypatch.setattr("scheduled_tasks.jobs.sync_index_levels.upsert_index_daily_metrics", capture)
    summary = sync_all_index_levels(
        Conn(),
        today=date(2026, 10, 20),
        lookback_days=10,
        api_key="test-key",
    )
    assert summary.status == "success"
    assert summary.success_codes == [
        "000905.SH",
        "000922.SH",
        "399989.SZ",
        "000990.SH",
        "000942.SH",
        "399971.SZ",
        "000993.SH",
        "399975.SZ",
        "930955.CSI",
        "399606.SZ",
        "USDCNH.OTH",
    ]
    assert {row["price_source"] for row in written} == {"csindex", "fuyao", "sina"}
    assert sum(row["index_code"] not in {"399606.SZ", "USDCNH.OTH"} for row in written) == 9
    assert all(row["pe_ttm"] is None and row["valuation_source"] is None for row in written)
