# scheduled-tasks

GitHub Actions 定时任务：将 **Yahoo Finance（yfinance）** ETF 日 K 同步到 Supabase PostgreSQL。

> 说明：模块/job 名仍含 `baostock` 为历史命名。GitHub Actions 海外 runner 上 BaoStock
> 会截断、东财/AkShare 会断连，故实际数据源为 yfinance。

本仓库独立于 `stock-view` / `stock-charts`。指数相关表（`indices` 等）**暂不维护**；当前主任务是 ETF 日 K 入库。

## 当前范围

- 从 `etf_pool_snapshots` 读取当前池（排除黑名单后断言 25 只）。
- 写入 `etf_daily`：不复权 OHLCV + 前/后复权 OHLC + `price_source='yfinance'`。
- 写入 `sync_runs` 执行记录（`job_name=sync_etf_kline_baostock`）。
- 模式：`full` / `incremental` / `adj_check`。
- 成功/失败均通过 Bark 推送（`BARK_KEY`）。

> 说明：`sync_runs.index_codes` 为历史命名遗留，语义为「本 run 涉及的标的代码」，ETF job 也会写入 6 位 ETF 代码。

## Required GitHub Configuration

Repository Secrets:

- `DATABASE_URL`：Supabase PostgreSQL 连接串
- `BARK_KEY`：Bark 推送 key（未配置时跳过通知，不阻断主任务）

Do not print secret values in logs, documents, issue comments, or chat.

## Supabase Setup

1. 新建库：在 SQL Editor 执行 `src/scheduled_tasks/models/schema.sql`。
2. 已有库（含旧 `etf_grid_*` 表名）：执行幂等迁移

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260709_etf_rename_and_adj_columns.sql
```

3. 将 PostgreSQL 连接串写入 GitHub Secret `DATABASE_URL`。
4. 配置 `BARK_KEY`。
5. 手动跑一次 `同步 ETF 日 K 到 Supabase`，`mode=full` 补历史。

## Local Usage

安装依赖：

```bash
python -m pip install -e ".[dev]"
```

全量同步：

```bash
python -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=full
```

日更（近窗，默认 lookback=5）：

```bash
python -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=incremental
```

周日除权检测：

```bash
python -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=adj_check
```

单只/子集（跳过 25 只断言）：

```bash
python -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=incremental --codes=510300,159915
```

运行测试：

```bash
pytest
```

## Cron

| 时间（北京）                 | 模式          |
| ---------------------------- | ------------- |
| 工作日 18:10 / 18:30 / 19:00 | `incremental` |
| 周日 10:00                   | `adj_check`   |

## Verification Queries

最近同步记录：

```sql
select id, job_name, status, started_at, finished_at, success_count, failure_count, meta
from sync_runs
order by started_at desc
limit 10;
```

各 ETF 最新交易日：

```sql
select etf_code, max(trade_date) as latest_trade_date
from etf_daily
group by etf_code
order by etf_code;
```

主行情尚未被 BaoStock 覆盖的行：

```sql
select etf_code, count(*)
from etf_daily
where price_source is distinct from 'yfinance'
group by etf_code
order by etf_code;
```
