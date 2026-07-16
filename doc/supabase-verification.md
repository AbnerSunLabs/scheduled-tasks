# Supabase Verification

Use Supabase Studio as the primary online verification surface.

## Setup

1. Create a Supabase project.
2. Open SQL Editor.
3. **新库**：执行 `src/scheduled_tasks/models/schema.sql`。
4. **已有库（含 `etf_grid_*`）**：执行幂等迁移

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260709_etf_rename_and_adj_columns.sql
```

5. **已有库且 live 仍为 `etf_pool_snapshots`**：在受控窗口、获得明确授权后**单独**执行 rename（不得与 enrichment migration 或回填同批）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260715_rename_etf_pool_snapshots_to_etf_pool.sql
```

只读验证（失败则停止后续 enrichment / 国内补数 Task 1–5）：

```sql
select to_regclass('public.etf_pool'), to_regclass('public.etf_pool_snapshots');
-- 期望：第一列为 public.etf_pool，第二列为 null
select count(*) from etf_pool;
```

GitHub Actions：workflow `应用驾驶舱 Migration`，勾选 `apply_rename_migration` 后手动触发。

6. **驾驶舱账本 12 表 + 共享表 RLS**（新库与已有库均需；`schema.sql` 不含账本表与 RLS）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260710_cockpit_ledger_and_fx_rates.sql
```

7. **成交额列 + `trade_calendar`**（幂等；新库在 `schema.sql` 已有表结构后仍需本 migration，以补 `trade_calendar` RLS）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260715_etf_daily_amount_enrichment_and_trade_calendar.sql
```

8. **表/列中文注释**（幂等；Dashboard 列 Description 可见）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260716_add_chinese_comments.sql
```

9. Confirm these tables exist:
   - `indices`（**暂不维护**）
   - `index_daily_prices`（**暂不维护**）
   - `index_daily_valuations`（**暂不维护**）
   - `index_industry_weights`（**暂不维护**）
   - `sync_runs`（含 `meta jsonb`）
   - `etf_pool`
   - `etf_daily`（复权 8 列 + `price_source` + `amount_source` + `amount_updated_at`）
   - `etf_valuation_snapshots`（本 job 不写）
   - `fx_rates`（`rate_date` + 货币对 PK；Frankfurter）
   - `trade_calendar`（`market` + `cal_date` PK；本期仅 `CN`）
   - 账本 12 表（DDL only；见 `20260710_cockpit_ledger_and_fx_rates.sql`）
10. Confirm these views exist（随指数基表停更而过期）:

- `index_latest_snapshot`
- `index_detail_snapshot`

11. Confirm 旧表名 `etf_grid_*` 与 `etf_pool_snapshots` **不存在**；主键约束名为 `etf_*_pkey` / `etf_pool_pkey`。
12. Confirm 索引存在：`etf_daily_trade_date_idx`、`etf_pool_snapshot_date_idx`、`fx_rates_rate_date_idx`、`trade_calendar_cal_date_idx`。
13. Confirm 中文注释已挂上（按列名抽查）：

```sql
select obj_description('public.etf_daily'::regclass);
select col_description(
  'public.etf_daily'::regclass,
  (select attnum from pg_attribute
   where attrelid = 'public.etf_daily'::regclass and attname = 'amount_source')
);
```

## GitHub Configuration

Required Secrets:

- `DATABASE_URL`（可直接粘贴 Supabase Dashboard URI；运行时清洗见 README Local Usage）
- `BARK_KEY`（未配置时通知 step 跳过）

## Manual Workflow Check

1. Open GitHub Actions in the `scheduled-tasks` repository.
2. Run `同步 ETF 日 K 到 Supabase` manually with `mode=full`（首跑）。
3. Optionally pass `codes` for a small smoke test.
4. Confirm the workflow log reports success and Bark notification arrives.
5. Do not copy secret values from logs or local env files into issues or chat.
6. 汇率：手动跑 `同步汇率到 Supabase`（`mode=full` 补历史）。
7. 国内 Hermes（见 [hermes-domestic-cron.md](./hermes-domestic-cron.md)）：
   - `sync_etf_enrich_akshare`（补 `amount` / `amount_source` / `amount_updated_at`）
   - `sync_trade_calendar_baostock`（upsert `trade_calendar(market='CN')`）

## SQL Checks

```sql
select id, job_name, status, started_at, finished_at, success_count, failure_count, meta
from sync_runs
where job_name in (
  'sync_etf_kline_yfinance',
  'sync_etf_kline_baostock',  -- 重命名前的历史 run
  'sync_etf_enrich_akshare',
  'sync_fx_rates_frankfurter',
  'sync_trade_calendar_baostock'
)
order by started_at desc
limit 20;
```

```sql
select etf_code, max(trade_date) as latest_trade_date, min(trade_date) as first_trade_date
from etf_daily
group by etf_code
order by etf_code;
```

```sql
select etf_code, trade_date, close, close_qfq, close_hfq, volume, price_source,
       amount, amount_source, amount_updated_at
from etf_daily
where etf_code = '510300'
order by trade_date desc
limit 10;
```

```sql
-- yfinance full/incremental 后：价格侧应接近全历史；price_source 多为 yfinance；复权列非空
-- 成交额统计（akshare/baostock）需在国内 sync_etf_enrich_akshare 跑完后再看；仅跑海外 workflow 时后两行可为 0
select
  count(*) as rows,
  count(*) filter (where price_source = 'yfinance') as yfinance_rows,
  count(*) filter (where close_qfq is null or close_hfq is null) as missing_adj,
  count(*) filter (where amount_source = 'akshare') as akshare_amount_rows,
  count(*) filter (where amount_source = 'baostock') as baostock_amount_rows,
  min(trade_date) as first_date,
  max(trade_date) as last_date
from etf_daily;
```

```sql
select count(*) as pool_size
from etf_pool
where etf_code not in ('512660', '159992');
```

```sql
select market, count(*) as rows, min(cal_date), max(cal_date)
from trade_calendar
group by market;
-- 期望：仅 CN 一行汇总
```

```sql
select rate_date, from_currency, to_currency, rate, source
from fx_rates
order by rate_date desc, from_currency, to_currency
limit 12;
```
