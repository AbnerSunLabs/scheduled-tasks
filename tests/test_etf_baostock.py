"""BaoStock merge / code mapping unit tests（不连远端）。"""

from datetime import date

import pandas as pd
import pytest

from scheduled_tasks.etf.baostock_client import (
    _iter_date_chunks,
    dataframe_to_ohlc_map,
    merge_adj_only,
    merge_three_adjustments,
    to_baostock_code,
)
from scheduled_tasks.jobs.sync_etf_kline_baostock import (
    _needs_adj_refresh,
    parse_codes_arg,
)


def test_to_baostock_code_sh_sz() -> None:
    assert to_baostock_code("510300") == "sh.510300"
    assert to_baostock_code("159915") == "sz.159915"


def test_iter_date_chunks_covers_full_range() -> None:
    from datetime import timedelta

    chunks = list(_iter_date_chunks(date(2020, 1, 1), date(2021, 1, 10), max_days=180))
    assert chunks[0][0] == date(2020, 1, 1)
    assert chunks[-1][1] == date(2021, 1, 10)
    # 相邻 chunk 连续无缺口
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        assert cur[0] == prev[1] + timedelta(days=1)
        assert (prev[1] - prev[0]).days + 1 <= 180


def test_fetch_ipo_date_requires_exact_code_match(monkeypatch: pytest.MonkeyPatch) -> None:
    """全表返回时不得取第一行 IPO，否则 full 会只拉近几个月。"""
    from scheduled_tasks.etf import baostock_client as client

    class FakeRs:
        error_code = "0"
        error_msg = "success"
        fields = ["code", "code_name", "ipoDate", "outDate", "type", "status"]

        def __init__(self) -> None:
            self._rows = [
                ["sh.000001", "上证指数", "1991-07-15", "", "2", "1"],
                ["sh.510300", "沪深300ETF", "2012-05-28", "", "5", "1"],
            ]
            self._i = -1

        def next(self) -> bool:
            self._i += 1
            return self._i < len(self._rows)

        def get_row_data(self) -> list[str]:
            return self._rows[self._i]

    class FakeBs:
        def query_stock_basic(self, code: str = ""):
            return FakeRs()

    ipo = client.fetch_ipo_date(FakeBs(), "510300")
    assert ipo == date(2012, 5, 28)


def test_to_baostock_code_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        to_baostock_code("600000")
    with pytest.raises(ValueError):
        to_baostock_code("51030")


def _bar(
    d: str,
    o: str,
    h: str,
    low: str,
    c: str,
    vol: str = "10000",
    amt: str = "1",
) -> dict:
    return {
        "date": d,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "volume": vol,
        "amount": amt,
        "tradestatus": "1",
    }


def test_merge_three_adjustments_ok_and_volume_lots() -> None:
    raw = pd.DataFrame([_bar("2024-01-02", "1", "2", "0.5", "1.5", vol="10000")])
    qfq = pd.DataFrame([_bar("2024-01-02", "1.1", "2.1", "0.6", "1.6")])
    hfq = pd.DataFrame([_bar("2024-01-02", "10", "20", "5", "15")])
    rows = merge_three_adjustments(raw, qfq, hfq, "510300")
    assert len(rows) == 1
    assert rows[0]["volume"] == 100.0  # 股→手
    assert rows[0]["close_qfq"] == 1.6
    assert rows[0]["close_hfq"] == 15.0
    assert rows[0]["price_source"] == "baostock"


def test_merge_three_adjustments_fails_when_qfq_missing() -> None:
    raw = pd.DataFrame([_bar("2024-01-02", "1", "2", "0.5", "1.5")])
    qfq = pd.DataFrame([])  # 缺复权
    hfq = pd.DataFrame([_bar("2024-01-02", "10", "20", "5", "15")])
    with pytest.raises(RuntimeError, match="missing qfq/hfq"):
        merge_three_adjustments(raw, qfq, hfq, "510300")


def test_merge_adj_only_requires_hfq() -> None:
    qfq = pd.DataFrame([_bar("2024-01-02", "1.1", "2.1", "0.6", "1.6")])
    hfq = pd.DataFrame([])
    with pytest.raises(RuntimeError, match="missing hfq"):
        merge_adj_only(qfq, hfq, "510300")


def test_merge_adj_only_skips_suspended_days() -> None:
    """停牌日不得进入 adj 日期集，否则会误判 needs_full。"""
    qfq = pd.DataFrame(
        [
            _bar("2024-01-02", "1.1", "2.1", "0.6", "1.6"),
            {
                **_bar("2024-01-03", "1.1", "2.1", "0.6", "1.6"),
                "tradestatus": "0",
            },
        ]
    )
    hfq = pd.DataFrame(
        [
            _bar("2024-01-02", "10", "20", "5", "15"),
            {
                **_bar("2024-01-03", "10", "20", "5", "15"),
                "tradestatus": "0",
            },
        ]
    )
    rows = merge_adj_only(qfq, hfq, "510300")
    assert len(rows) == 1
    assert rows[0]["trade_date"] == date(2024, 1, 2)


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
