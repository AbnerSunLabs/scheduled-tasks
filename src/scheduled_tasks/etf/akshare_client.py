"""AKShare ETF 日成交额客户端（仅国内机使用；东财失败时回退 BaoStock）。"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pandas as pd

AMOUNT_SOURCE = "akshare"
MAX_WINDOW_DAYS = 366
RETRY_BACKOFF_SECONDS = (2.0, 4.0, 8.0)
# 东财持续失败的熔断预算；enrich job 应跨 ETF 共享 AkshareGiveUpState
AKSHARE_GIVE_UP_SECONDS = 300.0
# proven 后连续窗口失败达此次数 → skip（避免每窗仍重试 4 次）
AKSHARE_CONSECUTIVE_FAIL_TO_SKIP = 3
# 相对库内待补日期：单窗覆盖率低于此值记 window_failures（防截断误报成功）
WINDOW_COVERAGE_MIN_RATIO = 0.95


@dataclass
class AkshareGiveUpState:
    """东财熔断状态（deadline / proven / skip），可在整个 job 内跨 ETF 共享。"""

    deadline: float
    proven: bool = False
    skip: bool = False
    consecutive_failures: int = 0


def new_akshare_give_up_state(
    *,
    give_up_seconds: float = AKSHARE_GIVE_UP_SECONDS,
) -> AkshareGiveUpState:
    """创建一份新的熔断状态（通常在 job 入口调用一次）。"""
    return AkshareGiveUpState(deadline=time.monotonic() + max(0.0, give_up_seconds))


def _mark_akshare_window_result(
    state: AkshareGiveUpState,
    *,
    ak_ok: bool,
    count_failure: bool = True,
) -> None:
    """根据本窗东财结果更新 proven / consecutive_failures / skip。

    ``count_failure=False`` 用于合法空响应（非宕机），避免年轻 ETF 上市前空窗误熔断。
    """
    if ak_ok:
        state.proven = True
        state.consecutive_failures = 0
        state.skip = False
        return
    if not state.proven:
        if time.monotonic() >= state.deadline:
            # 东财在预算内从未成功 → 本 job 后续 ETF/窗口直接 BaoStock
            state.skip = True
        return
    if not count_failure:
        return
    # 已 proven：仅连续异常触发熔断（不用「证明预算」deadline，避免 full 长任务误弃东财）
    state.consecutive_failures += 1
    if state.consecutive_failures >= AKSHARE_CONSECUTIVE_FAIL_TO_SKIP:
        state.skip = True


# 东财 WAF 对默认 python-requests UA 会 RST；生产 client 必须补 UA。
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_UA_PATCHED = False


def ensure_requests_user_agent() -> bool:
    """为进程内 requests.Session 补默认 UA，并避免系统代理劫持东财。"""
    global _UA_PATCHED
    if _UA_PATCHED:
        return True
    try:
        import requests
    except ImportError:
        return False

    original_request = requests.Session.request
    if getattr(original_request, "_enrich_ua_patched", False):
        _UA_PATCHED = True
        return True

    def _patched(self: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("User-Agent", _UA)
        kwargs["headers"] = headers
        # macOS 常开 127.0.0.1:7890 系统代理；东财经代理易 RST，本请求强制直连
        if "eastmoney.com" in (url or ""):
            kwargs.setdefault("proxies", {"http": None, "https": None})
            # 仅本请求关闭 trust_env，避免环境代理经 merge_environment_settings 再注入
            prev_trust = self.trust_env
            self.trust_env = False
            try:
                return original_request(self, method, url, *args, **kwargs)
            finally:
                self.trust_env = prev_trust
        return original_request(self, method, url, *args, **kwargs)

    _patched._enrich_ua_patched = True  # type: ignore[attr-defined]
    requests.Session.request = _patched  # type: ignore[method-assign]
    _UA_PATCHED = True
    return True


def to_six_digit_code(code: str) -> str:
    """统一映射为 6 位 ETF 代码。"""
    raw = code.strip()
    if "." in raw:
        left, right = raw.split(".", 1)
        if left.lower() in {"sh", "sz"} and right.isdigit() and len(right) == 6:
            return right
        if left.isdigit() and len(left) == 6 and right.upper() in {"SS", "SZ"}:
            return left
    if raw.isdigit() and len(raw) == 6:
        return raw
    raise ValueError(f"unsupported etf code format: {code}")


def iter_date_windows(
    start: date,
    end: date,
    *,
    max_days: int = MAX_WINDOW_DAYS,
) -> list[tuple[date, date]]:
    """按自然年/最多 max_days 分段（含端点）。"""
    if end < start:
        return []
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        year_end = date(cursor.year, 12, 31)
        window_end = min(end, year_end, cursor + timedelta(days=max_days - 1))
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return windows


def _sleep_backoff(attempt_index: int, *, max_seconds: float | None = None) -> None:
    base = RETRY_BACKOFF_SECONDS[min(attempt_index, len(RETRY_BACKOFF_SECONDS) - 1)]
    jitter = base * 0.2 * (2 * random.random() - 1)
    delay = max(0.0, base + jitter)
    if max_seconds is not None:
        delay = min(delay, max(0.0, max_seconds))
    if delay > 0:
        time.sleep(delay)


def _ingest_akshare_frame(
    df: pd.DataFrame,
    code: str,
    rows_by_date: dict[date, dict[str, Any]],
) -> int:
    """写入成交额行，返回新增条数。空表 / 无有效 amount 返回 0。"""
    if df is None or df.empty:
        return 0
    date_col = "日期" if "日期" in df.columns else "date"
    amount_col = "成交额" if "成交额" in df.columns else "amount"
    before = len(rows_by_date)
    for _, series in df.iterrows():
        trade_date = pd.Timestamp(series[date_col]).date()
        amount = series[amount_col]
        if pd.isna(amount):
            continue
        rows_by_date[trade_date] = {
            "etf_code": code,
            "trade_date": trade_date,
            "amount": float(amount),
            "amount_source": AMOUNT_SOURCE,
        }
    return len(rows_by_date) - before


def _try_akshare_window(
    *,
    fetch_fn: Callable[..., pd.DataFrame],
    code: str,
    win_start: date,
    win_end: date,
    rows_by_date: dict[date, dict[str, Any]],
    akshare_proven: bool,
    give_up_deadline: float,
) -> tuple[bool, str | None]:
    """尝试东财拉取本窗；返回 (ok, last_error)。

    - 空 DataFrame / 无有效行不当成功（否则会跳过 BaoStock 并误报）
    - 尚未证明东财可用：至少试 1 次，之后在 give_up_deadline 前持续重试
    - 已证明可用：最多 4 次（与历史行为一致）
    """
    last_error: str | None = None
    attempt = 0
    while True:
        try:
            df = fetch_fn(
                symbol=code,
                start_date=win_start.strftime("%Y%m%d"),
                end_date=win_end.strftime("%Y%m%d"),
            )
            ingested = _ingest_akshare_frame(df, code, rows_by_date)
            if ingested > 0:
                return True, None
            # 空响应：不重试（非瞬时网络错误），留给 BaoStock
            return False, "akshare=empty_result"
        except Exception as exc:  # noqa: BLE001 — 窗口级失败需续跑
            last_error = f"{type(exc).__name__}: {exc}"
            attempt += 1
            now = time.monotonic()
            if not akshare_proven and now >= give_up_deadline:
                return False, last_error
            if akshare_proven and attempt >= 4:
                return False, last_error
            if akshare_proven:
                _sleep_backoff(attempt - 1)
                continue
            remaining = give_up_deadline - now
            if remaining <= 0:
                return False, last_error
            _sleep_backoff(attempt - 1, max_seconds=remaining)


def _apply_window_coverage_gate(
    *,
    ok: bool,
    last_error: str | None,
    win_start: date,
    win_end: date,
    rows_by_date: dict[date, dict[str, Any]],
    expected_dates: set[date] | None,
    min_ratio: float,
) -> tuple[bool, str | None]:
    """按库内待补日期核对窗口覆盖；无待补日期时本窗视为成功。"""
    if expected_dates is None:
        return ok, last_error
    win_expected = {d for d in expected_dates if win_start <= d <= win_end}
    if not win_expected:
        # 本窗无可补主行情行：不因「有一行」语义误伤，也不要求拉到数据
        return True, None
    covered = sum(1 for d in win_expected if d in rows_by_date)
    ratio = covered / len(win_expected)
    if ratio >= min_ratio:
        return True, None
    cov_err = f"window_coverage={covered}/{len(win_expected)}"
    if last_error:
        return False, f"{last_error}; {cov_err}"
    return False, cov_err


def fetch_etf_amount_hist(
    etf_code: str,
    start: date,
    end: date,
    *,
    sleep_between_windows: float = 0.35,
    fetch_fn: Callable[..., pd.DataFrame] | None = None,
    baostock_fetch_fn: Callable[..., list[dict[str, Any]]] | None = None,
    enable_baostock_fallback: bool = True,
    akshare_give_up_seconds: float = AKSHARE_GIVE_UP_SECONDS,
    akshare_give_up: AkshareGiveUpState | None = None,
    expected_dates: set[date] | None = None,
    window_coverage_min_ratio: float = WINDOW_COVERAGE_MIN_RATIO,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """分段拉取 ETF 日成交额；返回 (rows, window_failures)。

    rows 字段：etf_code, trade_date, amount, amount_source
    单位：元。优先东财/AKShare；熔断预算默认 ``akshare_give_up_seconds``（5min）。
    传入共享的 ``akshare_give_up`` 时，跨 ETF 复用同一 deadline / skip / proven，
    此时 ``akshare_give_up_seconds`` 不生效；避免东财持续不可用时每只 ETF 重新空等。

    ``expected_dates`` 为库内待补（有主行情且 amount 空）日期时，窗口成功条件改为
    覆盖率 ≥ ``window_coverage_min_ratio``，避免全年窗仅 1 行仍判成功。
    未传入时保持「双源合计至少一行」语义（便于单测）。
    """
    ensure_requests_user_agent()
    code = to_six_digit_code(etf_code)
    if fetch_fn is None:
        import akshare as ak

        def _default_fetch(*, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
            return ak.fund_etf_hist_em(
                symbol=symbol,
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )

        fetch_fn = _default_fetch

    rows_by_date: dict[date, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    state = akshare_give_up or new_akshare_give_up_state(
        give_up_seconds=akshare_give_up_seconds
    )

    for win_start, win_end in iter_date_windows(start, end):
        last_error: str | None = None
        ak_ok = False

        if not state.skip:
            ak_ok, last_error = _try_akshare_window(
                fetch_fn=fetch_fn,
                code=code,
                win_start=win_start,
                win_end=win_end,
                rows_by_date=rows_by_date,
                akshare_proven=state.proven,
                give_up_deadline=state.deadline,
            )
            # 空响应非宕机，不计入 consecutive_failures（防上市前空窗误熔断）
            _mark_akshare_window_result(
                state,
                ak_ok=ak_ok,
                count_failure=last_error != "akshare=empty_result",
            )

        ok = ak_ok
        if enable_baostock_fallback:
            try:
                if baostock_fetch_fn is None:
                    from scheduled_tasks.etf.baostock_client import fetch_etf_amount_window

                    bs_rows = fetch_etf_amount_window(code, win_start, win_end)
                else:
                    bs_rows = baostock_fetch_fn(code, win_start, win_end)
                if bs_rows:
                    for row in bs_rows:
                        # 东财已写入的日期不覆盖
                        rows_by_date.setdefault(row["trade_date"], row)
                    ok = True
                    if not ak_ok:
                        last_error = None
                elif not ak_ok:
                    empty_err = "baostock_fallback=empty_result"
                    last_error = (
                        f"{last_error}; {empty_err}" if last_error else empty_err
                    )
                    # 熔断后 BaoStock 无历史时，仍单次回试东财（半开探测）
                    if state.skip:
                        try:
                            df = fetch_fn(
                                symbol=code,
                                start_date=win_start.strftime("%Y%m%d"),
                                end_date=win_end.strftime("%Y%m%d"),
                            )
                            if _ingest_akshare_frame(df, code, rows_by_date) > 0:
                                ok = True
                                last_error = None
                                _mark_akshare_window_result(state, ak_ok=True)
                        except Exception as retry_exc:  # noqa: BLE001
                            last_error = (
                                f"{last_error}; akshare_retry="
                                f"{type(retry_exc).__name__}: {retry_exc}"
                            )
            except Exception as exc:  # noqa: BLE001
                if not ak_ok:
                    fallback_err = f"{type(exc).__name__}: {exc}"
                    last_error = (
                        f"{last_error}; baostock_fallback={fallback_err}"
                        if last_error
                        else f"baostock_fallback={fallback_err}"
                    )

        ok, last_error = _apply_window_coverage_gate(
            ok=ok,
            last_error=last_error,
            win_start=win_start,
            win_end=win_end,
            rows_by_date=rows_by_date,
            expected_dates=expected_dates,
            min_ratio=window_coverage_min_ratio,
        )

        if not ok:
            failures.append(
                {
                    "etf_code": code,
                    "window_start": win_start.isoformat(),
                    "window_end": win_end.isoformat(),
                    "error": last_error or "unknown",
                }
            )
        if sleep_between_windows > 0:
            time.sleep(sleep_between_windows)

    rows = [rows_by_date[d] for d in sorted(rows_by_date)]
    return rows, failures


def optional_amount_sanity_sample(
    rows: Sequence[dict[str, Any]],
    ohlcv_rows: Sequence[dict[str, Any]],
    *,
    relative_tol: float = 0.15,
) -> dict[str, Any]:
    """可选抽检 amount ≈ close × volume × 100（volume 为手）。"""
    by_date = {r["trade_date"]: r for r in ohlcv_rows}
    checked = 0
    passed = 0
    for row in rows:
        ref = by_date.get(row["trade_date"])
        if not ref:
            continue
        close = ref.get("close")
        volume = ref.get("volume")
        if close is None or volume is None:
            continue
        expected = float(close) * float(volume) * 100.0
        if expected == 0:
            continue
        checked += 1
        rel = abs(float(row["amount"]) - expected) / abs(expected)
        if rel <= relative_tol:
            passed += 1
    return {
        "checked": checked,
        "passed": passed,
        "pass_rate": (passed / checked) if checked else None,
        "tolerance": relative_tol,
    }
