"""三只指数收盘落库：中证走官方 perf，创业板R 走扶摇 REST。"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from scheduled_tasks.etf.fuyao_index_client import fetch_index_daily_closes
from scheduled_tasks.jobs.sync_index_levels import sync_all_index_levels

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


def test_sync_writes_close_only_and_keeps_valuation_null(monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[dict] = []

    def fake_csi(index_code: str, *, start: date, end: date) -> list[dict]:
        assert end >= start
        return [{"trade_date": "2026-10-19", "close": 6800.66 if index_code == "000905.SH" else 11630.21}]

    def fake_fuyao(thscode: str, *, start: date, end: date, api_key: str) -> list[dict]:
        assert thscode == "399606.SZ"
        assert api_key == "test-key"
        return [{"trade_date": "2026-10-20", "close": 3061.17}]

    monkeypatch.setattr("scheduled_tasks.jobs.sync_index_levels.fetch_index_daily_bars", fake_csi)
    monkeypatch.setattr("scheduled_tasks.jobs.sync_index_levels.fetch_index_daily_closes", fake_fuyao)
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
    assert summary.success_codes == ["000905.SH", "930955.CSI", "399606.SZ"]
    assert {row["price_source"] for row in written} == {"csindex", "fuyao"}
    assert all(row["pe_ttm"] is None and row["valuation_source"] is None for row in written)
