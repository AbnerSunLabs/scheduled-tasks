"""hongsehuojian client / fill-validate 单元测试（默认不连远端）。"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from scheduled_tasks.etf.hongsehuojian_client import (
    _mean_positive_valuations,
    build_etf_daily_rows,
    parse_kline_items,
    parse_trade_date,
    to_security_code,
)
from scheduled_tasks.jobs.sync_hongsehuojian_fill_validate import (
    compare_etf_row,
    fill_and_validate_etf,
    values_mismatch,
)


def test_to_security_code_etf_and_index() -> None:
    assert to_security_code("512170", kind="etf") == "512170.SH"
    assert to_security_code("159828", kind="etf") == "159828.SZ"
    assert to_security_code("399989.SZ", kind="index") == "399989.SZ"
    assert to_security_code("HSI.HI", kind="index") == "HSI.HI"
    assert to_security_code("HSTECH.HI", kind="index") == "HSTECH.HI"
    assert to_security_code("H30184.CSI", kind="index") == "H30184.CSI"
    assert to_security_code("NDX.NASDAQ", kind="index") == "NDX.NASDAQ"
    assert to_security_code("SPX.OTH", kind="index") == "SPX.OTH"
    with pytest.raises(ValueError):
        to_security_code("600000", kind="etf")
    with pytest.raises(ValueError):
        to_security_code("399989", kind="index")
    with pytest.raises(ValueError):
        to_security_code("HSI.HK", kind="index")


def test_parse_trade_date_formats() -> None:
    assert parse_trade_date("20260717") == date(2026, 7, 17)
    assert parse_trade_date("2026-07-17") == date(2026, 7, 17)
    assert parse_trade_date(date(2026, 7, 17)) == date(2026, 7, 17)


def test_parse_kline_items_semicolon_string() -> None:
    data = {
        "columns": "week,tradeDate,open,high,low,close,volume,amount,change,changePercent",
        "items": (
            "星期五,2026-07-17,0.331,0.333,0.317,0.318,31620931,1021250220,-0.015,-4.50;"
            "星期四,2026-07-16,0.33,0.338,0.324,0.333,33090632,1097178237,0.001,0.30"
        ),
    }
    rows = parse_kline_items(data)
    assert len(rows) == 2
    assert rows[0]["tradeDate"] == "2026-07-17"
    assert rows[0]["close"] == "0.318"
    assert rows[1]["tradeDate"] == "2026-07-16"


def test_build_etf_daily_rows_merges_adjustments() -> None:
    raw = [
        {
            "tradeDate": "2026-07-17",
            "open": "0.331",
            "high": "0.333",
            "low": "0.317",
            "close": "0.318",
            "volume": "100",
            "amount": "1000",
        }
    ]
    qfq = [
        {
            "tradeDate": "2026-07-17",
            "open": "0.331",
            "high": "0.333",
            "low": "0.317",
            "close": "0.318",
        }
    ]
    hfq = [
        {
            "tradeDate": "2026-07-17",
            "open": "1.032",
            "high": "1.039",
            "low": "0.989",
            "close": "0.992",
        }
    ]
    rows = build_etf_daily_rows("512170", raw, qfq, hfq)
    assert len(rows) == 1
    assert rows[0]["etf_code"] == "512170"
    assert rows[0]["close"] == pytest.approx(0.318)
    assert rows[0]["close_hfq"] == pytest.approx(0.992)
    assert rows[0]["price_source"] == "hongsehuojian"


def test_values_mismatch_semantics() -> None:
    assert values_mismatch(1.0, 1.0, epsilon=0.001) is False
    assert values_mismatch(1.0, 1.01, epsilon=0.001) is True
    assert values_mismatch(None, 1.0, epsilon=0.001) is False
    assert values_mismatch(None, None, epsilon=0.001) is False


def test_compare_etf_row_reports_close_diff() -> None:
    db = {"close": 0.318, "open": 0.331, "high": 0.333, "low": 0.317}
    remote = {"close": 0.320, "open": 0.331, "high": 0.333, "low": 0.317}
    diffs = compare_etf_row(db, remote, epsilon=0.001)
    assert any(d["field"] == "close" for d in diffs)


def test_fill_and_validate_etf_inserts_missing_only() -> None:
    remote = [
        {
            "etf_code": "512170",
            "trade_date": date(2026, 7, 16),
            "open": 0.33,
            "high": 0.338,
            "low": 0.324,
            "close": 0.333,
            "volume": 1.0,
            "open_qfq": 0.33,
            "high_qfq": 0.338,
            "low_qfq": 0.324,
            "close_qfq": 0.333,
            "open_hfq": 1.0,
            "high_hfq": 1.0,
            "low_hfq": 1.0,
            "close_hfq": 1.0,
            "price_source": "hongsehuojian",
        },
        {
            "etf_code": "512170",
            "trade_date": date(2026, 7, 17),
            "open": 0.331,
            "high": 0.333,
            "low": 0.317,
            "close": 0.318,
            "volume": 1.0,
            "open_qfq": 0.331,
            "high_qfq": 0.333,
            "low_qfq": 0.317,
            "close_qfq": 0.318,
            "open_hfq": 1.0,
            "high_hfq": 1.0,
            "low_hfq": 1.0,
            "close_hfq": 1.0,
            "price_source": "hongsehuojian",
        },
    ]
    conn = MagicMock()
    summary = MagicMock()
    summary.mismatches = []
    summary.etf_mismatch_count = 0

    with (
        pytest.MonkeyPatch.context() as mp,
    ):
        mp.setattr(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.existing_trade_dates",
            lambda *_a, **_k: {date(2026, 7, 17)},
        )
        inserted: list[list] = []

        def _insert(_conn: object, rows: list) -> int:
            inserted.append(list(rows))
            return len(rows)

        mp.setattr(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.insert_etf_daily_bars_ignore_conflict",
            _insert,
        )
        mp.setattr(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.fetch_etf_daily_rows",
            lambda *_a, **_k: {
                date(2026, 7, 17): {
                    "close": 0.318,
                    "open": 0.331,
                    "high": 0.333,
                    "low": 0.317,
                    "volume": 1.0,
                    "open_qfq": 0.331,
                    "high_qfq": 0.333,
                    "low_qfq": 0.317,
                    "close_qfq": 0.318,
                    "open_hfq": 1.0,
                    "high_hfq": 1.0,
                    "low_hfq": 1.0,
                    "close_hfq": 1.0,
                }
            },
        )
        fill_and_validate_etf(conn, "512170", remote, epsilon=0.001, summary=summary)

    assert summary.etf_fetched == 2
    assert summary.etf_filled == 1
    assert summary.etf_validated == 1
    assert len(inserted) == 1
    assert inserted[0][0]["trade_date"] == date(2026, 7, 16)
    assert summary.etf_mismatch_count == 0


def test_mean_positive_valuations() -> None:
    items = [
        {"valuationValue": "10"},
        {"valuationValue": "20"},
        {"valuationValue": "0"},
        {"valuationValue": None},
    ]
    assert _mean_positive_valuations(items) == pytest.approx(15.0)


def test_fetch_index_industry_weights_maps_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    from scheduled_tasks.etf import hongsehuojian_client as client

    payloads = {
        2: {
            "latestDate": "20260331",
            "resultMap": {
                "最新": [
                    {
                        "industryName": "医药生物",
                        "weight": 91.5,
                        "report": "20260331",
                    }
                ]
            },
        },
        3: {
            "latestDate": "20260331",
            "resultMap": {
                "最新": [
                    {
                        "industryName": "医疗器械",
                        "weight": 50.0,
                        "report": "20260331",
                    }
                ]
            },
        },
        4: {
            "latestDate": "20260331",
            "resultMap": {
                "最新": [
                    {
                        "industryName": "医疗设备",
                        "weight": 25.0,
                        "report": "20260331",
                    }
                ]
            },
        },
    }

    def fake_api(path: str, params: dict, *, base_url: str = "") -> dict:
        assert "industryDistribution" in path
        return payloads[int(params["industryLevel"])]

    monkeypatch.setattr(client, "_api_get", fake_api)
    rows = client.fetch_index_industry_weights("399989.SZ")
    by_level = {(r["sw_level"], r["industry_name"]): r["weight_pct"] for r in rows}
    assert by_level[("sw1", "医药生物")] == pytest.approx(91.5)
    assert by_level[("sw2", "医疗器械")] == pytest.approx(50.0)
    assert by_level[("sw3", "医疗设备")] == pytest.approx(25.0)
    assert rows[0]["as_of_date"] == date(2026, 3, 31)


def test_valuation_only_skips_industry_weights() -> None:
    from scheduled_tasks.jobs import sync_hongsehuojian_fill_validate as job

    pe = {
        "tracking_index_code": "399989.SZ",
        "as_of_date": date(2026, 7, 17),
        "current_pe_ttm": 26.0,
        "pe_ttm_avg_5y": 20.0,
        "pe_ttm_avg_10y": 18.0,
        "source": "hongsehuojian",
    }

    with (
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.load_settings",
            return_value=MagicMock(database_url="postgresql://x"),
        ),
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.connect"
        ) as connect_m,
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.create_sync_run",
            return_value=1,
        ),
        patch("scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.finish_sync_run"),
        patch("scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.ensure_index_row"),
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.fetch_index_pe_snapshot",
            return_value=pe,
        ),
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.refresh_index_valuation_snapshot"
        ) as upsert_pe,
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.fetch_index_industry_weights"
        ) as fetch_w,
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.refresh_index_industry_weights"
        ) as refresh_w,
        patch(
            "scheduled_tasks.jobs.sync_hongsehuojian_fill_validate.fetch_etf_daily_bundle"
        ) as fetch_etf,
    ):
        connect_m.return_value = MagicMock()
        out = job.run(mode="valuation-only")
    assert out.status == "success"
    upsert_pe.assert_called_once()
    fetch_w.assert_not_called()
    refresh_w.assert_not_called()
    fetch_etf.assert_not_called()
