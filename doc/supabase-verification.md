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
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260718_etf_pool_authenticated_read.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260718_index_market_anon_read.sql
```

只读验证：`etf_pool` 已 `ENABLE ROW LEVEL SECURITY`，且存在 `etf_pool_select_authenticated`；`authenticated` 有 `SELECT`。

7. **指数估值表改名**（旧库；幂等，单独执行）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260719_rename_etf_valuation_to_index_valuation.sql
```

只读验证：`index_valuation` 存在且行数与迁移前一致，`etf_valuation` 不存在；主键约束为 `index_valuation_pkey`，公开读 policy 为 `index_valuation_select_anon` / `index_valuation_select_authenticated`。

8. **清理废弃列/表**（旧库；可重复执行）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260717_drop_etf_daily_amount_columns.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260722_drop_etf_daily_idle_columns.sql
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260717_drop_trade_calendar.sql
```

9. **表/列中文注释**（幂等；Dashboard 列 Description 可见）：

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/migrations/20260716_add_chinese_comments.sql
```

10. Confirm these tables exist:

- `indices`（红色火箭可 ensure）
- `index_industry_weights`（红色火箭主写）
- `index_daily_metrics`（红色火箭主写；`20260721_add_index_daily_metrics.sql`）
- `sync_runs`（含 `meta jsonb`）
- `etf_pool`
- `etf_daily`（复权 8 列 + `price_source`；无成交额列 / 无闲置净值侧列）
- `index_valuation`（红色火箭可写）
- 账本 12 表（DDL only；见 `20260710_cockpit_ledger_and_fx_rates.sql`）
- 确认 **不存在** `fx_rates`（`20260721_drop_fx_rates.sql`）
- 确认 **不存在** `index_daily_prices`（`20260718_drop_index_daily_prices.sql`）
- 确认 **不存在** `index_daily_valuations`（已由 `20260717_drop_index_daily_valuations.sql` 删除）
- 确认 **不存在** `trade_calendar`（`20260717_drop_trade_calendar.sql`）
- 确认 `etf_daily` **无** `amount` / `amount_source` / `amount_updated_at`（`20260717_drop_etf_daily_amount_columns.sql`）
- 确认 `etf_daily` **无** `nav` / `premium_rate` / `fund_size` / `listing_days` / `bid_price` / `ask_price`（`20260722_drop_etf_daily_idle_columns.sql`）
- 确认 **不存在** `etf_valuation_snapshots` / `etf_valuation` / `portfolio_snapshots`（前两者已依次迁移为 `index_valuation`）

11. Confirm these views exist（估值列改挂 `index_valuation`）:

- `index_latest_snapshot`
- `index_detail_snapshot`

12. Confirm 旧表名 `etf_grid_*` 与 `etf_pool_snapshots` **不存在**；主键约束名为 `etf_*_pkey` / `etf_pool_pkey` / `index_valuation_pkey` / `portfolio_pkey`。
13. Confirm 索引存在：`etf_daily_trade_date_idx`、`etf_pool_snapshot_date_idx`、`idx_index_daily_metrics_trade_date`。
14. Confirm 中文注释已挂上（按列名抽查）：

```sql
select obj_description('public.etf_daily'::regclass);
select col_description(
  'public.etf_daily'::regclass,
  (select attnum from pg_attribute
   where attrelid = 'public.etf_daily'::regclass and attname = 'price_source')
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
6. 指数估值：手动跑 `同步指数估值到 Supabase`（默认沪深 300 / valuation-only）。
7. 红色火箭（可选）：本地跑 `python -m scheduled_tasks.jobs.sync_hongsehuojian_fill_validate`。
8. 官网校验（可选）：`python -m scheduled_tasks.jobs.sync_official_cross_check`（默认只比对）。

## SQL Checks

```sql
select id, job_name, status, started_at, finished_at, success_count, failure_count, meta
from sync_runs
where job_name in (
  'sync_etf_kline_yfinance',
  'sync_etf_kline_baostock',  -- 重命名前的历史 run
  'sync_fx_rates_frankfurter',
  'sync_hongsehuojian_fill_validate',
  'sync_official_cross_check'
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
select etf_code, trade_date, close, close_qfq, close_hfq, volume, price_source
from etf_daily
where etf_code = '510300'
order by trade_date desc
limit 10;
```

```sql
-- yfinance full/incremental 后：价格侧应接近全历史；price_source 多为 yfinance；复权列非空
  min(trade_date) as first_date,
  max(trade_date) as last_date
from etf_daily;
```

```sql
select count(*) as pool_size
from etf_pool
where etf_code not in ('512660', '159992');
-- 期望：18（排除历史黑名单后与 EXPECTED_POOL_SIZE 一致）
```

```sql
-- fx_rates 已下线
select to_regclass('public.fx_rates');  -- 期望 null
```

```sql
select index_code, trade_date, close, pe_ttm, pb, valuation_source
from index_daily_metrics
where index_code = '000300.SH'
order by trade_date desc
limit 10;
```
