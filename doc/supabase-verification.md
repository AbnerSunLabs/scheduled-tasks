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

6. Confirm these tables exist:
   - `indices`（**暂不维护**）
   - `index_daily_prices`（**暂不维护**）
   - `index_daily_valuations`（**暂不维护**）
   - `index_industry_weights`（**暂不维护**）
   - `sync_runs`（含 `meta jsonb`）
   - `etf_pool`
   - `etf_daily`（含复权 8 列 + `price_source`）
   - `etf_valuation_snapshots`（本 job 不写）
7. Confirm these views exist（随指数基表停更而过期）:
   - `index_latest_snapshot`
   - `index_detail_snapshot`
8. Confirm 旧表名 `etf_grid_*` 与 `etf_pool_snapshots` **不存在**；主键约束名为 `etf_*_pkey` / `etf_pool_pkey`。

## GitHub Configuration

Required Secrets:

- `DATABASE_URL`
- `BARK_KEY`（未配置时通知 step 跳过）

## Manual Workflow Check

1. Open GitHub Actions in the `scheduled-tasks` repository.
2. Run `同步 ETF 日 K 到 Supabase` manually with `mode=full`（首跑）。
3. Optionally pass `codes` for a small smoke test.
4. Confirm the workflow log reports success and Bark notification arrives.
5. Do not copy secret values from logs or local env files into issues or chat.

## SQL Checks

```sql
select id, job_name, status, started_at, finished_at, success_count, failure_count, meta
from sync_runs
where job_name = 'sync_etf_kline_baostock'
order by started_at desc
limit 10;
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
-- full 后应接近全历史；price_source 应为 yfinance；复权列非空
select
  count(*) as rows,
  count(*) filter (where price_source = 'yfinance') as yfinance_rows,
  count(*) filter (where close_qfq is null or close_hfq is null) as missing_adj,
  min(trade_date) as first_date,
  max(trade_date) as last_date
from etf_daily;
```

```sql
select count(*) as pool_size
from etf_pool
where etf_code not in ('512660', '159992');
```
