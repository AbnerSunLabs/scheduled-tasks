# Spike：上交所 / 中证官网可机读接口（2026-07-17）

只读探测，不写库。结论供 `sync_official_cross_check` 使用。

## 上交所 ETF（512170）

可用：

```
GET http://yunhq.sse.com.cn:32041/v1/sh1/dayk/512170?select=date,open,high,low,close,volume&begin=-30&end=-1
```

- **必须**在 `select` 中显式包含 `close`；`last` 在日 K 上常为 `null`。
- 仅 **http** 可用；https 会 SSL 失败。
- `volume` 量级偏大（疑似股/手口径与库内不一致），MVP **不比对 volume**。
- 非公开 SLA，可能改版。

## 中证指数（399989）

可用：

```
GET https://www.csindex.com.cn/csindex-home/perf/index-perf?indexCode=399989&startDate=YYYYMMDD&endDate=YYYYMMDD
```

- 日期参数必须为 **`YYYYMMDD`**；传 `YYYY-MM-DD` 时接口仍返回 `code=200` 但 `data=[]`。
- `data[]`：`tradeDate`（常为 `YYYYMMDD` 字符串）, `open`, `high`, `low`, `close`, …
- 字段名 `peg` 数值量级为市盈率（约 20–30），**不是**传统 PEG；client 映射为 `current_pe_ttm`。
- 日期窗口过短或未来区间可能返回空 `data`。

## 不采用

- 上交所付费历史数据包 / LDDS（需签约）。
- AKShare / 东财（非官网权威）。
