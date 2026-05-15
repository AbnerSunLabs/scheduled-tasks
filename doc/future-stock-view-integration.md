# Future stock-view Integration Notes

Phase 1 does not modify `stock-view`.

These notes only record the future integration points after Supabase PostgreSQL
sync is stable.

## Read Targets

- List page can read from `index_latest_snapshot`.
- Detail page can read from base tables:
  - `indices`
  - `index_daily_prices`
  - `index_daily_valuations`
  - `index_industry_weights`
- `index_detail_snapshot` is a lightweight verification view, not a complete
  detail API replacement.

## Existing stock-view Touch Points

Future work can replace runtime TuShare calls behind the existing server data
boundary:

- `src/lib/indices/server/fetch-index-data.ts`
- `src/app/indices/page.tsx`
- `src/app/indices/[code]/page.tsx`

The frontend component contracts should stay compatible with the current index
types where practical.

## Suggested Rollout

1. Keep current JSON snapshot and runtime TuShare code in place.
2. Compare Supabase data against the current snapshot for several trading days.
3. Add a new database read layer in `stock-view`.
4. Switch list page first, then detail page.
5. Keep a rollback path to the existing snapshot/runtime TuShare flow until the
   Supabase data path is proven stable.
