"""yfinance ETF merge unit tests（不连远端）。"""

from datetime import date

import pandas as pd
import pytest

from scheduled_tasks.etf.yfinance_client import (
    build_adj_only,
    build_three_adjustments,
    to_yahoo_symbol,
)
from scheduled_tasks.jobs.sync_etf_kline_baostock import (
    _needs_adj_refresh,
    parse_codes_arg,
)


def test_to_yahoo_symbol() -> None:
    assert to_yahoo_symbol("510300") == "510300.SS"
    assert to_yahoo_symbol("159915") == "159915.SZ"
    with pytest.raises(ValueError):
        to_yahoo_symbol("600000")


def test_build_three_adjustments_qfq_hfq() -> None:
    # 模拟分红：不复权 close 从 10→9，Adj Close 保持连续
    df = pd.DataFrame(
        [
            {
                "date": "2024-01-02",
                "open": 10.0,
                "high": 10.5,
                "low": 9.5,
                "close": 10.0,
                "adj_close": 9.0,
                "volume": 10000,
            },
            {
                "date": "2024-01-03",
                "open": 9.0,
                "high": 9.2,
                "low": 8.8,
                "close": 9.0,
                "adj_close": 9.0,
                "volume": 20000,
            },
        ]
    )
    rows = build_three_adjustments(df, "510300")
    assert len(rows) == 2
    assert rows[0]["price_source"] == "yfinance"
    assert rows[0]["volume"] == 100.0  # 股→手
    # 首日 qfq=adj；hfq 锚定首日不复权 close
    assert rows[0]["close_qfq"] == pytest.approx(9.0)
    assert rows[0]["close_hfq"] == pytest.approx(10.0)
    assert rows[1]["close_qfq"] == pytest.approx(9.0)
    assert rows[1]["close_hfq"] == pytest.approx(10.0)


def test_build_three_adjustments_uses_external_hfq_scale() -> None:
    """incremental 近窗：必须用全历史 scale，不能用窗口首日。"""
    # 窗口内无分红，若用窗口首日 scale=1，hfq=adj；正确全历史 scale=10/9
    df = pd.DataFrame(
        [
            {
                "date": "2024-06-01",
                "open": 9.0,
                "high": 9.1,
                "low": 8.9,
                "close": 9.0,
                "adj_close": 9.0,
                "volume": 1000,
            }
        ]
    )
    wrong = build_three_adjustments(df, "510300")
    assert wrong[0]["close_hfq"] == pytest.approx(9.0)

    fixed = build_three_adjustments(df, "510300", hfq_scale=10.0 / 9.0)
    assert fixed[0]["close_hfq"] == pytest.approx(10.0)


def test_build_adj_only_shape() -> None:
    df = pd.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "adj_close": 1.0,
                "volume": 100,
            }
        ]
    )
    rows = build_adj_only(df, "510300")
    assert set(rows[0]) == {
        "etf_code",
        "trade_date",
        "open_qfq",
        "high_qfq",
        "low_qfq",
        "close_qfq",
        "open_hfq",
        "high_hfq",
        "low_hfq",
        "close_hfq",
    }


def test_parse_codes_arg_rejects_excluded() -> None:
    with pytest.raises(ValueError, match="excluded"):
        parse_codes_arg("510300,512660")


def test_parse_codes_arg_ok() -> None:
    assert parse_codes_arg("510300, 159915") == ("510300", "159915")


def test_needs_adj_refresh_epsilon() -> None:
    assert _needs_adj_refresh(None, 1.0, 0.001) is True
    assert _needs_adj_refresh(100.0, 100.05, 0.001) is False
    assert _needs_adj_refresh(100.0, 100.2, 0.001) is True
