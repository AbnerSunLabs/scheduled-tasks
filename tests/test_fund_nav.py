"""502010 单位净值：上海日期，当天没公布则记为未覆盖。"""

from __future__ import annotations

from datetime import date

import pytest

from scheduled_tasks.etf.eastmoney_fund_nav import fetch_fund_nav, parse_fund_nav_script
from scheduled_tasks.jobs.sync_fund_nav import FUND_CODES, covers_session


def test_parse_uses_shanghai_date_and_keeps_the_later_duplicate() -> None:
    script = (
        'var Data_netWorthTrend = ['
        '{"x":1514822400000,"y":1,"equityReturn":0},'
        '{"x":1514822400000,"y":1.1,"equityReturn":0},'
        '{"x":1793808000000,"y":1.3281,"equityReturn":1}];'
    )
    assert parse_fund_nav_script(script, "502010") == [
        {"fund_code": "502010", "nav_date": "2018-01-02", "unit_nav": 1.1, "source": "eastmoney"},
        {"fund_code": "502010", "nav_date": "2026-11-05", "unit_nav": 1.3281, "source": "eastmoney"},
    ]
    assert parse_fund_nav_script("var other = [];", "502010") == []


def test_fetch_rejects_a_bad_code_before_request() -> None:
    def fail(_url: str) -> str:
        raise AssertionError("should not request")

    with pytest.raises(RuntimeError, match="6 digits"):
        fetch_fund_nav("502010.SZ", http_get_text=fail)


def test_session_is_covered_only_when_that_day_is_present() -> None:
    rows = [{"nav_date": "2026-11-06", "unit_nav": 1.18}]
    assert covers_session(rows, date(2026, 11, 6)) is True
    assert covers_session(rows, date(2026, 11, 7)) is False
    assert FUND_CODES == ("004752", "502010")
