"""update_etf_daily_enrichment 单元测试（mock cursor；不连库）。"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from scheduled_tasks.db import (
    EnrichmentResult,
    connect,
    conninfo_from_database_url,
    normalize_database_url,
    update_etf_daily_enrichment,
)


def test_conninfo_unquotes_percent_encoded_password() -> None:
    # Dashboard URI：密码里的 ! 会编码成 %21
    raw = (
        "postgresql://postgres.ref:ab%21cd%5Bef@aws-1-ap-southeast-2.pooler.supabase.com:6543"
        "/postgres"
    )
    info = conninfo_from_database_url(raw)
    assert info["password"] == "ab!cd[ef"
    assert info["user"] == "postgres.ref"


def test_conninfo_allows_unencoded_slash_in_password() -> None:
    # 密码含未编码 / 时，不能把第一个 / 当成 path
    raw = "postgresql://postgres.ref:ab/cd@ef@localhost:5432/postgres?sslmode=require"
    info = conninfo_from_database_url(raw)
    assert info["password"] == "ab/cd@ef"
    assert info["user"] == "postgres.ref"
    assert info["host"] == "localhost"
    assert info["port"] == 5432
    assert info["dbname"] == "postgres"
    assert info["sslmode"] == "require"


def test_normalize_database_url_encodes_slash_in_password() -> None:
    raw = "postgresql://u:ab/cd@localhost:5432/db"
    out = normalize_database_url(raw)
    assert out == "postgresql://u:ab%2Fcd@localhost:5432/db"
    # 再解析应还原
    info = conninfo_from_database_url(out)
    assert info["password"] == "ab/cd"
    assert info["dbname"] == "db"


def test_normalize_database_url_strips_pgbouncer() -> None:
    raw = (
        "postgresql://postgres.ref:secret@aws-1-ap-southeast-2.pooler.supabase.com:6543"
        "/postgres?pgbouncer=true"
    )
    out = normalize_database_url(raw)
    assert "pgbouncer" not in out
    assert out.endswith("/postgres")


def test_normalize_database_url_encodes_special_password() -> None:
    raw = (
        "postgresql://postgres.ref:ab[cd]ef@aws-1-ap-southeast-2.pooler.supabase.com:6543"
        "/postgres?pgbouncer=true"
    )
    out = normalize_database_url(raw)
    assert "pgbouncer" not in out
    assert "%5B" in out and "%5D" in out
    # 编码后应可被标准库解析
    from urllib.parse import urlsplit

    parts = urlsplit(out)
    assert parts.hostname == "aws-1-ap-southeast-2.pooler.supabase.com"


def test_conninfo_keeps_at_sign_in_query() -> None:
    """query 参数中的 @ 不得被当成 userinfo/host 分界。"""
    raw = "postgresql://u:p@localhost:5432/db?application_name=a@b&sslmode=require"
    info = conninfo_from_database_url(raw)
    assert info["host"] == "localhost"
    assert info["port"] == 5432
    assert info["dbname"] == "db"
    assert info["user"] == "u"
    assert info["password"] == "p"
    assert info["application_name"] == "a@b"
    assert info["sslmode"] == "require"


def test_normalize_database_url_keeps_at_sign_in_query() -> None:
    raw = "postgresql://u:p@localhost:5432/db?application_name=a@b"
    out = normalize_database_url(raw)
    assert "localhost:5432/db" in out
    assert "application_name=a" in out


def test_connect_disables_prepare_for_pooler() -> None:
    raw = (
        "postgresql://postgres.ref:ab[cd]ef@aws-1-ap-southeast-2.pooler.supabase.com:6543"
        "/postgres?pgbouncer=true"
    )
    with patch("scheduled_tasks.db.psycopg.connect") as mock_connect:
        connect(raw)
    kwargs = mock_connect.call_args.kwargs
    assert kwargs["prepare_threshold"] is None
    assert kwargs["password"] == "ab[cd]ef"
    assert kwargs["host"] == "aws-1-ap-southeast-2.pooler.supabase.com"
    assert kwargs["port"] == 6543
    assert kwargs["sslmode"] == "require"
    assert "pgbouncer" not in kwargs



def test_update_etf_daily_enrichment_empty() -> None:
    conn = MagicMock()
    result = update_etf_daily_enrichment(conn, [])
    assert result == EnrichmentResult(updated_count=0, unmatched=[])
    conn.cursor.assert_not_called()


def test_update_etf_daily_enrichment_duplicate_keys_fail_fast() -> None:
    conn = MagicMock()
    rows = [
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 2),
            "amount": 1.0,
            "amount_source": "akshare",
        },
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 2),
            "amount": 2.0,
            "amount_source": "akshare",
        },
    ]
    with pytest.raises(ValueError, match="duplicate enrichment key"):
        update_etf_daily_enrichment(conn, rows)
    conn.cursor.assert_not_called()


def test_update_etf_daily_enrichment_unmatched_from_returning() -> None:
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cur.fetchall.return_value = [
        {"etf_code": "510300", "trade_date": date(2024, 1, 2)},
    ]
    rows = [
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 2),
            "amount": 100.0,
            "amount_source": "akshare",
        },
        {
            "etf_code": "510300",
            "trade_date": date(2024, 1, 3),
            "amount": 200.0,
            "amount_source": "akshare",
        },
    ]
    result = update_etf_daily_enrichment(conn, rows)
    assert result.updated_count == 1
    assert result.unmatched == [
        {"etf_code": "510300", "trade_date": "2024-01-03"},
    ]
    sql = cur.execute.call_args.args[0]
    assert "update public.etf_daily" in sql
    assert "amount_updated_at = now()" in sql
    set_clause = sql.split("set", 1)[1].split("from", 1)[0]
    assert "amount_updated_at" in set_clause
    # enrichment 不得刷新行级 updated_at（价格新鲜度）
    assert "updated_at = now()" not in set_clause.replace("amount_updated_at = now()", "")
    assert "insert" not in sql.lower()


def test_akshare_code_and_windows() -> None:
    from scheduled_tasks.etf.akshare_client import iter_date_windows, to_six_digit_code

    assert to_six_digit_code("510300") == "510300"
    assert to_six_digit_code("sh.510300") == "510300"
    assert to_six_digit_code("510300.SS") == "510300"
    assert to_six_digit_code("159915.SZ") == "159915"

    windows = iter_date_windows(date(2024, 11, 1), date(2025, 2, 1))
    assert windows[0] == (date(2024, 11, 1), date(2024, 12, 31))
    assert windows[-1][1] == date(2025, 2, 1)
    assert all((end - start).days + 1 <= 366 for start, end in windows)


def test_fetch_etf_amount_hist_baostock_fallback() -> None:
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    def boom(**_kwargs: object) -> pd.DataFrame:
        raise ConnectionError("eastmoney down")

    def bs_ok(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": date(2026, 7, 15),
                "amount": 1.23e9,
                "amount_source": "baostock",
            }
        ]

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2026, 7, 15),
        date(2026, 7, 15),
        sleep_between_windows=0,
        fetch_fn=boom,
        baostock_fetch_fn=bs_ok,
        akshare_give_up_seconds=0,
    )
    assert fails == []
    assert len(rows) == 1
    assert rows[0]["amount_source"] == "baostock"
    assert rows[0]["amount"] == 1.23e9


def test_akshare_partial_filled_by_baostock() -> None:
    """东财返回成功但缺日时，BaoStock 补洞且不覆盖东财行。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    def ak_partial(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame([{"日期": "2026-07-14", "成交额": 100.0}])

    def bs_full(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": date(2026, 7, 14),
                "amount": 999.0,
                "amount_source": "baostock",
            },
            {
                "etf_code": code,
                "trade_date": date(2026, 7, 15),
                "amount": 200.0,
                "amount_source": "baostock",
            },
        ]

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2026, 7, 14),
        date(2026, 7, 15),
        sleep_between_windows=0,
        fetch_fn=ak_partial,
        baostock_fetch_fn=bs_full,
        akshare_give_up_seconds=60,
    )
    assert fails == []
    by_date = {r["trade_date"]: r for r in rows}
    assert by_date[date(2026, 7, 14)]["amount"] == 100.0
    assert by_date[date(2026, 7, 14)]["amount_source"] == "akshare"
    assert by_date[date(2026, 7, 15)]["amount"] == 200.0
    assert by_date[date(2026, 7, 15)]["amount_source"] == "baostock"


def test_baostock_empty_retries_akshare_when_skipped() -> None:
    """熔断后 BaoStock 空结果时，仍单次回试东财。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import (
        fetch_etf_amount_hist,
        new_akshare_give_up_state,
    )

    state = new_akshare_give_up_state(give_up_seconds=0)
    state.skip = True
    ak_calls = 0

    def boom(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        nonlocal ak_calls
        ak_calls += 1
        return pd.DataFrame([{"日期": "2025-08-01", "成交额": 42.0}])

    def bs_empty(code: str, start: date, end: date) -> list[dict]:
        return []

    rows, fails = fetch_etf_amount_hist(
        "159915",
        date(2025, 8, 1),
        date(2025, 8, 1),
        sleep_between_windows=0,
        fetch_fn=boom,
        baostock_fetch_fn=bs_empty,
        akshare_give_up=state,
    )
    assert fails == []
    assert ak_calls == 1
    assert rows[0]["amount"] == 42.0
    assert rows[0]["amount_source"] == "akshare"


def test_akshare_give_up_skips_later_windows() -> None:
    """东财预算耗尽后，后续窗口不再请求东财（除非 BaoStock 空再回试）。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    ak_calls: list[tuple[str, str]] = []

    def boom(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        ak_calls.append((start_date, end_date))
        raise ConnectionError("eastmoney down")

    def bs_ok(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": start,
                "amount": 1.0,
                "amount_source": "baostock",
            }
        ]

    # 跨年 → 至少 2 个窗口
    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2024, 12, 30),
        date(2025, 1, 2),
        sleep_between_windows=0,
        fetch_fn=boom,
        baostock_fetch_fn=bs_ok,
        akshare_give_up_seconds=0,
    )
    assert fails == []
    assert len(rows) >= 1
    # 预算为 0：第一窗尝试 1 次后放弃；第二窗因 BS 有数据不再回试东财
    assert len(ak_calls) == 1


def test_akshare_give_up_shared_across_etfs() -> None:
    """共享熔断状态：第一只耗尽预算后，后续 ETF 不再请求东财。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import (
        fetch_etf_amount_hist,
        new_akshare_give_up_state,
    )

    ak_symbols: list[str] = []

    def boom(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        ak_symbols.append(symbol)
        raise ConnectionError("eastmoney down")

    def bs_ok(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": start,
                "amount": 1.0,
                "amount_source": "baostock",
            }
        ]

    state = new_akshare_give_up_state(give_up_seconds=0)
    for code in ("510300", "159915", "510500"):
        rows, fails = fetch_etf_amount_hist(
            code,
            date(2026, 7, 15),
            date(2026, 7, 15),
            sleep_between_windows=0,
            fetch_fn=boom,
            baostock_fetch_fn=bs_ok,
            akshare_give_up=state,
        )
        assert fails == []
        assert len(rows) == 1
        assert rows[0]["amount_source"] == "baostock"

    assert state.skip is True
    # 仅第一只 ETF 试过东财；后两只直接 BaoStock
    assert ak_symbols == ["510300"]


def test_baostock_empty_result_counts_as_failure() -> None:
    """BaoStock 返回空列表不得记为窗口成功（历史窗常见）。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    def boom(**_kwargs: object) -> pd.DataFrame:
        raise ConnectionError("eastmoney down")

    def bs_empty(code: str, start: date, end: date) -> list[dict]:
        return []

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2024, 6, 1),
        date(2024, 6, 1),
        sleep_between_windows=0,
        fetch_fn=boom,
        baostock_fetch_fn=bs_empty,
        akshare_give_up_seconds=0,
    )
    assert rows == []
    assert len(fails) == 1
    assert "baostock_fallback=empty_result" in fails[0]["error"]


def test_akshare_empty_dataframe_not_success() -> None:
    """空 DataFrame 不得 ok=True（否则跳过 BaoStock 并误报成功）。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    def empty_df(**_kwargs: object) -> pd.DataFrame:
        return pd.DataFrame()

    def bs_ok(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": date(2026, 7, 15),
                "amount": 9.0,
                "amount_source": "baostock",
            }
        ]

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2026, 7, 15),
        date(2026, 7, 15),
        sleep_between_windows=0,
        fetch_fn=empty_df,
        baostock_fetch_fn=bs_ok,
        akshare_give_up_seconds=0,
    )
    assert fails == []
    assert len(rows) == 1
    assert rows[0]["amount_source"] == "baostock"


def test_akshare_and_baostock_both_empty_is_failure() -> None:
    """双源皆空 → window_failures，复现 rows=[] failures=[] 误报。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    def empty_df(**_kwargs: object) -> pd.DataFrame:
        return pd.DataFrame()

    def bs_empty(code: str, start: date, end: date) -> list[dict]:
        return []

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2024, 6, 1),
        date(2024, 6, 1),
        sleep_between_windows=0,
        fetch_fn=empty_df,
        baostock_fetch_fn=bs_empty,
        akshare_give_up_seconds=0,
    )
    assert rows == []
    assert len(fails) == 1
    assert "akshare=empty_result" in fails[0]["error"]
    assert "baostock_fallback=empty_result" in fails[0]["error"]


def test_akshare_proven_consecutive_failures_trigger_skip() -> None:
    """proven 后连续失败应 skip，避免每窗仍重试 4 次。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import (
        fetch_etf_amount_hist,
        new_akshare_give_up_state,
    )

    ak_calls: list[tuple[str, str]] = []
    state = new_akshare_give_up_state(give_up_seconds=3600)
    state.proven = True

    def boom(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        ak_calls.append((start_date, end_date))
        raise ConnectionError("eastmoney down")

    def bs_ok(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": start,
                "amount": 1.0,
                "amount_source": "baostock",
            }
        ]

    with patch("scheduled_tasks.etf.akshare_client._sleep_backoff"):
        # 三个自然年窗：连续失败 3 次后 skip；第 4 窗不应再打东财
        rows, fails = fetch_etf_amount_hist(
            "510300",
            date(2022, 1, 1),
            date(2025, 1, 2),
            sleep_between_windows=0,
            fetch_fn=boom,
            baostock_fetch_fn=bs_ok,
            akshare_give_up=state,
        )
    assert fails == []
    assert len(rows) >= 1
    assert state.skip is True
    # 3 窗各最多 4 次重试；第 4 窗起因 skip 不再打东财
    assert len({w for w in ak_calls}) == 3
    assert len(ak_calls) == 12


def test_akshare_proven_respects_deadline() -> None:
    """proven=True 时 deadline 已过也不因单次失败立即 skip（改由连续失败驱动）。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import (
        fetch_etf_amount_hist,
        new_akshare_give_up_state,
    )

    ak_calls = 0
    state = new_akshare_give_up_state(give_up_seconds=0)
    state.proven = True

    def boom(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        nonlocal ak_calls
        ak_calls += 1
        raise ConnectionError("eastmoney down")

    def bs_ok(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": start,
                "amount": 1.0,
                "amount_source": "baostock",
            }
        ]

    with patch("scheduled_tasks.etf.akshare_client._sleep_backoff"):
        fetch_etf_amount_hist(
            "510300",
            date(2024, 12, 30),
            date(2025, 1, 2),
            sleep_between_windows=0,
            fetch_fn=boom,
            baostock_fetch_fn=bs_ok,
            akshare_give_up=state,
        )
    # 跨年 2 窗：连续失败未达 3，不应 skip；两窗均会打东财
    assert state.skip is False
    assert state.consecutive_failures == 2
    assert ak_calls == 8


def test_akshare_empty_result_does_not_trip_proven_skip() -> None:
    """proven 后连续空响应不得熔断（年轻 ETF 上市前窗常见）。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import (
        fetch_etf_amount_hist,
        new_akshare_give_up_state,
    )

    ak_calls = 0
    state = new_akshare_give_up_state(give_up_seconds=3600)
    state.proven = True

    def empty_df(**_kwargs: object) -> pd.DataFrame:
        nonlocal ak_calls
        ak_calls += 1
        return pd.DataFrame()

    def bs_ok(code: str, start: date, end: date) -> list[dict]:
        return [
            {
                "etf_code": code,
                "trade_date": start,
                "amount": 1.0,
                "amount_source": "baostock",
            }
        ]

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2022, 1, 1),
        date(2025, 1, 2),
        sleep_between_windows=0,
        fetch_fn=empty_df,
        baostock_fetch_fn=bs_ok,
        akshare_give_up=state,
    )
    assert fails == []
    assert len(rows) >= 1
    assert state.skip is False
    assert state.consecutive_failures == 0
    # 4 个自然年窗均仍请求东财（空响应不 skip）
    assert ak_calls == 4


def test_window_coverage_gate_flags_truncated_history() -> None:
    """有待补日期时，全年窗仅 1 行不得判成功。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    def ak_one(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame([{"日期": "2024-06-01", "成交额": 1.0}])

    def bs_empty(code: str, start: date, end: date) -> list[dict]:
        return []

    expected = {date(2024, 1, 2) + timedelta(days=i) for i in range(0, 200, 2)}
    expected.add(date(2024, 6, 1))

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2024, 1, 1),
        date(2024, 12, 31),
        sleep_between_windows=0,
        fetch_fn=ak_one,
        baostock_fetch_fn=bs_empty,
        akshare_give_up_seconds=0,
        expected_dates=expected,
    )
    assert len(rows) == 1
    assert len(fails) == 1
    assert "window_coverage=" in fails[0]["error"]


def test_window_coverage_vacuous_ok_without_pending() -> None:
    """本窗无待补日期 → 即使双源空也算覆盖通过。"""
    import pandas as pd

    from scheduled_tasks.etf.akshare_client import fetch_etf_amount_hist

    def empty_df(**_kwargs: object) -> pd.DataFrame:
        return pd.DataFrame()

    def bs_empty(code: str, start: date, end: date) -> list[dict]:
        return []

    rows, fails = fetch_etf_amount_hist(
        "510300",
        date(2024, 6, 1),
        date(2024, 6, 1),
        sleep_between_windows=0,
        fetch_fn=empty_df,
        baostock_fetch_fn=bs_empty,
        akshare_give_up_seconds=0,
        expected_dates=set(),
    )
    assert rows == []
    assert fails == []


def test_to_baostock_code() -> None:
    from scheduled_tasks.etf.baostock_client import to_baostock_code

    assert to_baostock_code("510300") == "sh.510300"
    assert to_baostock_code("159915") == "sz.159915"
