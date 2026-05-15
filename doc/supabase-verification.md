# Supabase Verification

Use Supabase Studio as the primary online verification surface.

## Setup

1. Create a Supabase project.
2. Open SQL Editor.
3. Run `src/scheduled_tasks/models/schema.sql`.
4. Confirm these tables exist:
   - `indices`
   - `index_daily_prices`
   - `index_daily_valuations`
   - `index_industry_weights`
   - `sync_runs`
5. Confirm these views exist:
   - `index_latest_snapshot`
   - `index_detail_snapshot`

## GitHub Configuration

Variables:

- `INDEX_CODES`

Required Secrets:

- `DATABASE_URL`
- `TUSHARE_TOKEN`
- `DATA_API`

Optional Secrets:

- `DATA_API_SCHEME`
- `TUSHARE_SYNC_PROXY_ENV`

## Manual Workflow Check

1. Open GitHub Actions in the `scheduled-tasks` repository.
2. Run `同步指数数据到 Supabase` manually.
3. Optionally pass one index code in `index_codes` for a small smoke test.
4. Confirm the workflow log reports success and row counts.
5. Do not copy secret values from logs or local env files into issues or chat.

## SQL Checks

```sql
select id, job_name, status, started_at, finished_at, success_count, failure_count
from sync_runs
order by started_at desc
limit 10;
```

```sql
select index_code, max(trade_date) as latest_trade_date
from index_daily_prices
group by index_code
order by index_code;
```

```sql
select *
from index_latest_snapshot
order by display_order;
```
