# scheduled-tasks

GitHub Actions 定时任务：将 **Yahoo Finance（yfinance）** ETF 日 K、以及 **Frankfurter（ECB）** 汇率同步到 Supabase PostgreSQL；并维护 ETF 投资驾驶舱所需的**用户账本 DDL / RLS**（业务数据仍由 `stock-charts` UI 写入）。

> 说明：ETF 模块/job 名仍含 `baostock` 为历史命名。GitHub Actions 海外 runner 上 BaoStock
> 会截断、东财/AkShare 会断连，故实际 ETF 数据源为 yfinance。

本仓库独立于 `stock-view` / `stock-charts`。指数相关表（`indices` 等）**暂不维护**。

## 当前范围

### ETF 日 K

- 从 `etf_pool_snapshots` 读取当前池（排除黑名单后断言 25 只）。
- 写入 `etf_daily`：不复权 OHLCV + 前/后复权 OHLC + `price_source='yfinance'`。
- 写入 `sync_runs`（`job_name=sync_etf_kline_baostock`）。
- 模式：`full` / `incremental` / `adj_check`。

### 汇率

- 数据源：Frankfurter（ECB 日频参考价），无 API Key。
- 写入 `fx_rates`：`USD→CNY`、`USD→HKD`、`HKD→CNY`（由 USD 锚点推导）。
- 写入 `sync_runs`（`job_name=sync_fx_rates_frankfurter`）。
- 模式：`full` / `incremental`。

### 驾驶舱账本 DDL

- Migration：`src/scheduled_tasks/models/migrations/20260710_cockpit_ledger_and_fx_rates.sql`
- 新建用户账本 12 表 + `fx_rates`，启用 RLS（账本按 `user_id`；共享表 `authenticated` 只读）。
- **不**在本仓库写入账本业务行；持仓/成交等由 `stock-charts` 经 Supabase Auth（邮箱 Magic Link）+ RLS 写入。

成功/失败均可通过 Bark 推送（`BARK_KEY`）。

> 说明：`sync_runs.index_codes` 为历史命名遗留，语义为「本 run 涉及的标的代码」。

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

3. 驾驶舱账本 + 汇率表 + RLS（**已有库必跑**）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260710_cockpit_ledger_and_fx_rates.sql
```

4. 将 PostgreSQL 连接串写入 GitHub Secret `DATABASE_URL`。
5. 配置 `BARK_KEY`。
6. 手动跑一次 `同步 ETF 日 K 到 Supabase`，`mode=full` 补历史。
7. 手动跑一次 `同步汇率到 Supabase`，`mode=full` 补历史汇率。

## Local Usage

安装依赖：

```bash
python3 -m pip install -e ".[dev]"
```

### ETF

```bash
python3 -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=full
python3 -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=incremental
python3 -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=adj_check
python3 -m scheduled_tasks.jobs.sync_etf_kline_baostock --mode=incremental --codes=510300,159915
```

### 汇率

```bash
python3 -m scheduled_tasks.jobs.sync_fx_rates_frankfurter --mode=full
python3 -m scheduled_tasks.jobs.sync_fx_rates_frankfurter --mode=incremental
python3 -m scheduled_tasks.jobs.sync_fx_rates_frankfurter --mode=incremental --lookback-days=14
```

运行测试：

```bash
pytest
```

## Cron

| 任务     | 时间（北京）                 | 模式          |
| -------- | ---------------------------- | ------------- |
| ETF 日 K | 工作日 18:10 / 18:30 / 19:00 | `incremental` |
| ETF 日 K | 周日 10:00                   | `adj_check`   |
| 汇率     | 工作日 23:30                 | `incremental` |

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

汇率最新日：

```sql
select from_currency, to_currency, max(rate_date) as latest_rate_date
from fx_rates
group by from_currency, to_currency
order by 1, 2;
```

账本表是否存在：

```sql
select tablename
from pg_tables
where schemaname = 'public'
  and tablename in (
    'portfolio_settings', 'target_allocations', 'etf_instruments', 'positions',
    'trade_records', 'cash_flows', 'cash_accounts', 'rebalance_plans',
    'grid_plans', 'review_entries', 'decision_logs', 'portfolio_snapshots',
    'fx_rates'
  )
order by tablename;
```
