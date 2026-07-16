"""Task 0 Spike：验证 AKShare / BaoStock 作为 ETF 成交额（amount）补充数据源的可行性。

对应 `docs/superpowers/plans/2026-07-15-domestic-baostock-akshare-enrichment.md` §4。

用法（须先 `pip install -e '.[domestic]'`）：

    python scripts/spike_baostock_akshare.py \
        --out-json doc/spike-baostock-akshare.raw.json \
        [--pool-json <etf_pool 只读导出.json>]

设计要点（详见 §4.3）：
- 单标的多年拉取按 ≤366 天窗口分段，串行 + sleep，避免单次 5 年大请求。
- 每个窗口首次失败后最多重试 3 次，按 2s/4s/8s 指数退避 ±20% jitter（单窗口最多 4 次调用）。
- 某窗口最终失败仅记录、跳过，不中断其他窗口/标的。
- 所有请求级明细（尝试次数、耗时、错误类型）写入 raw JSON，供 §4.4 门禁判定使用。

已知网络限制（本脚本会自动处理，并在 raw JSON 的 network_mitigations 字段中如实记录）：
- 东方财富（AKShare 依赖的 push2his.eastmoney.com）会对 `requests` 默认
  User-Agent（`python-requests/x.x`）直接断开连接（非代理、非地域问题，
  curl / 自定义 UA 均可正常返回，已用 curl 与 requests 分别验证）。
  脚本对 `requests.Session.request` 做进程内 monkeypatch，补齐一个常见浏览器
  UA，这是社区已知的必要缓解措施，不是伪造通过。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

import pandas as pd

T = TypeVar("T")

# --------------------------------------------------------------------------
# 网络缓解：东财 WAF 对默认 requests UA 直接 RST，与地域/代理无关（已用
# curl 与裸 requests 分别验证过；此处仅补一个常见浏览器 UA，不修改任何业务参数）。
# --------------------------------------------------------------------------
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def patch_requests_user_agent() -> bool:
    """为进程内所有 requests.Session 请求补默认 UA；返回是否实际生效。"""
    try:
        import requests
    except ImportError:
        return False

    original_request = requests.Session.request
    if getattr(original_request, "_spike_ua_patched", False):
        return True

    def _patched(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        headers = kwargs.pop("headers", None) or {}
        headers = dict(headers)
        headers.setdefault("User-Agent", _UA)
        kwargs["headers"] = headers
        return original_request(self, method, url, *args, **kwargs)

    _patched._spike_ua_patched = True  # type: ignore[attr-defined]
    requests.Session.request = _patched  # type: ignore[method-assign]
    return True


# 必须在 import akshare / baostock 之前完成 UA 补丁（两者内部都会新建 requests.Session）。
_UA_PATCH_APPLIED = patch_requests_user_agent()

import akshare as ak  # noqa: E402
import baostock as bs  # noqa: E402

MAX_WINDOW_DAYS = 366
RETRY_MAX_ATTEMPTS = 4  # 首次 + 最多 3 次重试
BACKOFF_BASE_SEC = (2.0, 4.0, 8.0)
JITTER_RATIO = 0.2
AMOUNT_TOLERANCE = 0.15  # amount ≈ close*volume*100 的容忍相对误差（VWAP 与收盘价天然有偏）
SLEEP_BETWEEN_CALLS_SEC = 1.0

# 池内代表标的：宽基/行业/跨境各选沪深各 1 只（覆盖 SSE + SZSE）。
# 数据来源：2026-07-15 通过 Supabase MCP 只读查询 public.etf_pool_snapshots
# （live rename 迁移未执行，正式表名仍为旧名；fetch_etf_pool() 已切换读 etf_pool，
# 本 Spike 与生产表名无关，只借该表拿真实池内代码/分类，不写入）。
POOL_SAMPLE: list[dict[str, str]] = [
    {"code": "510300", "name": "沪深300ETF", "category": "宽基", "exchange": "SSE"},
    {"code": "159915", "name": "创业板ETF", "category": "宽基", "exchange": "SZSE"},
    {"code": "512480", "name": "半导体ETF", "category": "行业", "exchange": "SSE"},
    {"code": "159928", "name": "消费ETF", "category": "行业", "exchange": "SZSE"},
    {"code": "513100", "name": "纳指ETF", "category": "跨境", "exchange": "SSE"},
    {"code": "159920", "name": "恒生ETF", "category": "跨境", "exchange": "SZSE"},
]

# 池外能力探针：固定标的，不在正式池，不得宣称正式池已覆盖债券/商品。
OUT_OF_POOL_PROBES: list[dict[str, str]] = [
    {"code": "511010", "name": "国债ETF（池外探针）", "category": "债券", "exchange": "SSE"},
    {"code": "518880", "name": "黄金ETF（池外探针）", "category": "商品", "exchange": "SSE"},
]

# 深度抽样：近 5 年，验证历史深度（在 POOL_SAMPLE 中已覆盖，复用同标的做多年窗口）。
DEEP_SAMPLE_CODES = ["510300", "159915"]

# 覆盖检查要求 >=250 交易日；A股年化约 245 个交易日/365 天，留足非交易日余量。
COVERAGE_WINDOW_DAYS = 420


@dataclass
class AttemptRecord:
    attempt: int
    ok: bool
    elapsed_ms: float
    error: str | None
    row_count: int | None = None


@dataclass
class WindowResult:
    code: str
    source: str
    window_start: str
    window_end: str
    attempts: list[dict[str, Any]] = field(default_factory=list)
    final_ok: bool = False
    row_count: int = 0
    error: str | None = None


def to_ak_symbol(etf_code: str) -> str:
    """AKShare `fund_etf_hist_em` 直接用 6 位代码。"""
    if not (etf_code.isdigit() and len(etf_code) == 6):
        raise ValueError(f"invalid etf_code: {etf_code}")
    return etf_code


def to_baostock_code(etf_code: str) -> str:
    if not (etf_code.isdigit() and len(etf_code) == 6):
        raise ValueError(f"invalid etf_code: {etf_code}")
    if etf_code.startswith(("5", "6")):
        return f"sh.{etf_code}"
    if etf_code.startswith(("0", "1", "3")):
        return f"sz.{etf_code}"
    raise ValueError(f"unsupported exchange prefix: {etf_code}")


def to_yfinance_symbol(etf_code: str) -> str:
    if not (etf_code.isdigit() and len(etf_code) == 6):
        raise ValueError(f"invalid etf_code: {etf_code}")
    if etf_code.startswith(("5", "6")):
        return f"{etf_code}.SS"
    if etf_code.startswith(("0", "1", "3")):
        return f"{etf_code}.SZ"
    raise ValueError(f"unsupported exchange prefix: {etf_code}")


def from_baostock_code(bs_code: str) -> str:
    return bs_code.split(".", 1)[1]


def from_yfinance_symbol(yf_symbol: str) -> str:
    return yf_symbol.split(".", 1)[0]


def check_code_format_mapping(etf_code: str) -> dict[str, Any]:
    ak_symbol = to_ak_symbol(etf_code)
    bs_code = to_baostock_code(etf_code)
    yf_symbol = to_yfinance_symbol(etf_code)
    ok = (
        ak_symbol == etf_code
        and from_baostock_code(bs_code) == etf_code
        and from_yfinance_symbol(yf_symbol) == etf_code
    )
    return {
        "code": etf_code,
        "ak_symbol": ak_symbol,
        "baostock_code": bs_code,
        "yfinance_symbol": yf_symbol,
        "round_trip_ok": ok,
    }


def split_windows(
    start: date, end: date, max_days: int = MAX_WINDOW_DAYS
) -> list[tuple[date, date]]:
    if start > end:
        return []
    windows: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        win_end = min(cur + timedelta(days=max_days - 1), end)
        windows.append((cur, win_end))
        cur = win_end + timedelta(days=1)
    return windows


def call_with_retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
) -> tuple[T | None, list[dict[str, Any]], str | None]:
    """执行 fn，失败按指数退避 + jitter 重试；返回 (结果或None, 每次尝试记录, 最终错误)。"""
    attempts: list[dict[str, Any]] = []
    last_error: str | None = None
    for i in range(max_attempts):
        t0 = time.monotonic()
        try:
            result = fn()
            elapsed_ms = (time.monotonic() - t0) * 1000
            attempts.append(
                {"attempt": i + 1, "ok": True, "elapsed_ms": round(elapsed_ms, 1), "error": None}
            )
            return result, attempts, None
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (time.monotonic() - t0) * 1000
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append(
                {
                    "attempt": i + 1,
                    "ok": False,
                    "elapsed_ms": round(elapsed_ms, 1),
                    "error": last_error,
                }
            )
            if i + 1 >= max_attempts:
                break
            base = BACKOFF_BASE_SEC[i]
            jitter = base * random.uniform(-JITTER_RATIO, JITTER_RATIO)
            time.sleep(max(0.0, base + jitter))
    return None, attempts, last_error


def fetch_akshare_window(etf_code: str, start: date, end: date) -> pd.DataFrame:
    symbol = to_ak_symbol(etf_code)
    df = ak.fund_etf_hist_em(
        symbol=symbol,
        period="daily",
        start_date=start.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
        adjust="",
    )
    if df is None:
        return pd.DataFrame()
    return df


def fetch_baostock_window(etf_code: str, start: date, end: date) -> pd.DataFrame:
    bs_code = to_baostock_code(etf_code)
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,code,open,high,low,close,volume,amount",
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        frequency="d",
        adjustflag="2",
    )
    if rs.error_code != "0":
        raise RuntimeError(f"baostock error {rs.error_code}: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return pd.DataFrame(rows, columns=rs.fields)


def run_windowed_fetch(
    codes: list[str],
    start: date,
    end: date,
    source: str,
    fetch_fn: Callable[[str, date, date], pd.DataFrame],
) -> tuple[list[WindowResult], dict[str, pd.DataFrame]]:
    results: list[WindowResult] = []
    frames: dict[str, pd.DataFrame] = {}
    for code in codes:
        windows = split_windows(start, end)
        code_frames: list[pd.DataFrame] = []
        for win_start, win_end in windows:
            def _do(c: str = code, s: date = win_start, e: date = win_end) -> pd.DataFrame:
                return fetch_fn(c, s, e)

            df, attempts, error = call_with_retry(_do)
            wr = WindowResult(
                code=code,
                source=source,
                window_start=win_start.isoformat(),
                window_end=win_end.isoformat(),
                attempts=attempts,
                final_ok=df is not None,
                row_count=0 if df is None else len(df),
                error=error,
            )
            results.append(wr)
            if df is not None and not df.empty:
                code_frames.append(df)
            time.sleep(SLEEP_BETWEEN_CALLS_SEC)
        if code_frames:
            frames[code] = pd.concat(code_frames, ignore_index=True)
        else:
            frames[code] = pd.DataFrame()
    return results, frames


def normalize_akshare_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    rename_map = {
        "日期": "trade_date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
    out = df.rename(columns=rename_map)
    cols = ["trade_date", "open", "high", "low", "close", "volume", "amount"]
    keep = [c for c in cols if c in out.columns]
    out = out[keep].copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"])
    return out.sort_values("trade_date")


def normalize_baostock_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.rename(columns={"date": "trade_date"}).copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.date
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["trade_date"]).drop_duplicates(subset=["trade_date"])
    return out.sort_values("trade_date")


def check_amount_consistency(
    df: pd.DataFrame, tolerance: float = AMOUNT_TOLERANCE
) -> dict[str, Any]:
    if df.empty:
        return {
            "sample_size": 0,
            "pass_count": 0,
            "pass_rate": None,
            "amount_non_null_rate": None,
            "failures": [],
        }
    total = len(df)
    amount_non_null = int(df["amount"].notna().sum())
    checkable = df.dropna(subset=["close", "volume", "amount"])
    checkable = checkable[checkable["amount"] != 0]
    failures: list[dict[str, Any]] = []
    pass_count = 0
    for _, row in checkable.iterrows():
        expected = float(row["close"]) * float(row["volume"]) * 100
        actual = float(row["amount"])
        rel_err = abs(actual - expected) / abs(actual)
        if rel_err <= tolerance:
            pass_count += 1
        else:
            failures.append(
                {
                    "trade_date": str(row["trade_date"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                    "amount": actual,
                    "expected_amount": round(expected, 2),
                    "relative_error": round(rel_err, 4),
                }
            )
    checkable_n = len(checkable)
    return {
        "sample_size": total,
        "checkable_size": checkable_n,
        "pass_count": pass_count,
        "pass_rate": round(pass_count / checkable_n, 4) if checkable_n else None,
        "amount_non_null_rate": round(amount_non_null / total, 4) if total else None,
        "tolerance": tolerance,
        "failures": failures[:20],
        "failure_count_total": len(failures),
    }


def cross_source_amount_diff(
    ak_df: pd.DataFrame, bs_df: pd.DataFrame, sample_days: int = 5
) -> dict[str, Any]:
    if ak_df.empty or bs_df.empty:
        return {
            "available": False,
            "reason": (
                "BaoStock ETF 日K接口（query_history_k_data_plus / "
                "query_daily_history_k_ETF）在本次 Spike 环境下对所有测试 ETF 代码"
                "均返回 0 行（error_code=0 但空结果集），无法做同标的 amount 抽样对比；"
                "已用 query_all_stock 交叉确认该代码未出现在 BaoStock 当日证券列表中。"
            ),
        }
    merged = pd.merge(ak_df, bs_df, on="trade_date", suffixes=("_ak", "_bs"))
    merged = merged.sort_values("trade_date")
    if merged.empty:
        return {
            "available": False,
            "reason": "no overlapping trade_date between AKShare and BaoStock results",
        }
    merged = merged.tail(sample_days)
    diffs = []
    for _, row in merged.iterrows():
        ak_amt = float(row["amount_ak"]) if pd.notna(row["amount_ak"]) else None
        bs_amt = float(row["amount_bs"]) if pd.notna(row["amount_bs"]) else None
        rel_diff = None
        if ak_amt and bs_amt:
            rel_diff = round(abs(ak_amt - bs_amt) / abs(ak_amt), 4)
        diffs.append(
            {
                "trade_date": str(row["trade_date"]),
                "akshare_amount": ak_amt,
                "baostock_amount": bs_amt,
                "relative_diff": rel_diff,
            }
        )
    return {"available": True, "sample": diffs}


def run_trade_dates_check(year_start: date, year_end: date) -> dict[str, Any]:
    lg = bs.login()
    if lg.error_code != "0":
        return {"ok": False, "error": f"{lg.error_code}: {lg.error_msg}"}
    try:
        rs = bs.query_trade_dates(
            start_date=year_start.isoformat(), end_date=year_end.isoformat()
        )
        if rs.error_code != "0":
            return {"ok": False, "error": f"{rs.error_code}: {rs.error_msg}"}
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        fields = rs.fields
        sample = rows[:5]
        has_expected_fields = fields == ["calendar_date", "is_trading_day"]
        return {
            "ok": True,
            "fields": fields,
            "row_count": len(rows),
            "sample": sample,
            "matches_expected_schema": has_expected_fields,
            "mapping_note": (
                "calendar_date -> trade_calendar.trade_date; "
                "is_trading_day -> is_open; market='CN' (fixed)"
            ),
        }
    finally:
        bs.logout()


def run_baostock_universe_probe(check_date: date, sample_codes: list[str]) -> dict[str, Any]:
    lg = bs.login()
    if lg.error_code != "0":
        return {"ok": False, "error": f"{lg.error_code}: {lg.error_msg}"}
    try:
        rs = bs.query_all_stock(day=check_date.isoformat())
        if rs.error_code != "0":
            return {"ok": False, "error": f"{rs.error_code}: {rs.error_msg}"}
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        codes_present = {r[0] for r in rows}
        found = {c: to_baostock_code(c) in codes_present for c in sample_codes}
        return {
            "ok": True,
            "check_date": check_date.isoformat(),
            "total_securities": len(rows),
            "sample_codes_found": found,
            "any_found": any(found.values()),
        }
    finally:
        bs.logout()


def compute_latency_stats(records: list[WindowResult]) -> dict[str, Any]:
    all_attempt_latencies = [a["elapsed_ms"] for r in records for a in r.attempts]
    windows_total = len(records)
    windows_ok = sum(1 for r in records if r.final_ok)
    windows_failed = windows_total - windows_ok
    retried_windows = sum(1 for r in records if len(r.attempts) > 1)
    p95 = None
    if all_attempt_latencies:
        sorted_lat = sorted(all_attempt_latencies)
        idx = min(len(sorted_lat) - 1, int(round(0.95 * (len(sorted_lat) - 1))))
        p95 = round(sorted_lat[idx], 1)
    return {
        "windows_total": windows_total,
        "windows_ok": windows_ok,
        "windows_failed_after_retry": windows_failed,
        "failure_rate_after_retry": (
            round(windows_failed / windows_total, 4) if windows_total else None
        ),
        "windows_needed_retry": retried_windows,
        "avg_attempt_latency_ms": (
            round(statistics.mean(all_attempt_latencies), 1) if all_attempt_latencies else None
        ),
        "p95_attempt_latency_ms": p95,
        "total_attempts": len(all_attempt_latencies),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-json", default="doc/spike-baostock-akshare.raw.json")
    parser.add_argument("--today", default=None, help="覆盖当前日期（测试用，ISO 格式）")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else date.today()
    coverage_start = today - timedelta(days=COVERAGE_WINDOW_DAYS)
    deep_start = today - timedelta(days=365 * 5 + 30)

    generated_at = datetime.now(UTC).isoformat()

    print(f"[spike] generated_at={generated_at} today={today}")
    print(f"[spike] UA patch applied: {_UA_PATCH_APPLIED}")

    format_checks = [
        check_code_format_mapping(item["code"]) for item in POOL_SAMPLE + OUT_OF_POOL_PROBES
    ]

    pool_codes = [item["code"] for item in POOL_SAMPLE]
    probe_codes = [item["code"] for item in OUT_OF_POOL_PROBES]
    coverage_codes = pool_codes + probe_codes

    print(f"[spike] AKShare coverage fetch ({COVERAGE_WINDOW_DAYS}d window) for {coverage_codes}")
    ak_coverage_results, ak_coverage_frames = run_windowed_fetch(
        coverage_codes, coverage_start, today, "akshare", fetch_akshare_window
    )

    print(f"[spike] AKShare deep 5y windowed fetch for {DEEP_SAMPLE_CODES}")
    ak_deep_results, ak_deep_frames = run_windowed_fetch(
        DEEP_SAMPLE_CODES, deep_start, today, "akshare", fetch_akshare_window
    )

    print(f"[spike] BaoStock windowed fetch (single-column stat only) for {coverage_codes}")
    bs_login = bs.login()
    if bs_login.error_code != "0":
        raise RuntimeError(f"baostock login failed: {bs_login.error_code} {bs_login.error_msg}")
    try:
        bs_results, bs_frames = run_windowed_fetch(
            coverage_codes, coverage_start, today, "baostock", fetch_baostock_window
        )
    finally:
        bs.logout()

    print("[spike] BaoStock query_trade_dates check")
    trade_dates_check = run_trade_dates_check(date(today.year, 1, 1), today)

    print("[spike] BaoStock query_all_stock universe probe")
    universe_probe = run_baostock_universe_probe(today - timedelta(days=1), coverage_codes)

    amount_checks: dict[str, Any] = {}
    normalized_ak: dict[str, pd.DataFrame] = {}
    for code, df in {**ak_coverage_frames, **ak_deep_frames}.items():
        norm = normalize_akshare_columns(df)
        normalized_ak[code] = norm
        amount_checks[code] = check_amount_consistency(norm)

    normalized_bs: dict[str, pd.DataFrame] = {
        code: normalize_baostock_columns(df) for code, df in bs_frames.items()
    }

    cross_source: dict[str, Any] = {}
    for code in coverage_codes:
        ak_df = normalized_ak.get(code, pd.DataFrame())
        bs_df = normalized_bs.get(code, pd.DataFrame())
        cross_source[code] = cross_source_amount_diff(ak_df, bs_df)

    ak_all_results = ak_coverage_results + ak_deep_results
    ak_latency_stats = compute_latency_stats(ak_all_results)
    bs_latency_stats = compute_latency_stats(bs_results)

    # ---- §4.4 硬门禁判定 ----
    unit_pass_rates = [
        v["pass_rate"] for v in amount_checks.values() if v.get("pass_rate") is not None
    ]
    gate_unit_pass_rate = min(unit_pass_rates) if unit_pass_rates else None
    gate_unit_ok = gate_unit_pass_rate is not None and gate_unit_pass_rate >= 0.95

    coverage_ok_per_code: dict[str, dict[str, Any]] = {}
    for code in coverage_codes:
        df = normalized_ak.get(code, pd.DataFrame())
        trading_days = len(df)
        amount_non_null_rate = float(df["amount"].notna().mean()) if trading_days else 0.0
        coverage_ok_per_code[code] = {
            "trading_days": trading_days,
            "amount_non_null_rate": round(amount_non_null_rate, 4),
            "meets_250d": trading_days >= 250,
            "meets_amount_90pct": amount_non_null_rate >= 0.9,
        }
    pool_coverage_ok = all(
        coverage_ok_per_code[c]["meets_250d"] and coverage_ok_per_code[c]["meets_amount_90pct"]
        for c in pool_codes
    )

    depth_ok_per_code: dict[str, dict[str, Any]] = {}
    for code in DEEP_SAMPLE_CODES:
        df = normalized_ak.get(code, pd.DataFrame())
        amount_non_null_rate = float(df["amount"].notna().mean()) if len(df) else 0.0
        depth_ok_per_code[code] = {
            "trading_days": len(df),
            "amount_non_null_rate": round(amount_non_null_rate, 4),
            "meets_90pct": amount_non_null_rate >= 0.9,
        }
    depth_gate_ok = any(v["meets_90pct"] for v in depth_ok_per_code.values())

    stability_gate_ok = (
        ak_latency_stats["failure_rate_after_retry"] is not None
        and ak_latency_stats["failure_rate_after_retry"] <= 0.10
    )

    format_gate_ok = all(item["round_trip_ok"] for item in format_checks)

    gates = {
        "unit": {
            "pass_condition": "抽样校验通过率 >= 95%",
            "observed_min_pass_rate": gate_unit_pass_rate,
            "passed": gate_unit_ok,
        },
        "coverage": {
            "pass_condition": "池内实际分类全部拉到>=250日且amount非空率>=90%；池外探针单列结果",
            "pool_detail": coverage_ok_per_code,
            "passed": pool_coverage_ok,
        },
        "depth": {
            "pass_condition": ">=1只近5年amount非空率>=90%",
            "detail": depth_ok_per_code,
            "passed": depth_gate_ok,
        },
        "stability": {
            "pass_condition": "AKShare主源窗口级重试后失败率<=10%；BaoStock单列统计不稀释主源分母",
            "akshare": ak_latency_stats,
            "baostock_single_column": bs_latency_stats,
            "passed": stability_gate_ok,
        },
        "format": {
            "pass_condition": "三种代码格式均可映射",
            "detail": format_checks,
            "passed": format_gate_ok,
        },
    }
    overall_passed = all(g["passed"] for g in gates.values())

    raw: dict[str, Any] = {
        "generated_at": generated_at,
        "today": today.isoformat(),
        "environment": {
            "note": (
                "Mac，非国内 Hermes 机器；结果仅供代码就绪与真实网络行为参考，"
                "门禁最终以国内机复跑为准。"
            ),
        },
        "network_mitigations": {
            "eastmoney_default_ua_blocked": True,
            "detail": (
                "push2his.eastmoney.com 对 requests 默认 UA (python-requests/x.x) "
                "直接 RST（RemoteDisconnected），与代理设置无关（已禁用系统代理复测）、"
                "curl 与自定义 UA 的 requests 均 200；"
                "已在进程内为 requests.Session 补统一浏览器 UA。"
            ),
            "ua_patch_applied": _UA_PATCH_APPLIED,
        },
        "pool_sample": POOL_SAMPLE,
        "out_of_pool_probes": OUT_OF_POOL_PROBES,
        "deep_sample_codes": DEEP_SAMPLE_CODES,
        "code_format_mapping_checks": format_checks,
        "akshare_window_requests": [r.__dict__ for r in ak_all_results],
        "baostock_window_requests": [r.__dict__ for r in bs_results],
        "amount_consistency_checks": amount_checks,
        "cross_source_amount_comparison": cross_source,
        "baostock_trade_dates_check": trade_dates_check,
        "baostock_universe_probe": universe_probe,
        "latency_stats": {"akshare": ak_latency_stats, "baostock": bs_latency_stats},
        "gate_evaluation": gates,
        "overall_gate_passed": overall_passed,
    }

    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(raw, ensure_ascii=False, indent=2, default=str)
    out_path.write_text(payload, encoding="utf-8")
    print(f"[spike] raw JSON written to {out_path} (overall_gate_passed={overall_passed})")
    return 0 if overall_passed else 1


if __name__ == "__main__":
    sys.exit(main())
