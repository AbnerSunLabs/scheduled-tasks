# scheduled-tasks

GitHub Actions 定时任务：将 **Yahoo Finance（yfinance）** ETF 日 K、以及 **Frankfurter（ECB）** 汇率同步到 Supabase PostgreSQL；并维护 ETF 投资驾驶舱所需的**用户账本 DDL / RLS**（业务数据仍由 `stock-charts` UI 写入）。

> 说明：海外 GitHub Actions runner 上 BaoStock / 东财 AkShare 不可用，故 ETF **价格**主源为 yfinance；成交额由国内 Hermes 的 `sync_etf_enrich_akshare` 补数。

本仓库独立于 `stock-view` / `stock-charts`。指数相关表（`indices` 等）**暂不维护**。

## 当前范围

### ETF 日 K

- 从 `etf_pool` 读取当前池（排除黑名单后断言 25 只）。
- 写入 `etf_daily`：不复权 OHLCV + 前/后复权 OHLC + `price_source='yfinance'`。
- 写入 `sync_runs`（`job_name=sync_etf_kline_yfinance`）。
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

4. **ETF 池表更名**（live 仍为 `etf_pool_snapshots` 时必跑；须单独授权，**不得**与 enrichment migration 或回填同批执行）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260715_rename_etf_pool_snapshots_to_etf_pool.sql
```

部署后只读验证：

```sql
select to_regclass('public.etf_pool'), to_regclass('public.etf_pool_snapshots');
-- 期望：public.etf_pool 存在，public.etf_pool_snapshots 为 null
select count(*) from public.etf_pool;
```

GitHub Actions：`应用驾驶舱 Migration` workflow 中勾选 `apply_rename_migration` 后手动触发；未获明确授权勿对 live 执行。

5. **成交额补数列 + 交易日历**（须 rename 已验证通过后再跑；单独授权，勿与 rename 同批）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260715_etf_daily_amount_enrichment_and_trade_calendar.sql
```

只读验证：`etf_daily.amount_source` / `amount_updated_at` 存在，且 `public.trade_calendar` 存在。

国内 Hermes 调度见 `doc/hermes-domestic-cron.md`（Spike 须国内机复跑通过后再启用生产 cron）。

6. **表/列中文注释**（幂等；对象不存在则跳过；可随时重跑覆盖注释）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260716_add_chinese_comments.sql
```

执行后在 Supabase Table Editor 的列 Description 可见中文说明。

7. 将 PostgreSQL 连接串写入 GitHub Secret `DATABASE_URL`。
8. 配置 `BARK_KEY`。
9. 手动跑一次 `同步 ETF 日 K 到 Supabase`，`mode=full` 补历史。
10. 手动跑一次 `同步汇率到 Supabase`，`mode=full` 补历史汇率。

## Local Usage

安装依赖：

```bash
python3 -m pip install -e ".[dev]"
```

本地环境变量（job 启动前必填）：

```bash
cp .env.example .env
# 编辑 .env：填入 Supabase Postgres 连接串 DATABASE_URL=
# 也可：export DATABASE_URL='postgresql://...'
```

`load_settings()` 会自动读取仓库根目录 `.env`（已存在的环境变量优先，不覆盖）。

`connect()` 会清洗 `DATABASE_URL`：去掉 Prisma 模板常见的 `pgbouncer=true`；对密码做安全解析（支持 Dashboard 百分号编码或含 `[]@/` 等特殊字符）；Supabase host 缺省时自动补 `sslmode=require`；事务池端口 `6543` 自动禁用 prepared statements。可直接粘贴 Supabase Dashboard 连接串。

### ETF

```bash
python3 -m scheduled_tasks.jobs.sync_etf_kline_yfinance --mode=full
python3 -m scheduled_tasks.jobs.sync_etf_kline_yfinance --mode=incremental
python3 -m scheduled_tasks.jobs.sync_etf_kline_yfinance --mode=adj_check
python3 -m scheduled_tasks.jobs.sync_etf_kline_yfinance --mode=incremental --codes=510300,159915
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
