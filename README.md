# scheduled-tasks

GitHub Actions scheduled jobs for syncing TuShare data into Supabase PostgreSQL.

This repository is independent from `stock-view`. The first phase only builds the
database, schema, sync jobs, and verification workflow. It does not change
`stock-view`.

## Phase 1 Scope

- Sync configured index codes from TuShare into Supabase PostgreSQL.
- Store index metadata, daily prices, daily valuation points, industry weights,
  and sync run history.
- Use `src/scheduled_tasks/data/indices/supported-indices.json` as the default
  metadata map for index name, category, and display order.
- Provide SQL views for verification and future `stock-view` integration.
- Keep `INDEX_CODES` in GitHub Repository Variables as comma-separated values.

## Required GitHub Configuration

Repository Variables:

- `INDEX_CODES`: comma-separated TuShare index codes, for example
  `000300.SH,000905.SH,399006.SZ`

Repository Secrets:

- `DATABASE_URL`: Supabase PostgreSQL connection string
- `TUSHARE_TOKEN`: TuShare token
- `DATA_API`: TuShare API gateway

Optional Repository Secrets:

- `DATA_API_SCHEME`
- `TUSHARE_SYNC_PROXY_ENV`

Do not print secret values in logs, documents, issue comments, or chat.

## Supabase Setup

1. Create a Supabase project.
2. Open Supabase Studio.
3. Use SQL Editor to run `src/scheduled_tasks/models/schema.sql`.
4. Copy the standard PostgreSQL connection string into GitHub Secret
   `DATABASE_URL`.
5. Configure the TuShare secrets and `INDEX_CODES`.
6. Run the `同步指数数据到 Supabase` workflow manually.

## Local Usage

Install dependencies:

```bash
python -m pip install -e ".[dev]"
```

Run the schema against Supabase or a local PostgreSQL database:

```bash
psql "$DATABASE_URL" -f src/scheduled_tasks/models/schema.sql
```

Run a manual index sync:

```bash
INDEX_CODES="000300.SH,000905.SH" python -m scheduled_tasks.jobs.sync_indices
```

Run tests:

```bash
pytest
```

## Verification Queries

Recent sync runs:

```sql
select id, job_name, status, started_at, finished_at, success_count, failure_count
from sync_runs
order by started_at desc
limit 10;
```

Latest synced market date by index:

```sql
select index_code, max(trade_date) as latest_trade_date
from index_daily_prices
group by index_code
order by index_code;
```

Snapshot view:

```sql
select *
from index_latest_snapshot
order by display_order;
```
