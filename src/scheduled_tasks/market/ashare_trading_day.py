"""A 股交易日探针：供 GHA schedule（定时触发）闸门使用。

主源：GitHub ``NateScarlet/holiday-cn``（国务院放假安排 JSON）+ 周末规则。
备源：腾讯财经 ``sh000001``（上证综指）日 K 是否含该日。

失败语义：查询异常时 ``should_run=true``（fail-open），``is_trading_day=unknown``。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import certifi

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
USER_AGENT = "scheduled-tasks-ashare-trading-day/1.0"
HOLIDAY_CN_URL = (
    "https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"
)
TENCENT_KLINE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param=sh000001,day,{start},{end},40,"
)
HTTP_TIMEOUT_SEC = 8.0


@dataclass(frozen=True)
class TradingDayCheck:
    """单日交易日判定结果。"""

    cal_date: str
    should_run: bool
    is_trading_day: str  # true | false | unknown
    source: str
    error: str = ""


def shanghai_today(*, now: datetime | None = None) -> date:
    current = now or datetime.now(tz=SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    else:
        current = current.astimezone(SHANGHAI_TZ)
    return current.date()


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _http_get_text(url: str, *, timeout: float = HTTP_TIMEOUT_SEC) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "*/*",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _http_get_json(url: str, *, timeout: float = HTTP_TIMEOUT_SEC) -> Any:
    return json.loads(_http_get_text(url, timeout=timeout))


def fetch_holiday_cn_off_days(year: int) -> set[date]:
    """返回该年 ``isOffDay=true``（放假日）集合。"""
    payload = _http_get_json(HOLIDAY_CN_URL.format(year=year))
    days = payload.get("days")
    if not isinstance(days, list):
        raise ValueError(f"holiday-cn {year}: missing days list")
    off: set[date] = set()
    for item in days:
        if not isinstance(item, dict):
            continue
        if item.get("isOffDay") is not True:
            continue
        raw = item.get("date")
        if not raw:
            continue
        off.add(date.fromisoformat(str(raw)[:10]))
    return off


def is_trading_day_holiday_cn(cal_date: date, *, off_days: set[date]) -> bool:
    """周末休市；国务院放假日休市；调休周六即使 isOffDay=false 也因周末休市。"""
    if cal_date.weekday() >= 5:
        return False
    return cal_date not in off_days


def fetch_tencent_index_trade_dates(start: date, end: date) -> set[date]:
    """拉取上证综指日 K 日期集合（备源）。"""
    url = TENCENT_KLINE_URL.format(start=start.isoformat(), end=end.isoformat())
    payload = _http_get_json(url)
    series = (((payload or {}).get("data") or {}).get("sh000001") or {}).get("day")
    if not isinstance(series, list):
        raise ValueError("tencent kline: missing data.sh000001.day")
    out: set[date] = set()
    for row in series:
        if not row:
            continue
        out.add(date.fromisoformat(str(row[0])[:10]))
    return out


def check_via_tencent(cal_date: date) -> bool | None:
    """备源判定。

    - 日 K 含该日 → 交易日
    - 已返回数据且最大日期 ≥ 查询日、但不含该日 → 休市
    - 否则（未来日 / 空数据）→ None（无法判定）
    """
    start = cal_date - timedelta(days=14)
    end = cal_date + timedelta(days=1)
    trade_dates = fetch_tencent_index_trade_dates(start, end)
    if cal_date in trade_dates:
        return True
    if trade_dates and max(trade_dates) >= cal_date:
        return False
    return None


def check_ashare_trading_day(
    cal_date: date | None = None,
    *,
    now: datetime | None = None,
) -> TradingDayCheck:
    """判定 ``cal_date`` 是否为 A 股交易日。"""
    target = cal_date or shanghai_today(now=now)
    cal_s = target.isoformat()
    errors: list[str] = []

    try:
        off_days = fetch_holiday_cn_off_days(target.year)
        opened = is_trading_day_holiday_cn(target, off_days=off_days)
        return TradingDayCheck(
            cal_date=cal_s,
            should_run=opened,
            is_trading_day="true" if opened else "false",
            source="holiday-cn",
            error="",
        )
    except Exception as exc:  # noqa: BLE001 — 闸门需吞掉并 fail-open
        errors.append(f"holiday-cn: {exc}")

    try:
        opened = check_via_tencent(target)
        if opened is True:
            return TradingDayCheck(
                cal_date=cal_s,
                should_run=True,
                is_trading_day="true",
                source="tencent",
                error="; ".join(errors),
            )
        if opened is False:
            return TradingDayCheck(
                cal_date=cal_s,
                should_run=False,
                is_trading_day="false",
                source="tencent",
                error="; ".join(errors),
            )
        errors.append("tencent: inconclusive")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"tencent: {exc}")

    return TradingDayCheck(
        cal_date=cal_s,
        should_run=True,
        is_trading_day="unknown",
        source="none",
        error="; ".join(errors)[:500],
    )


def _github_output_value(key: str, value: object) -> str:
    """GITHUB_OUTPUT 契约：布尔一律小写 true/false，供 workflow ``== 'true'`` 比较。"""
    if key == "should_run":
        return "true" if bool(value) else "false"
    return str(value).replace("\n", " ").replace("\r", " ")


def write_github_output(result: TradingDayCheck) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in asdict(result).items():
            fh.write(f"{key}={_github_output_value(key, value)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="判定上海时区日历日是否为 A 股交易日")
    parser.add_argument(
        "--date",
        dest="cal_date",
        default=None,
        help="YYYY-MM-DD；默认上海时区今天",
    )
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="写入 GITHUB_OUTPUT（供 Actions 读取）",
    )
    args = parser.parse_args(argv)
    target = date.fromisoformat(args.cal_date) if args.cal_date else None
    result = check_ashare_trading_day(target)
    print(json.dumps(asdict(result), ensure_ascii=False))
    if args.github_output:
        write_github_output(result)
    # 始终 0：休市/未知由输出字段表达，不把 job 标红
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
