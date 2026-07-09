"""AkShare ETF merge unit tests（不连远端）。"""

from datetime import date

import pandas as pd
import pytest

from scheduled_tasks.etf.akshare_client import (
    dataframe_to_ohlc_map,
    merge_adj_only,
    merge_three_adjustments,
)
from scheduled_tasks.jobs.sync_etf_kline_baostock import (
    _needs_adj_refresh,
    parse_codes_arg,
)


def _bar(d: str, o: str, h: str, low: str, c: str, vol: str = "100", amt: str = "1") -> dict:
    return {
        "date": d,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": vol,
        "amount": amt,
    }


def test_merge_three_adjustments_ok_volume_lots() -> None:
    raw = pd.DataFrame([_bar("2024-01-02", "1", "2", "0.5", "1.5", vol="100")])
    qfq = pd.DataFrame([_bar("2024-01-02", "1.1", "2.1", "0.6", "1.6")])
    hfq = pd.DataFrame([_bar("2024-01-02", "10", "20", "5", "15")])
    rows = merge_three_adjustments(raw, qfq, hfq, "510300")
    assert len(rows) == 1
    assert rows[0]["volume"] == 100.0  # 东财已是手，不再 ÷100
    assert rows[0]["close_qfq"] == 1.6
    assert rows[0]["close_hfq"] == 15.0
    assert rows[0]["price_source"] == "akshare"


def test_merge_three_adjustments_fails_when_qfq_missing() -> None:
    raw = pd.DataFrame([_bar("2024-01-02", "1", "2", "0.5", "1.5")])
    qfq = pd.DataFrame([])
    hfq = pd.DataFrame([_bar("2024-01-02", "10", "20", "5", "15")])
    with pytest.raises(RuntimeError, match="missing qfq/hfq"):
        merge_three_adjustments(raw, qfq, hfq, "510300")


def test_merge_adj_only_requires_hfq() -> None:
    qfq = pd.DataFrame([_bar("2024-01-02", "1.1", "2.1", "0.6", "1.6")])
    hfq = pd.DataFrame([])
    with pytest.raises(RuntimeError, match="missing hfq"):
        merge_adj_only(qfq, hfq, "510300")


def test_dataframe_to_ohlc_map_skips_incomplete() -> None:
    df = pd.DataFrame(
        [
            _bar("2024-01-02", "1", "2", "0.5", "1.5"),
            {"date": "2024-01-03", "open": "", "high": "2", "low": "1", "close": "1.5"},
        ]
    )
    m = dataframe_to_ohlc_map(df)
    assert date(2024, 1, 2) in m
    assert date(2024, 1, 3) not in m


def test_parse_codes_arg_rejects_excluded() -> None:
    with pytest.raises(ValueError, match="excluded"):
        parse_codes_arg("510300,512660")


def test_parse_codes_arg_ok() -> None:
    assert parse_codes_arg("510300, 159915") == ("510300", "159915")


def test_needs_adj_refresh_epsilon() -> None:
    assert _needs_adj_refresh(None, 1.0, 0.001) is True
    assert _needs_adj_refresh(100.0, 100.05, 0.001) is False
    assert _needs_adj_refresh(100.0, 100.2, 0.001) is True
