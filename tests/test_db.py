"""db URL / conninfo 单元测试（mock；不连库）。"""

from __future__ import annotations

from unittest.mock import patch

from scheduled_tasks.db import (
    connect,
    conninfo_from_database_url,
    normalize_database_url,
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


def test_conninfo_allows_query_without_explicit_dbname() -> None:
    raw = "postgresql://u:p@localhost:5432?sslmode=require"
    info = conninfo_from_database_url(raw)
    assert info["host"] == "localhost"
    assert info["port"] == 5432
    assert info["dbname"] == "postgres"
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
