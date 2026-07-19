# 红色火箭补缺 / 校验

## 目标

用华夏基金「红色火箭」站内行情，对 **指定标的** 做：

1. **补缺**：库中不存在的 `(code, trade_date)` → `INSERT`（ETF 日 K、指数收盘价）
2. **校验**：已有 ETF/指数收盘行只比对，**不 UPDATE**
3. **估值**：写入 `index_valuation`（当日 PE + 近 5 年 / 近 10 年均值），按指数 **upsert 刷新**

## 默认标的

| 类型 | 代码 | 名称 |
| --- | --- | --- |
| ETF | `512170` | 医疗ETF华宝 |
| 指数 | `399989.SZ` | 中证医疗 |

## 为何不用 Crawlee

站点为 SPA，数据来自 `https://www.hongsehuojian.com/fundex-quote/*` JSON。
生产路径与现有 `yfinance` / Frankfurter 一致：**stdlib + certifi HTTP client**，不跑浏览器。

## 主要接口

| 接口 | 用途 |
| --- | --- |
| `GET /fundex-quote/line/kline` | 日 K；`adjust=0/1/2` → 不复权 / 前复权 / 后复权；`items` 为分号分隔字符串 |
| `GET /fundex-quote/security/component/industryDistribution` | 行业权重；`industryLevel=2/3/4` → sw1/sw2/sw3；取 `resultMap.最新` |

非官方接口，无 SLA，改版需改 client。

## 写入表

- `etf_daily`：`price_source='hongsehuojian'`（仅新行；不含成交额）
- `indices`：缺则插入元数据（`ON CONFLICT DO NOTHING`）
- `index_valuation`：`tracking_index_code` 主键，刷新当日 PE / 5y / 10y 均值
- `index_industry_weights`：红色火箭主源，按指数删旧写新（最新一期 sw1/sw2/sw3）
- `sync_runs`：`job_name=sync_hongsehuojian_fill_validate`
- 估值：红色火箭对池内可解析指数刷 `index_valuation`。实测：**境内数字码 + `*.HI`（恒生/恒生科技）有 PE**；`H*.CSI` 半导体/中概互联/机器人、以及 `NDX.NASDAQ` / `SPX.OTH` 接口无估值数据，写不了。

## 本地运行

```bash
python3 -m pip install -e ".[dev]"
# 需 DATABASE_URL（.env 或环境变量）；若 shell 里已有 DATABASE_URL，会优先生效
unset DATABASE_URL   # 如需强制用 .env
# 默认 incremental（近 30 根，较快）
python3 -m scheduled_tasks.jobs.sync_hongsehuojian_fill_validate
# 只刷估值（当日 PE + 5y/10y 均值；不拉 K 线、不刷新行业权重）
python3 -m scheduled_tasks.jobs.sync_hongsehuojian_fill_validate --mode=valuation-only
# 首次补全历史（含行业权重刷新）
python3 -m scheduled_tasks.jobs.sync_hongsehuojian_fill_validate --mode=full
```

摘要：`artifacts/sync_hongsehuojian_fill_validate_summary.json`

`valuation-only` 仅 upsert `index_valuation`（当日 PE + 5y/10y）；**不**拉 K 线、**不**刷新 `index_industry_weights`。

官网交叉校验（上交所 / 中证 vs 库）见 [official-cross-check.md](./official-cross-check.md)，与本 job 互补：本 job 主写补缺，官网 job 只比对/可选纠偏。

## 后续（未做）

- 扩到全 `etf_pool` / 全指数
- GHA / Hermes 定期调度
