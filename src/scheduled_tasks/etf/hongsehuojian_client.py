"""红色火箭（hongsehuojian.com）行情 client — 直调 fundex-quote JSON API。

非官方接口，可能改版；本模块供 fill/validate job 使用，不爬页面。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any, TypeVar

import certifi

T = TypeVar("T")

DEFAULT_BASE_URL = "https://www.hongsehuojian.com"
PRICE_SOURCE = "hongsehuojian"
VALUATION_SOURCE = "hongsehuojian"

# 红色火箭 /fundex-quote/index/valuation 的 timeInterval
VALUATION_INTERVAL_MAX = "max"  # 上市以来全量
VALUATION_INTERVAL_RECENT = "last_10_years"  # 增量默认窗口
VALUATION_INTERVAL_5Y = "last_5_years"
VALUATION_INTERVAL_10Y = "last_10_years"

# kline adjust: 0 不复权 / 1 前复权 / 2 后复权
ADJUST_NONE = "0"
ADJUST_QFQ = "1"
ADJUST_HFQ = "2"

DEFAULT_PAGE_COUNT = 2000
USER_AGENT = "scheduled-tasks-hongsehuojian/1.0"


def to_security_code(code: str, *, kind: str) -> str:
    """库内代码 → 红色火箭 securityCode。

    kind: ``etf``（六位数字）或 ``index``（``*.SH|SZ|CSI|HI|NASDAQ|OTH``）。
    """
    raw = code.strip().upper()
    if kind == "etf":
        if len(raw) != 6 or not raw.isdigit():
            raise ValueError(f"invalid etf_code: {code}")
        if raw.startswith("5"):
            return f"{raw}.SH"
        if raw.startswith("1"):
            return f"{raw}.SZ"
        raise ValueError(f"unsupported etf exchange prefix: {code}")
    if kind == "index":
        if raw.count(".") != 1:
            raise ValueError(f"invalid index_code: {code}")
        left, suffix = raw.split(".", 1)
        if not left or len(left) > 12 or suffix not in {"SH", "SZ", "CSI", "HI", "NASDAQ", "OTH"}:
            raise ValueError(f"invalid index_code: {code}")
        # 境内/中证数字码；跨境 H 前缀 CSI；港股 HI；美股 NASDAQ/OTH
        if left.isdigit():
            if len(left) != 6:
                raise ValueError(f"invalid index_code: {code}")
        elif not left.isalnum():
            raise ValueError(f"invalid index_code: {code}")
        return f"{left}.{suffix}"
    raise ValueError(f"unsupported kind: {kind}")


def parse_trade_date(value: Any) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


def retry_call(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay_sec: float = 1.5,
) -> T:
    last_error: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as error:  # noqa: BLE001
            last_error = error
            if i + 1 >= attempts:
                break
            time.sleep(base_delay_sec * (i + 1))
    assert last_error is not None
    raise last_error


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _http_get_json(url: str, *, timeout: float = 60.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"hongsehuojian HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"hongsehuojian network error: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("hongsehuojian response is not a JSON object")
    return payload


def _api_get(
    path: str,
    params: dict[str, Any],
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{base_url.rstrip('/')}{path}?{query}"
    payload = retry_call(lambda: _http_get_json(url))
    code = str(payload.get("code", ""))
    if code and code != "200":
        raise RuntimeError(f"hongsehuojian API error code={code} msg={payload.get('msg')}")
    data = payload.get("data")
    if data is None:
        raise RuntimeError(f"hongsehuojian empty data for {path}")
    if not isinstance(data, dict):
        raise RuntimeError(f"hongsehuojian unexpected data type for {path}: {type(data)}")
    return data


def parse_kline_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """解析 ``columns`` + 分号分隔 ``items`` 字符串为行 dict。"""
    columns_raw = data.get("columns")
    items_raw = data.get("items")
    if not isinstance(columns_raw, str) or not columns_raw:
        return []
    columns = columns_raw.split(",")
    if items_raw is None or items_raw == "":
        return []
    if not isinstance(items_raw, str):
        raise RuntimeError(f"unexpected kline items type: {type(items_raw)}")
    rows: list[dict[str, Any]] = []
    for line in items_raw.split(";"):
        if not line:
            continue
        parts = line.split(",")
        if len(parts) < len(columns):
            continue
        rows.append(dict(zip(columns, parts[: len(columns)], strict=False)))
    return rows


def fetch_kline_page(
    security_code: str,
    *,
    period: str = "day",
    begin: date,
    count: int = DEFAULT_PAGE_COUNT,
    adjust: str = ADJUST_NONE,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """拉取以 begin 为终点、向前 |count| 根 K 线的一页。"""
    if count >= 0:
        raise ValueError("count must be negative (API convention: -N means last N bars)")
    data = _api_get(
        "/fundex-quote/line/kline",
        {
            "securityCode": security_code,
            "period": period,
            "count": count,
            "begin": begin.strftime("%Y%m%d"),
            "adjust": adjust,
        },
        base_url=base_url,
    )
    return parse_kline_items(data)


def fetch_kline_history(
    security_code: str,
    *,
    period: str = "day",
    adjust: str = ADJUST_NONE,
    end: date | None = None,
    page_count: int = DEFAULT_PAGE_COUNT,
    max_pages: int = 20,
    max_bars: int | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """分页拉日 K，按 tradeDate 升序去重。

    ``max_bars`` 若给定：只拉一页最近 N 根（增量快路径），不翻页。
    """
    end_date = end or date.today()
    if max_bars is not None:
        if max_bars <= 0:
            raise ValueError("max_bars must be positive")
        page = fetch_kline_page(
            security_code,
            period=period,
            begin=end_date,
            count=-max_bars,
            adjust=adjust,
            base_url=base_url,
        )
        by_date = {parse_trade_date(r["tradeDate"]): r for r in page}
        return [by_date[d] for d in sorted(by_date)]

    by_date: dict[date, dict[str, Any]] = {}
    cursor = end_date
    abs_count = abs(page_count)
    for _ in range(max_pages):
        page = fetch_kline_page(
            security_code,
            period=period,
            begin=cursor,
            count=-abs_count,
            adjust=adjust,
            base_url=base_url,
        )
        if not page:
            break
        for row in page:
            td = parse_trade_date(row["tradeDate"])
            by_date[td] = row
        earliest = min(parse_trade_date(r["tradeDate"]) for r in page)
        if len(page) < abs_count:
            break
        # 下一页：以最早日为终点再向前翻（API 会含该日，调用方去重）
        if earliest >= cursor:
            break
        cursor = earliest
    return [by_date[d] for d in sorted(by_date)]


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def build_etf_daily_rows(
    etf_code: str,
    raw_rows: list[dict[str, Any]],
    qfq_rows: list[dict[str, Any]],
    hfq_rows: list[dict[str, Any]],
    *,
    price_source: str = PRICE_SOURCE,
) -> list[dict[str, Any]]:
    """合并三套 adjust 日 K → etf_daily 行（volume 已为手）。"""
    qfq_by = {parse_trade_date(r["tradeDate"]): r for r in qfq_rows}
    hfq_by = {parse_trade_date(r["tradeDate"]): r for r in hfq_rows}
    out: list[dict[str, Any]] = []
    for raw in raw_rows:
        td = parse_trade_date(raw["tradeDate"])
        qfq = qfq_by.get(td, {})
        hfq = hfq_by.get(td, {})
        close = _num(raw.get("close"))
        if close is None or close <= 0:
            continue
        out.append(
            {
                "etf_code": etf_code,
                "trade_date": td,
                "open": _num(raw.get("open")),
                "high": _num(raw.get("high")),
                "low": _num(raw.get("low")),
                "close": close,
                "volume": _num(raw.get("volume")),
                "open_qfq": _num(qfq.get("open")),
                "high_qfq": _num(qfq.get("high")),
                "low_qfq": _num(qfq.get("low")),
                "close_qfq": _num(qfq.get("close")),
                "open_hfq": _num(hfq.get("open")),
                "high_hfq": _num(hfq.get("high")),
                "low_hfq": _num(hfq.get("low")),
                "close_hfq": _num(hfq.get("close")),
                "price_source": price_source,
            }
        )
    return out


def fetch_etf_daily_bundle(
    etf_code: str,
    *,
    end: date | None = None,
    max_bars: int | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """拉取 ETF 不复权 + 前/后复权日 K 并合并（三路并行）。"""
    from concurrent.futures import ThreadPoolExecutor

    security = to_security_code(etf_code, kind="etf")

    def _one(adjust: str) -> list[dict[str, Any]]:
        return fetch_kline_history(
            security, adjust=adjust, end=end, max_bars=max_bars, base_url=base_url
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_raw = pool.submit(_one, ADJUST_NONE)
        fut_qfq = pool.submit(_one, ADJUST_QFQ)
        fut_hfq = pool.submit(_one, ADJUST_HFQ)
        raw = fut_raw.result()
        qfq = fut_qfq.result()
        hfq = fut_hfq.result()
    return build_etf_daily_rows(etf_code, raw, qfq, hfq)


def fetch_index_daily_prices(
    index_code: str,
    *,
    end: date | None = None,
    max_bars: int | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """拉取指数日收盘，映射为 index_daily_metrics 行片段。"""
    security = to_security_code(index_code, kind="index")
    rows = fetch_kline_history(
        security, adjust=ADJUST_NONE, end=end, max_bars=max_bars, base_url=base_url
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        close = _num(row.get("close"))
        if close is None or close <= 0:
            continue
        out.append(
            {
                "index_code": index_code,
                "trade_date": parse_trade_date(row["tradeDate"]),
                "close": close,
                "pe_ttm": None,
                "pb": None,
                "price_source": PRICE_SOURCE,
                "valuation_source": None,
            }
        )
    return out


def _mean_positive_valuations(items: list[Any]) -> float | None:
    vals: list[float] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = _num(item.get("valuationValue"))
        if value is not None and value > 0:
            vals.append(value)
    if not vals:
        return None
    return sum(vals) / len(vals)


def _fetch_valuation_window(
    security_code: str,
    valuation_type: str,
    time_interval: str,
    *,
    base_url: str,
) -> dict[str, Any]:
    metric = valuation_type.strip().upper()
    if metric not in {"PE", "PB"}:
        raise ValueError(f"unsupported valuation_type: {valuation_type}")
    data = _api_get(
        "/fundex-quote/index/valuation",
        {
            "securityCode": security_code,
            "valuationType": metric,
            "timeInterval": time_interval,
        },
        base_url=base_url,
    )
    items = data.get("items") or []
    if not isinstance(items, list):
        raise RuntimeError("valuation items must be a list")
    return data


def _fetch_pe_window(
    security_code: str,
    time_interval: str,
    *,
    base_url: str,
) -> dict[str, Any]:
    return _fetch_valuation_window(security_code, "PE", time_interval, base_url=base_url)


def fetch_index_valuation_history(
    index_code: str,
    valuation_type: str,
    *,
    time_interval: str = VALUATION_INTERVAL_RECENT,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """拉取指数 PE 或 PB 日序列，映射为 index_daily_metrics 行片段。

    ``time_interval`` 传给红色火箭 ``timeInterval``：``max``=全量，
    ``last_10_years`` / ``last_5_years`` 为滚动窗口。
    """
    metric = valuation_type.strip().upper()
    field = "pe_ttm" if metric == "PE" else "pb"
    if metric not in {"PE", "PB"}:
        raise ValueError(f"unsupported valuation_type: {valuation_type}")
    security = to_security_code(index_code, kind="index")
    data = _fetch_valuation_window(security, metric, time_interval, base_url=base_url)
    out: list[dict[str, Any]] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict):
            continue
        value = _num(item.get("valuationValue"))
        if value is None or value <= 0:
            continue
        row: dict[str, Any] = {
            "index_code": index_code,
            "trade_date": parse_trade_date(item["tradeDate"]),
            "close": None,
            "pe_ttm": None,
            "pb": None,
            "price_source": None,
            "valuation_source": "hongsehuojian",
        }
        row[field] = value
        out.append(row)
    return out


def merge_index_metric_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (index_code, trade_date) 合并 close/PE/PB 片段行。"""
    merged: dict[tuple[str, date], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["index_code"]), row["trade_date"])
        target = merged.setdefault(
            key,
            {
                "index_code": row["index_code"],
                "trade_date": row["trade_date"],
                "close": None,
                "pe_ttm": None,
                "pb": None,
                "price_source": None,
                "valuation_source": None,
            },
        )
        for field_name in ("close", "pe_ttm", "pb", "price_source", "valuation_source"):
            value = row.get(field_name)
            if value is not None:
                target[field_name] = value
    return [merged[key] for key in sorted(merged.keys(), key=lambda item: (item[0], item[1]))]


def fetch_index_daily_metrics_bundle(
    index_code: str,
    *,
    time_interval: str = VALUATION_INTERVAL_RECENT,
    include_prices: bool = True,
    max_price_bars: int | None = None,
    end: date | None = None,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """并行拉取 PE/PB（及可选收盘）历史并合并为日指标行。"""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=3) as pool:
        fut_pe = pool.submit(
            fetch_index_valuation_history,
            index_code,
            "PE",
            time_interval=time_interval,
            base_url=base_url,
        )
        fut_pb = pool.submit(
            fetch_index_valuation_history,
            index_code,
            "PB",
            time_interval=time_interval,
            base_url=base_url,
        )
        fut_px = (
            pool.submit(
                fetch_index_daily_prices,
                index_code,
                end=end,
                max_bars=max_price_bars,
                base_url=base_url,
            )
            if include_prices
            else None
        )
        pe_rows = fut_pe.result()
        pb_rows = fut_pb.result()
        price_rows = fut_px.result() if fut_px is not None else []
    return merge_index_metric_rows([*pe_rows, *pb_rows, *price_rows])


def fetch_index_pe_snapshot(
    index_code: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    """拉取指数 PE 快照：当日 + 近 5 年均值 + 近 10 年均值。

    对应表 ``index_valuation``（不落日估值序列）。5y/10y 两路并行。
    """
    from concurrent.futures import ThreadPoolExecutor

    security = to_security_code(index_code, kind="index")
    with ThreadPoolExecutor(max_workers=2) as pool:
        fut_5y = pool.submit(_fetch_pe_window, security, VALUATION_INTERVAL_5Y, base_url=base_url)
        fut_10y = pool.submit(_fetch_pe_window, security, VALUATION_INTERVAL_10Y, base_url=base_url)
        pe_5y = fut_5y.result()
        pe_10y = fut_10y.result()

    current = _num(pe_5y.get("valuation"))
    if current is None or current <= 0:
        current = _num(pe_10y.get("valuation"))
    if current is None or current <= 0:
        raise RuntimeError(f"missing current PE for {index_code}")

    items_5y = pe_5y.get("items") or []
    items_10y = pe_10y.get("items") or []
    trade_date: date | None = None
    if isinstance(items_5y, list) and items_5y and isinstance(items_5y[0], dict):
        trade_date = parse_trade_date(items_5y[0]["tradeDate"])
    elif isinstance(items_10y, list) and items_10y and isinstance(items_10y[0], dict):
        trade_date = parse_trade_date(items_10y[0]["tradeDate"])
    if trade_date is None:
        trade_date = date.today()

    avg_5y = _mean_positive_valuations(items_5y if isinstance(items_5y, list) else [])
    avg_10y = _mean_positive_valuations(items_10y if isinstance(items_10y, list) else [])
    return {
        "tracking_index_code": index_code,
        "trade_date": trade_date,
        "current_pe_ttm": current,
        "pe_ttm_avg_5y": avg_5y,
        "pe_ttm_avg_10y": avg_10y,
    }


# 红色火箭 industryLevel 参数 → 本库 sw_level
_INDUSTRY_LEVEL_PARAM_TO_SW = {
    2: "sw1",
    3: "sw2",
    4: "sw3",
}


def fetch_index_industry_weights(
    index_code: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
) -> list[dict[str, Any]]:
    """拉取指数「最新」行业权重（sw1/sw2/sw3），写入 ``index_industry_weights``。"""
    from concurrent.futures import ThreadPoolExecutor

    security = to_security_code(index_code, kind="index")

    def _one(level_param: int) -> tuple[str, dict[str, Any]]:
        data = _api_get(
            "/fundex-quote/security/component/industryDistribution",
            {"securityCode": security, "industryLevel": level_param},
            base_url=base_url,
        )
        return _INDUSTRY_LEVEL_PARAM_TO_SW[level_param], data

    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(_one, p) for p in _INDUSTRY_LEVEL_PARAM_TO_SW]
        payloads = [f.result() for f in futs]

    rows: list[dict[str, Any]] = []
    for sw_level, data in payloads:
        result_map = data.get("resultMap") or {}
        if not isinstance(result_map, dict):
            raise RuntimeError(f"industryDistribution resultMap invalid for {index_code}")
        latest_items = result_map.get("最新")
        if not latest_items:
            # 无「最新」时退回 latestDate 对应 report
            latest_date_raw = data.get("latestDate")
            latest_items = []
            if latest_date_raw:
                for bucket in result_map.values():
                    if not isinstance(bucket, list) or not bucket:
                        continue
                    if str(bucket[0].get("report")) == str(latest_date_raw):
                        latest_items = bucket
                        break
        if not isinstance(latest_items, list) or not latest_items:
            raise RuntimeError(f"empty industry weights for {index_code} {sw_level}")

        for item in latest_items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("industryName") or "").strip()
            weight = _num(item.get("weight"))
            report = item.get("report") or data.get("latestDate")
            if not name or weight is None or weight <= 0:
                continue
            # 约束 weight_pct <= 100；偶发浮点可略超则截断到 100
            if weight > 100:
                weight = 100.0
            rows.append(
                {
                    "index_code": index_code,
                    "as_of_date": parse_trade_date(report),
                    "sw_level": sw_level,
                    "industry_name": name,
                    "weight_pct": weight,
                }
            )

    if not rows:
        raise RuntimeError(f"no industry weight rows for {index_code}")
    return rows


def default_history_end() -> date:
    return date.today()


def lookback_start(days: int, *, end: date | None = None) -> date:
    end_date = end or date.today()
    return end_date - timedelta(days=max(days, 0))
