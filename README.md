# scheduled-tasks

GitHub Actions 定时任务：将 **Yahoo Finance（yfinance）** ETF 日 K、以及 **红色火箭** 指数估值同步到 Supabase PostgreSQL；并维护共享行情表的 **RLS**（`cockpit_apply_*` 函数仍由历史 migration 提供）。

> 说明：ETF **价格**主源为 yfinance；补缺 / 估值 / 行业权重由红色火箭写入。汇率同步与 `fx_rates` 表已下线。驾驶舱用户账本 12 表已下线。不再使用 BaoStock / AKShare；`etf_daily` 不维护成交额；**无交易日历表**。定时闹钟在数据库里，到点叫醒对应的 GitHub 任务并写上上海日期；带日期的任务才用公开 HTTP 判断这一天是否为交易日（休市跳过；查询失败则照跑并 Bark 警告），见 [doc/ashare-trading-day-gate.md](./doc/ashare-trading-day-gate.md)。

本仓库独立于 `stock-view` / `stock-charts`。旧指数日线表 `index_daily_prices` 已删除；`index_daily_metrics` 存收盘/PE/PB（Phase 1 主跑沪深 300）。红色火箭 job `sync_hongsehuojian_fill_validate` 可刷新 `index_valuation` 与 `index_daily_metrics` PE/PB。表结构见 [doc/supabase-schema.md](./doc/supabase-schema.md)。

## 当前范围

### ETF 日 K

- 从 `etf_pool` 读取当前池（排除黑名单后断言 20 只）。
- 写入 `etf_daily`：不复权 OHLCV + 前/后复权 OHLC + `price_source='yfinance'`；已官网纠偏（`sse`/`szse`）的行不覆盖。
- 写入 `sync_runs`（`job_name=sync_etf_kline_yfinance`）。
- 模式：`full` / `incremental` / `adj_check`。
- 闹钟：工作日北京 09:40 / 10:40 补前一交易日收盘；18:10 / 18:30 / 19:00 试写当日日 K，沪深300ETF 当天收盘已在库里就不再叫。这两档带日期，休市跳过。周日 10:00 / 11:00 做复权巡检，当天已经成功跑过就不再叫。

### 指数估值（红色火箭）

- 数据源：华夏基金「红色火箭」站内 `fundex-quote` JSON（**非官方**）。
- 闹钟：工作日北京 19:15 / 20:15 / 21:15。沪深300估值快照已经是当天就不再叫。带日期，休市跳过。
- Phase 1 默认：`510300` / `000300.SH`；`valuation-only`。
- 写入：
  - `index_valuation`：当日 PE + 5y/10y 均值快照
  - `index_daily_metrics`：PE/PB 日序列（`valuation_source='hongsehuojian'`）
- 本地入口：`python -m scheduled_tasks.jobs.sync_hongsehuojian_fill_validate --mode=valuation-only --etf-code=510300 --index-code=000300.SH`

### 红色火箭补缺 / 校验（手动）

- 默认标的：`512170`（医疗 ETF）+ `399989.SZ`（中证医疗）。
- ETF 补缺、行业权重刷新见 [doc/hongsehuojian-fill-validate.md](doc/hongsehuojian-fill-validate.md)。

### 指数收盘

- 中证官网 `index-perf`：中证500 `000905.SH`、中证红利 `000922.SH`、中证医疗 `399989.SZ`、全指消费 `000990.SH`、内地消费 `000942.SH`、中证传媒 `399971.SZ`、全指信息 `000993.SH`、证券公司 `399975.SZ`、中证红利低波动100 `930955.CSI`。
- 新浪离岸日线 `fx_susdcnh`：美元兑人民币 `USDCNH.OTH`，收盘按 1 美元计。`indices.code` 用已有的 `OTH` 后缀。
- 只写 `index_daily_metrics.close` 与 `price_source`（`csindex` / `eastmoney` / `sina`）；PE/PB 传空，不覆盖红色火箭已有估值。
- 入口：`python -m scheduled_tasks.jobs.sync_index_levels`。
- 闹钟在数据库里：工作日北京时间 19:30、20:30、21:30 叫醒 `Sync index levels`，并写上当天日期。这一天的中证500收盘已经在库里就不再叫。手点运行可以不填日期。

### 官网双源校验

- 上交所（ETF）+ 中证（指数）vs 库；默认只比对告警，`--apply-official --yes` 才纠偏；纠偏后主写不覆盖不复权 OHLC，`adj_check` 仍可刷复权列。
- 闹钟：工作日北京 20:00 / 21:00。当天已经成功比对过就不再叫。带日期，休市跳过。
- 入口：`python -m scheduled_tasks.jobs.sync_official_cross_check`（见 [doc/official-cross-check.md](doc/official-cross-check.md)）

### 共享 RLS（历史 cockpit migration）

- 函数与共享表 policy：`src/scheduled_tasks/models/migrations/20260710_cockpit_ledger_and_fx_rates.sql`（历史文件名保留；曾建账本 12 表与 `fx_rates`）
- 下线账本 12 表：`20260820_drop_cockpit_ledger.sql`（跑完 20260710 后必须再跑，避免空表被重建）
- 下线汇率：`20260721_drop_fx_rates.sql`

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

3. 共享 RLS（**已有库必跑**；20260710 仍会建账本表，紧接着 drop）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260710_cockpit_ledger_and_fx_rates.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260820_drop_cockpit_ledger.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260718_etf_pool_authenticated_read.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260718_index_market_anon_read.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260721_add_index_daily_metrics.sql
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

6. **补齐 159915、入池 563360 并回填历史行情**：

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 \
  -f src/scheduled_tasks/models/migrations/20260928_add_etfs_159915_563360.sql
python3 -m scheduled_tasks.jobs.sync_etf_kline_yfinance \
  --mode=full --codes=159915,563360
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
