-- Drop `_snapshots` suffix from table names:
--   etf_valuation_snapshots → etf_valuation
--   portfolio_snapshots → portfolio
-- Views index_*_snapshot (singular) are unchanged; recreate them to reference etf_valuation.

-- ---------------------------------------------------------------------------
-- etf_valuation_snapshots → etf_valuation
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regclass('public.etf_valuation_snapshots') is not null
     and to_regclass('public.etf_valuation') is not null then
    raise exception
      'both etf_valuation_snapshots and etf_valuation exist; manual migration required';
  end if;

  if to_regclass('public.etf_valuation_snapshots') is not null
     and to_regclass('public.etf_valuation') is null then
    alter table public.etf_valuation_snapshots rename to etf_valuation;
  end if;

  if to_regclass('public.etf_valuation') is null then
    raise exception 'etf_valuation missing after rename step';
  end if;

  if exists (
    select 1 from pg_constraint
    where conname = 'etf_valuation_snapshots_pkey'
      and conrelid = 'public.etf_valuation'::regclass
  ) then
    if exists (
      select 1 from pg_constraint where conname = 'etf_valuation_pkey'
    ) then
      raise exception
        'both etf_valuation_snapshots_pkey and etf_valuation_pkey exist; manual migration required';
    end if;
    alter table public.etf_valuation
      rename constraint etf_valuation_snapshots_pkey to etf_valuation_pkey;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- portfolio_snapshots → portfolio
-- ---------------------------------------------------------------------------
do $$
begin
  if to_regclass('public.portfolio_snapshots') is not null
     and to_regclass('public.portfolio') is not null then
    raise exception
      'both portfolio_snapshots and portfolio exist; manual migration required';
  end if;

  if to_regclass('public.portfolio_snapshots') is not null
     and to_regclass('public.portfolio') is null then
    alter table public.portfolio_snapshots rename to portfolio;
  end if;

  if to_regclass('public.portfolio') is null then
    raise exception 'portfolio missing after rename step';
  end if;

  -- constraints
  if exists (
    select 1 from pg_constraint
    where conname = 'portfolio_snapshots_pkey'
      and conrelid = 'public.portfolio'::regclass
  ) then
    if exists (select 1 from pg_constraint where conname = 'portfolio_pkey') then
      raise exception
        'both portfolio_snapshots_pkey and portfolio_pkey exist; manual migration required';
    end if;
    alter table public.portfolio
      rename constraint portfolio_snapshots_pkey to portfolio_pkey;
  end if;

  if exists (
    select 1 from pg_constraint
    where conname = 'portfolio_snapshots_user_date_unique'
      and conrelid = 'public.portfolio'::regclass
  ) then
    if exists (
      select 1 from pg_constraint where conname = 'portfolio_user_date_unique'
    ) then
      raise exception
        'both portfolio_snapshots_user_date_unique and portfolio_user_date_unique exist; manual migration required';
    end if;
    alter table public.portfolio
      rename constraint portfolio_snapshots_user_date_unique to portfolio_user_date_unique;
  end if;

  if exists (
    select 1 from pg_constraint
    where conname = 'portfolio_snapshots_user_id_fkey'
      and conrelid = 'public.portfolio'::regclass
  ) then
    if exists (
      select 1 from pg_constraint where conname = 'portfolio_user_id_fkey'
    ) then
      raise exception
        'both portfolio_snapshots_user_id_fkey and portfolio_user_id_fkey exist; manual migration required';
    end if;
    alter table public.portfolio
      rename constraint portfolio_snapshots_user_id_fkey to portfolio_user_id_fkey;
  end if;

  -- indexes (non-constraint)
  if to_regclass('public.portfolio_snapshots_user_id_idx') is not null
     and to_regclass('public.portfolio_user_id_idx') is null then
    alter index public.portfolio_snapshots_user_id_idx
      rename to portfolio_user_id_idx;
  end if;

  if to_regclass('public.portfolio_snapshots_as_of_date_idx') is not null
     and to_regclass('public.portfolio_as_of_date_idx') is null then
    alter index public.portfolio_snapshots_as_of_date_idx
      rename to portfolio_as_of_date_idx;
  end if;

  -- RLS policies (names still carry old table prefix after ALTER TABLE RENAME)
  if exists (
    select 1 from pg_policy
    where polname = 'portfolio_snapshots_select_own'
      and polrelid = 'public.portfolio'::regclass
  ) and not exists (
    select 1 from pg_policy
    where polname = 'portfolio_select_own'
      and polrelid = 'public.portfolio'::regclass
  ) then
    alter policy portfolio_snapshots_select_own on public.portfolio
      rename to portfolio_select_own;
  end if;

  if exists (
    select 1 from pg_policy
    where polname = 'portfolio_snapshots_insert_own'
      and polrelid = 'public.portfolio'::regclass
  ) and not exists (
    select 1 from pg_policy
    where polname = 'portfolio_insert_own'
      and polrelid = 'public.portfolio'::regclass
  ) then
    alter policy portfolio_snapshots_insert_own on public.portfolio
      rename to portfolio_insert_own;
  end if;

  if exists (
    select 1 from pg_policy
    where polname = 'portfolio_snapshots_update_own'
      and polrelid = 'public.portfolio'::regclass
  ) and not exists (
    select 1 from pg_policy
    where polname = 'portfolio_update_own'
      and polrelid = 'public.portfolio'::regclass
  ) then
    alter policy portfolio_snapshots_update_own on public.portfolio
      rename to portfolio_update_own;
  end if;

  if exists (
    select 1 from pg_policy
    where polname = 'portfolio_snapshots_delete_own'
      and polrelid = 'public.portfolio'::regclass
  ) and not exists (
    select 1 from pg_policy
    where polname = 'portfolio_delete_own'
      and polrelid = 'public.portfolio'::regclass
  ) then
    alter policy portfolio_snapshots_delete_own on public.portfolio
      rename to portfolio_delete_own;
  end if;
end $$;

-- Ensure authenticated privileges follow the new table name
grant select, insert, update, delete on public.portfolio to authenticated;

-- Recreate index views against etf_valuation
create or replace view public.index_latest_snapshot as
with latest_price as (
  select distinct on (index_code)
    index_code,
    trade_date,
    close
  from public.index_daily_prices
  order by index_code, trade_date desc
),
history_high as (
  select
    index_code,
    max(close) as history_high
  from public.index_daily_prices
  group by index_code
)
select
  i.code,
  i.name,
  i.category,
  i.display_order,
  p.trade_date as as_of_date,
  p.close,
  h.history_high,
  case
    when p.close is null or h.history_high is null or h.history_high <= 0 then null
    else round(((p.close / h.history_high - 1) * 100)::numeric, 1)
  end as drawdown_from_high_pct,
  s.current_pe_ttm as pe_ttm,
  null::numeric as pe_percentile_current,
  null::numeric as percentile_5y_pe,
  null::numeric as percentile_10y_pe,
  null::numeric as pb,
  null::numeric as pb_percentile_current,
  null::numeric as pb_percentile_5y,
  null::numeric as pb_percentile_10y,
  s.pe_ttm_avg_5y,
  s.pe_ttm_avg_10y,
  s.trade_date as valuation_as_of_date
from public.indices i
left join latest_price p on p.index_code = i.code
left join history_high h on h.index_code = i.code
left join public.etf_valuation s on s.tracking_index_code = i.code
order by i.display_order;

create or replace view public.index_detail_snapshot as
select
  i.code,
  i.name,
  i.category,
  i.display_order,
  (
    select max(trade_date)
    from public.index_daily_prices p
    where p.index_code = i.code
  ) as latest_price_date,
  (
    select s.trade_date
    from public.etf_valuation s
    where s.tracking_index_code = i.code
  ) as latest_valuation_date,
  (
    select max(as_of_date)
    from public.index_industry_weights w
    where w.index_code = i.code
  ) as latest_industry_date
from public.indices i
order by i.display_order;

comment on table public.etf_valuation is '跟踪指数估值（当日 PE + 5y/10y 均值）';
comment on table public.portfolio is '组合资产快照';
