"""PostgreSQL persistence helpers."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qsl, quote, unquote, urlencode

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

# Prisma / 部分前端模板会带上；libpq/psycopg 不认
_UNSUPPORTED_URI_QUERY_KEYS = frozenset({"pgbouncer"})

# 官网纠偏写入的 price_source；主写 UPSERT / adj_check 不得覆盖这些行
ETF_DAILY_LOCKED_PRICE_SOURCES = frozenset({"sse", "szse"})
_ETF_DAILY_LOCKED_PRICE_SOURCES_SQL = ", ".join(
    f"'{s}'" for s in sorted(ETF_DAILY_LOCKED_PRICE_SOURCES)
)


def _split_authority(url: str) -> tuple[str, str, str, str, str]:
    """拆 DATABASE_URL → (scheme, user, password, hostport, path_and_query)。

    不用 urlsplit：密码含 []@/ 等未编码字符时，Python 3.13 会误判 bracketed host。
    有 query 时：按 ``@host:port[/path]?query`` 结构识别 authority，避免
    ``application_name=a@b`` 里的 ``@`` 被当成 userinfo/host 分界。无 query 时：
    仍按最后一个 ``@`` 切分，再按 host 段第一个 ``/`` 切 path，兼容密码中的 ``/``。

    会 unquote user/password（兼容 Dashboard 百分号编码 URI）；
    若密码本身含字面 %，须写成 %25。
    """
    raw = url.strip()
    if "://" not in raw:
        raise ValueError("DATABASE_URL must be a postgresql:// URI")
    scheme, rest = raw.split("://", 1)
    if scheme not in {"postgres", "postgresql"}:
        raise ValueError(f"unsupported DATABASE_URL scheme: {scheme}")

    if "@" not in rest:
        raise ValueError("DATABASE_URL missing user@host")

    if "?" in rest:
        # path 必有，这样 query 内的 @ 不会被 rsplit 误吃
        matched = re.match(
            r"^(?P<userinfo>.+)@"
            r"(?P<hostport>(?:\[[^\]]+\]|[^/?@:]+)(?::\d+)?)"
            r"(?P<path>/[^?]*)?"
            r"(?P<query>\?.*)$",
            rest,
        )
        if matched is None:
            raise ValueError("DATABASE_URL has invalid authority before query")
        userinfo = matched.group("userinfo")
        hostport = matched.group("hostport")
        path_and_query = (matched.group("path") or "") + matched.group("query")
    else:
        # 密码可能含 @，取最后一个作为 userinfo 与 host 的分界
        userinfo, host_and_path = rest.rsplit("@", 1)
        path_and_query = ""
        hostport = host_and_path
        if "/" in host_and_path:
            hostport, after = host_and_path.split("/", 1)
            path_and_query = "/" + after

    if ":" in userinfo:
        user, password = userinfo.split(":", 1)
    else:
        user, password = userinfo, ""
    # Dashboard 复制的 URI 常对密码做百分号编码；关键字连接必须还原
    return scheme, unquote(user), unquote(password), hostport, path_and_query


def normalize_database_url(database_url: str) -> str:
    """清洗为可解析的 URI 字符串（日志/工具用）。

    运行时连接请用 ``conninfo_from_database_url`` + ``connect``（关键字参数，
    密码含特殊字符更稳）。本函数去掉 ``pgbouncer``，并对 user/password 做百分号编码。
    """
    scheme, user, password, hostport, path_and_query = _split_authority(database_url)
    path = path_and_query
    query = ""
    if "?" in path_and_query:
        path, query = path_and_query.split("?", 1)
        if "#" in query:
            query = query.split("#", 1)[0]

    filtered = [
        (k, v)
        for k, v in parse_qsl(query, keep_blank_values=True)
        if k.lower() not in _UNSUPPORTED_URI_QUERY_KEYS
    ]
    # 密码按 URI 组件编码，避免 []@#? 等破坏解析
    safe_password = quote(password, safe="")
    base = f"{scheme}://{quote(user, safe='')}:{safe_password}@{hostport}{path or '/'}"
    if filtered:
        return f"{base}?{urlencode(filtered)}"
    return base


def conninfo_from_database_url(database_url: str) -> dict[str, Any]:
    """解析为 psycopg 关键字参数（密码可含特殊字符，无需事先 URL 编码）。"""
    _, user, password, hostport, path_and_query = _split_authority(database_url)

    path = path_and_query
    query = ""
    if "?" in path_and_query:
        path, query = path_and_query.split("?", 1)
        if "#" in query:
            query = query.split("#", 1)[0]
    dbname = (path or "/").lstrip("/").split("/", 1)[0] or "postgres"

    # host:port 或 [ipv6]:port
    if hostport.startswith("["):
        end = hostport.find("]")
        if end < 0:
            raise ValueError("DATABASE_URL has invalid IPv6 host")
        host = hostport[1:end]
        port_s = hostport[end + 1 :]
        port = int(port_s[1:]) if port_s.startswith(":") and port_s[1:] else 5432
    elif hostport.count(":") == 1:
        host, port_s = hostport.split(":", 1)
        port = int(port_s)
    else:
        host, port = hostport, 5432

    conninfo: dict[str, Any] = {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "dbname": dbname,
    }
    for key, value in parse_qsl(query, keep_blank_values=True):
        if key.lower() in _UNSUPPORTED_URI_QUERY_KEYS:
            continue
        conninfo[key] = value

    # Supabase 需要 SSL；模板常漏 sslmode
    if "supabase.com" in host and "sslmode" not in conninfo:
        conninfo["sslmode"] = "require"
    return conninfo


def _uses_transaction_pooler(conninfo: dict[str, Any], raw_url: str) -> bool:
    """Supabase 事务池（6543 / pgbouncer）不支持 prepared statements。"""
    if "pgbouncer" in raw_url.lower():
        return True
    return int(conninfo.get("port") or 0) == 6543


def connect(database_url: str) -> Connection[dict[str, Any]]:
    conninfo = conninfo_from_database_url(database_url)
    kwargs: dict[str, Any] = {**conninfo, "row_factory": dict_row}
    if _uses_transaction_pooler(conninfo, database_url):
        kwargs["prepare_threshold"] = None
    return psycopg.connect(**kwargs)


def create_sync_run(
    conn: Connection[dict[str, Any]],
    job_name: str,
    codes: tuple[str, ...],
    meta: dict[str, Any] | None = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into sync_runs (job_name, status, index_codes, meta)
            values (%s, 'running', %s, %s)
            returning id
            """,
            (job_name, list(codes), Jsonb(meta or {})),
        )
        row = cur.fetchone()
    conn.commit()
    if not row:
        raise RuntimeError("failed to create sync run")
    return int(row["id"])


def finish_sync_run(
    conn: Connection[dict[str, Any]],
    run_id: int,
    success_codes: list[str],
    failures: list[dict[str, str]],
    meta: dict[str, Any] | None = None,
) -> None:
    status = "success"
    if failures and success_codes:
        status = "partial"
    elif failures:
        status = "failed"

    with conn.cursor() as cur:
        if meta is None:
            cur.execute(
                """
                update sync_runs
                set status = %s,
                    finished_at = %s,
                    success_codes = %s,
                    success_count = %s,
                    failure_count = %s,
                    error_summary = %s
                where id = %s
                """,
                (
                    status,
                    datetime.now(UTC),
                    success_codes,
                    len(success_codes),
                    len(failures),
                    Jsonb(failures),
                    run_id,
                ),
            )
        else:
            cur.execute(
                """
                update sync_runs
                set status = %s,
                    finished_at = %s,
                    success_codes = %s,
                    success_count = %s,
                    failure_count = %s,
                    error_summary = %s,
                    meta = coalesce(meta, '{}'::jsonb) || %s
                where id = %s
                """,
                (
                    status,
                    datetime.now(UTC),
                    success_codes,
                    len(success_codes),
                    len(failures),
                    Jsonb(failures),
                    Jsonb(meta),
                    run_id,
                ),
            )
    conn.commit()


def fetch_etf_pool(
    conn: Connection[dict[str, Any]],
    excluded_codes: Sequence[str],
) -> list[dict[str, Any]]:
    """读取当前 ETF 池全表（禁止按 max(snapshot_date) 过滤）。表名：etf_pool。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            select etf_code, snapshot_date
            from public.etf_pool
            where etf_code <> all(%s)
            order by etf_code
            """,
            (list(excluded_codes),),
        )
        return list(cur.fetchall())


def get_etf_max_trade_date(conn: Connection[dict[str, Any]], etf_code: str) -> date | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select max(trade_date) as last_date
            from public.etf_daily
            where etf_code = %s
            """,
            (etf_code,),
        )
        row = cur.fetchone()
    if not row or row["last_date"] is None:
        return None
    return row["last_date"]


def get_etf_anchor_qfq(
    conn: Connection[dict[str, Any]], etf_code: str
) -> tuple[date, float | None] | None:
    """取该只最早交易日及对应 close_qfq，作为除权检测锚点。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            select trade_date, close_qfq
            from public.etf_daily
            where etf_code = %s
            order by trade_date asc
            limit 1
            """,
            (etf_code,),
        )
        row = cur.fetchone()
    if not row:
        return None
    close_qfq = row["close_qfq"]
    return row["trade_date"], float(close_qfq) if close_qfq is not None else None


def get_etf_hfq_scale(conn: Connection[dict[str, Any]], etf_code: str) -> float | None:
    """全历史首日 close/close_qfq，供 incremental / adj_check 固定后复权锚定。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            select close, close_qfq
            from public.etf_daily
            where etf_code = %s
              and close is not null
              and close_qfq is not null
              and close_qfq <> 0
            order by trade_date asc
            limit 1
            """,
            (etf_code,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return float(row["close"]) / float(row["close_qfq"])


def count_etf_rows(conn: Connection[dict[str, Any]], etf_code: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) as cnt from public.etf_daily where etf_code = %s",
            (etf_code,),
        )
        row = cur.fetchone()
    return int(row["cnt"]) if row else 0


def existing_trade_dates(
    conn: Connection[dict[str, Any]],
    etf_code: str,
    trade_dates: Sequence[date],
) -> set[date]:
    if not trade_dates:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            """
            select trade_date
            from public.etf_daily
            where etf_code = %s
              and trade_date = any(%s)
            """,
            (etf_code, list(trade_dates)),
        )
        return {row["trade_date"] for row in cur.fetchall()}


def upsert_etf_daily_bars(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """写入不复权 OHLCV + 复权列 + price_source；复权列非空才覆盖。

    已有行 ``price_source`` 属于官网锁定集（``sse``/``szse``）时跳过 UPDATE，
    避免主写冲掉 ``--apply-official`` 纠偏结果。
    """
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"""
            insert into public.etf_daily (
              etf_code, trade_date,
              open, high, low, close, volume,
              open_qfq, high_qfq, low_qfq, close_qfq,
              open_hfq, high_hfq, low_hfq, close_hfq,
              price_source, updated_at
            ) values (
              %(etf_code)s, %(trade_date)s,
              %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
              %(open_qfq)s, %(high_qfq)s, %(low_qfq)s, %(close_qfq)s,
              %(open_hfq)s, %(high_hfq)s, %(low_hfq)s, %(close_hfq)s,
              %(price_source)s, now()
            )
            on conflict (etf_code, trade_date) do update
            set open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                open_qfq = coalesce(excluded.open_qfq, etf_daily.open_qfq),
                high_qfq = coalesce(excluded.high_qfq, etf_daily.high_qfq),
                low_qfq = coalesce(excluded.low_qfq, etf_daily.low_qfq),
                close_qfq = coalesce(excluded.close_qfq, etf_daily.close_qfq),
                open_hfq = coalesce(excluded.open_hfq, etf_daily.open_hfq),
                high_hfq = coalesce(excluded.high_hfq, etf_daily.high_hfq),
                low_hfq = coalesce(excluded.low_hfq, etf_daily.low_hfq),
                close_hfq = coalesce(excluded.close_hfq, etf_daily.close_hfq),
                price_source = excluded.price_source,
                updated_at = now()
            where coalesce(etf_daily.price_source, '')
                  not in ({_ETF_DAILY_LOCKED_PRICE_SOURCES_SQL})
            """,
            values,
        )
    return len(values)


def update_etf_adj_columns(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """仅 UPDATE 已有行的复权列；不改 price_source / 不复权 OHLCV。

    官网锁定行（``sse``/``szse``）整行跳过，与主写 UPSERT 一致。
    """
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            f"""
            update public.etf_daily
            set open_qfq = coalesce(%(open_qfq)s, open_qfq),
                high_qfq = coalesce(%(high_qfq)s, high_qfq),
                low_qfq = coalesce(%(low_qfq)s, low_qfq),
                close_qfq = coalesce(%(close_qfq)s, close_qfq),
                open_hfq = coalesce(%(open_hfq)s, open_hfq),
                high_hfq = coalesce(%(high_hfq)s, high_hfq),
                low_hfq = coalesce(%(low_hfq)s, low_hfq),
                close_hfq = coalesce(%(close_hfq)s, close_hfq),
                updated_at = now()
            where etf_code = %(etf_code)s
              and trade_date = %(trade_date)s
              and coalesce(price_source, '')
                  not in ({_ETF_DAILY_LOCKED_PRICE_SOURCES_SQL})
            """,
            values,
        )
    return len(values)


def update_etf_daily_ohlc_official(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """官网纠偏：仅 UPDATE 已有行的不复权 OHLC；同步标记 price_source。"""
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            update public.etf_daily
            set open = coalesce(%(open)s, open),
                high = coalesce(%(high)s, high),
                low = coalesce(%(low)s, low),
                close = %(close)s,
                price_source = %(price_source)s,
                updated_at = now()
            where etf_code = %(etf_code)s
              and trade_date = %(trade_date)s
            """,
            values,
        )
    return len(values)


def update_index_valuation_pe_official(
    conn: Connection[dict[str, Any]],
    *,
    tracking_index_code: str,
    trade_date: date,
    current_pe_ttm: float,
) -> int:
    """官网纠偏：仅 UPDATE 已有估值快照的当日 PE（不改 5y/10y）。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            update public.index_valuation
            set current_pe_ttm = %s,
                trade_date = %s,
                updated_at = now()
            where tracking_index_code = %s
            """,
            (current_pe_ttm, trade_date, tracking_index_code),
        )
        return cur.rowcount


def fetch_etf_daily_rows(
    conn: Connection[dict[str, Any]],
    etf_code: str,
    trade_dates: Sequence[date],
) -> dict[date, dict[str, Any]]:
    """按日期取 etf_daily 行（校验用）。"""
    if not trade_dates:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            """
            select
              etf_code, trade_date,
              open, high, low, close, volume,
              open_qfq, high_qfq, low_qfq, close_qfq,
              open_hfq, high_hfq, low_hfq, close_hfq,
              price_source
            from public.etf_daily
            where etf_code = %s
              and trade_date = any(%s)
            """,
            (etf_code, list(trade_dates)),
        )
        return {row["trade_date"]: dict(row) for row in cur.fetchall()}


def insert_etf_daily_bars_ignore_conflict(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """仅 INSERT 缺失 (etf_code, trade_date)；已有行不改写。"""
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into public.etf_daily (
              etf_code, trade_date,
              open, high, low, close, volume,
              open_qfq, high_qfq, low_qfq, close_qfq,
              open_hfq, high_hfq, low_hfq, close_hfq,
              price_source, updated_at
            ) values (
              %(etf_code)s, %(trade_date)s,
              %(open)s, %(high)s, %(low)s, %(close)s, %(volume)s,
              %(open_qfq)s, %(high_qfq)s, %(low_qfq)s, %(close_qfq)s,
              %(open_hfq)s, %(high_hfq)s, %(low_hfq)s, %(close_hfq)s,
              %(price_source)s, now()
            )
            on conflict (etf_code, trade_date) do nothing
            """,
            values,
        )
    return len(values)


def ensure_index_row(
    conn: Connection[dict[str, Any]],
    *,
    code: str,
    name: str,
    category: str = "行业主题",
    display_order: int = 0,
) -> None:
    """确保 indices 元数据行存在（FK 前置）；已有行不覆盖 name/category。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.indices (code, name, category, display_order)
            values (%s, %s, %s, %s)
            on conflict (code) do nothing
            """,
            (code, name, category, display_order),
        )


def fetch_index_valuation(
    conn: Connection[dict[str, Any]],
    tracking_index_code: str,
) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select
              tracking_index_code, trade_date,
              current_pe_ttm, pe_ttm_avg_5y, pe_ttm_avg_10y, updated_at
            from public.index_valuation
            where tracking_index_code = %s
            """,
            (tracking_index_code,),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def upsert_index_valuation(
    conn: Connection[dict[str, Any]],
    row: dict[str, Any],
) -> None:
    """刷新跟踪指数估值快照（当日 PE + 5y/10y 均值）；按 tracking_index_code upsert。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into public.index_valuation (
              tracking_index_code, trade_date,
              current_pe_ttm, pe_ttm_avg_5y, pe_ttm_avg_10y, updated_at
            ) values (
              %(tracking_index_code)s, %(trade_date)s,
              %(current_pe_ttm)s, %(pe_ttm_avg_5y)s, %(pe_ttm_avg_10y)s, now()
            )
            on conflict (tracking_index_code) do update
            set trade_date = excluded.trade_date,
                current_pe_ttm = excluded.current_pe_ttm,
                pe_ttm_avg_5y = excluded.pe_ttm_avg_5y,
                pe_ttm_avg_10y = excluded.pe_ttm_avg_10y,
                updated_at = now()
            """,
            row,
        )


def upsert_index_daily_metrics(
    conn: Connection[dict[str, Any]],
    rows: Iterable[dict[str, Any]],
) -> int:
    """按 (index_code, trade_date) 字段级 coalesce upsert 日指标。

    仅覆盖传入的非空字段，避免用 null 抹掉已有 close / PE / PB。
    """
    values = list(rows)
    if not values:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            insert into public.index_daily_metrics as m (
              index_code, trade_date, close, pe_ttm, pb,
              price_source, valuation_source, updated_at
            ) values (
              %(index_code)s, %(trade_date)s, %(close)s, %(pe_ttm)s, %(pb)s,
              %(price_source)s, %(valuation_source)s, now()
            )
            on conflict (index_code, trade_date) do update set
              close = coalesce(excluded.close, m.close),
              pe_ttm = coalesce(excluded.pe_ttm, m.pe_ttm),
              pb = coalesce(excluded.pb, m.pb),
              price_source = coalesce(excluded.price_source, m.price_source),
              valuation_source = coalesce(excluded.valuation_source, m.valuation_source),
              updated_at = now()
            """,
            values,
        )
    return len(values)


def replace_index_industry_weights(
    conn: Connection[dict[str, Any]],
    index_code: str,
    rows: Iterable[dict[str, Any]],
) -> int:
    """以红色火箭为该指数行业权重主源：删光后写入本次快照行。"""
    values = list(rows)
    with conn.cursor() as cur:
        cur.execute(
            "delete from public.index_industry_weights where index_code = %s",
            (index_code,),
        )
        if not values:
            return 0
        cur.executemany(
            """
            insert into public.index_industry_weights (
              index_code, as_of_date, sw_level, industry_name, weight_pct, updated_at
            ) values (
              %(index_code)s, %(as_of_date)s, %(sw_level)s, %(industry_name)s,
              %(weight_pct)s, now()
            )
            """,
            values,
        )
    return len(values)
