# scheduled-tasks

GitHub Actions 定时任务：将 **Yahoo Finance（yfinance）** ETF 日 K、以及 **红色火箭** 指数估值同步到 Supabase PostgreSQL；并维护 ETF 投资驾驶舱所需的**用户账本 DDL / RLS**（业务数据仍由 `stock-charts` UI 写入）。

> 说明：ETF **价格**主源为 yfinance；补缺 / 估值 / 行业权重由红色火箭写入。汇率同步与 `fx_rates` 表已下线。不再使用 BaoStock / AKShare；`etf_daily` 不维护成交额；无交易日历表。

本仓库独立于 `stock-view` / `stock-charts`。旧指数日线表 `index_daily_prices` 已删除；`index_daily_metrics` 存收盘/PE/PB（Phase 1 主跑沪深 300）。红色火箭 job `sync_hongsehuojian_fill_validate` 可刷新 `index_valuation` 与 `index_daily_metrics` PE/PB。表结构见 [doc/supabase-schema.md](./doc/supabase-schema.md)。

## 当前范围

### ETF 日 K

- 从 `etf_pool` 读取当前池（排除黑名单后断言 18 只）。
- 写入 `etf_daily`：不复权 OHLCV + 前/后复权 OHLC + `price_source='yfinance'`。
- 写入 `sync_runs`（`job_name=sync_etf_kline_yfinance`）。
- 模式：`full` / `incremental` / `adj_check`。
- GHA：`同步 ETF 日 K 到 Supabase`（工作日盘后）。

### 指数估值（红色火箭）

- 数据源：华夏基金「红色火箭」站内 `fundex-quote` JSON（**非官方**）。
- GHA：`同步指数估值到 Supabase`（工作日北京约 19:15）。
- Phase 1 默认：`510300` / `000300.SH`；`valuation-only`。
- 写入：
  - `index_valuation`：当日 PE + 5y/10y 均值快照
  - `index_daily_metrics`：PE/PB 日序列（`valuation_source='hongsehuojian'`）
- 本地入口：`python -m scheduled_tasks.jobs.sync_hongsehuojian_fill_validate --mode=valuation-only --etf-code=510300 --index-code=000300.SH`

### 红色火箭补缺 / 校验（手动）

- 默认标的：`512170`（医疗 ETF）+ `399989.SZ`（中证医疗）。
- ETF 补缺、行业权重刷新见 [doc/hongsehuojian-fill-validate.md](doc/hongsehuojian-fill-validate.md)。

### 官网双源校验

- 上交所（ETF）+ 中证（指数）vs 库；默认只比对告警，`--apply-official --yes` 才纠偏。
- 入口：`python -m scheduled_tasks.jobs.sync_official_cross_check`（见 [doc/official-cross-check.md](doc/official-cross-check.md)）

### 驾驶舱账本 DDL

- Migration：`src/scheduled_tasks/models/migrations/20260710_cockpit_ledger_and_fx_rates.sql`（历史文件名保留；账本 12 表 + 曾含 `fx_rates`）
- 下线汇率：`20260721_drop_fx_rates.sql`
- **不**在本仓库写入账本业务行。

成功/失败均通过 **Bark** 推送（`BARK_KEY`）；无邮箱通知 step。

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

3. 驾驶舱账本 + RLS（**已有库必跑**）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260710_cockpit_ledger_and_fx_rates.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260718_etf_pool_authenticated_read.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260718_index_market_anon_read.sql
```

4. **下线汇率表**（须单独授权）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260721_drop_fx_rates.sql
```

GitHub Actions：`应用驾驶舱 Migration` 勾选 `apply_drop_fx_rates_migration`。

5. **ETF 池表更名** / **指数估值表更名**：见既有 workflow 勾选项与下方历史步骤。

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260715_rename_etf_pool_snapshots_to_etf_pool.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260719_rename_etf_valuation_to_index_valuation.sql
```

## 本地运行

```bash
python3 -m pip install -e ".[dev]"
unset DATABASE_URL   # 如需强制用 .env

# ETF 日 K
python3 -m scheduled_tasks.jobs.sync_etf_kline_yfinance --mode=incremental

# 指数估值（沪深 300）
python3 -m scheduled_tasks.jobs.sync_hongsehuojian_fill_validate \
  --mode=valuation-only --etf-code=510300 --index-code=000300.SH

# 官网校验
python3 -m scheduled_tasks.jobs.sync_official_cross_check --from-pool
```

## 只读验证

```sql
select trade_date, current_pe_ttm from index_valuation where tracking_index_code = '000300.SH';
select max(trade_date), count(*) filter (where pe_ttm is not null)
from index_daily_metrics where index_code = '000300.SH';
select to_regclass('public.fx_rates');  -- 期望 null
```
