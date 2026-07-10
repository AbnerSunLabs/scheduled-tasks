-- ETF 投资驾驶舱：用户账本 12 表 + fx_rates + RLS
-- 目标库：Supabase Postgres（需已有 auth.users）
-- 幂等：可重复执行（create if not exists / drop policy if exists）
-- 注意：CREATE TABLE IF NOT EXISTS 不会给已存在表补列/改约束；结构变更请另开新 migration。
-- 账本数据由 stock-charts UI 经 Auth/RLS 写入；本仓库 job 只写 fx_rates

-- ---------------------------------------------------------------------------
-- 共享：汇率
-- ---------------------------------------------------------------------------
create table if not exists public.fx_rates (
  rate_date date not null,
  from_currency text not null,
  to_currency text not null,
  rate numeric(18, 8) not null,
  source text not null default 'frankfurter',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (rate_date, from_currency, to_currency),
  constraint fx_rates_from_currency_check
    check (from_currency in ('CNY', 'HKD', 'USD')),
  constraint fx_rates_to_currency_check
    check (to_currency in ('CNY', 'HKD', 'USD')),
  constraint fx_rates_pair_distinct check (from_currency <> to_currency),
  constraint fx_rates_rate_positive check (rate > 0)
);

create index if not exists fx_rates_rate_date_idx
  on public.fx_rates (rate_date desc);

-- ---------------------------------------------------------------------------
-- 用户账本
-- ---------------------------------------------------------------------------
create table if not exists public.portfolio_settings (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  base_currency text not null default 'CNY',
  benchmark_id text,
  relative_drift_threshold numeric(8, 4) not null default 0.20,
  absolute_drift_threshold numeric(8, 4) not null default 0.05,
  review_cadence_days integer not null default 90,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint portfolio_settings_base_currency_check
    check (base_currency in ('CNY', 'HKD', 'USD')),
  constraint portfolio_settings_relative_drift_check
    check (relative_drift_threshold > 0),
  constraint portfolio_settings_absolute_drift_check
    check (absolute_drift_threshold > 0),
  constraint portfolio_settings_review_cadence_check
    check (review_cadence_days > 0),
  constraint portfolio_settings_user_id_unique unique (user_id)
);

create table if not exists public.etf_instruments (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  symbol text not null,
  name text not null,
  market text not null,
  currency text not null,
  asset_class text not null,
  tracking_index text,
  benchmark_id text,
  expense_ratio numeric(12, 6),
  distribution_policy text,
  liquidity_tag text,
  valuation_tag text,
  default_allocation_role text,
  grid_eligible boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint etf_instruments_market_check check (market in ('CN', 'HK', 'US')),
  constraint etf_instruments_currency_check check (currency in ('CNY', 'HKD', 'USD')),
  constraint etf_instruments_role_check
    check (
      default_allocation_role is null
      or default_allocation_role in ('core', 'satellite', 'cash', 'watch')
    ),
  constraint etf_instruments_user_symbol_unique unique (user_id, symbol)
);

create index if not exists etf_instruments_user_id_idx
  on public.etf_instruments (user_id);

create table if not exists public.target_allocations (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  -- 软引用：池内标的可能仅存在于 etf_pool_snapshots，不强制 FK
  instrument_id text not null,
  target_weight numeric(12, 8) not null,
  allocation_role text not null,
  updated_at timestamptz not null default now(),
  created_at timestamptz not null default now(),
  constraint target_allocations_weight_check
    check (target_weight >= 0 and target_weight <= 1),
  constraint target_allocations_role_check
    check (allocation_role in ('core', 'satellite', 'cash', 'watch')),
  constraint target_allocations_user_instrument_unique unique (user_id, instrument_id)
);

create index if not exists target_allocations_user_id_idx
  on public.target_allocations (user_id);
create index if not exists target_allocations_instrument_id_idx
  on public.target_allocations (instrument_id);

create table if not exists public.positions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  instrument_id text not null,
  as_of_date date not null,
  shares numeric(20, 8) not null,
  average_cost numeric(20, 8) not null,
  current_price numeric(20, 8) not null,
  market_value numeric(20, 8) not null,
  currency text not null,
  fx_rate_to_base numeric(18, 8) not null,
  market_value_base numeric(20, 8) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint positions_currency_check check (currency in ('CNY', 'HKD', 'USD')),
  constraint positions_fx_positive check (fx_rate_to_base > 0)
);

create index if not exists positions_user_id_idx on public.positions (user_id);
create index if not exists positions_instrument_id_idx on public.positions (instrument_id);
create index if not exists positions_as_of_date_idx on public.positions (as_of_date desc);

create table if not exists public.cash_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  currency text not null,
  as_of_date date not null,
  balance numeric(20, 8) not null,
  fx_rate_to_base numeric(18, 8) not null,
  balance_base numeric(20, 8) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint cash_accounts_currency_check check (currency in ('CNY', 'HKD', 'USD')),
  constraint cash_accounts_fx_positive check (fx_rate_to_base > 0),
  constraint cash_accounts_user_currency_date_unique unique (user_id, currency, as_of_date)
);

create index if not exists cash_accounts_user_id_idx on public.cash_accounts (user_id);

create table if not exists public.rebalance_plans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  status text not null default 'draft',
  reason text not null default '',
  trigger_reason text not null,
  target_weights jsonb not null default '{}'::jsonb,
  planned_trades jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint rebalance_plans_status_check
    check (status in ('draft', 'active', 'completed', 'cancelled')),
  constraint rebalance_plans_trigger_check
    check (
      trigger_reason in (
        'absolute_drift',
        'relative_drift',
        'calendar_review',
        'cash_deployment'
      )
    )
);

create index if not exists rebalance_plans_user_id_idx on public.rebalance_plans (user_id);

create table if not exists public.grid_plans (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  instrument_id text not null,
  status text not null default 'draft',
  params jsonb not null default '{}'::jsonb,
  legs jsonb not null default '[]'::jsonb,
  aggregated_rows jsonb not null default '[]'::jsonb,
  total_budget numeric(20, 8) not null default 0,
  remaining_budget numeric(20, 8) not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint grid_plans_status_check
    check (status in ('draft', 'active', 'paused', 'closed'))
);

create index if not exists grid_plans_user_id_idx on public.grid_plans (user_id);
create index if not exists grid_plans_instrument_id_idx on public.grid_plans (instrument_id);

create table if not exists public.decision_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  title text not null,
  hypothesis text not null,
  validation_condition text not null,
  invalid_condition text not null,
  review_date date not null,
  status text not null default 'open',
  linked_instrument_id text,
  linked_trade_id uuid,
  linked_rebalance_plan_id uuid references public.rebalance_plans (id) on delete set null,
  linked_grid_plan_id uuid references public.grid_plans (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint decision_logs_status_check
    check (status in ('open', 'validated', 'invalidated', 'archived'))
);

create index if not exists decision_logs_user_id_idx on public.decision_logs (user_id);

create table if not exists public.trade_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  instrument_id text not null,
  trade_date date not null,
  settlement_date date,
  side text not null,
  price numeric(20, 8) not null,
  quantity numeric(20, 8) not null,
  fee numeric(20, 8) not null default 0,
  tax numeric(20, 8) not null default 0,
  currency text not null,
  fx_rate_to_base numeric(18, 8) not null,
  execution_intent text not null default 'manual',
  rebalance_plan_id uuid references public.rebalance_plans (id) on delete set null,
  grid_plan_id uuid references public.grid_plans (id) on delete set null,
  decision_log_id uuid references public.decision_logs (id) on delete set null,
  broker_ref text,
  import_row_index integer,
  import_hash text,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint trade_records_side_check check (side in ('buy', 'sell')),
  constraint trade_records_currency_check check (currency in ('CNY', 'HKD', 'USD')),
  constraint trade_records_intent_check
    check (execution_intent in ('rebalance', 'grid', 'manual')),
  constraint trade_records_fx_positive check (fx_rate_to_base > 0),
  constraint trade_records_quantity_positive check (quantity > 0)
);

create index if not exists trade_records_user_id_idx on public.trade_records (user_id);
create index if not exists trade_records_instrument_id_idx on public.trade_records (instrument_id);
create index if not exists trade_records_trade_date_idx on public.trade_records (trade_date desc);
create index if not exists trade_records_rebalance_plan_id_idx
  on public.trade_records (rebalance_plan_id);
create index if not exists trade_records_grid_plan_id_idx on public.trade_records (grid_plan_id);
create unique index if not exists trade_records_user_broker_ref_uidx
  on public.trade_records (user_id, broker_ref)
  where broker_ref is not null;
create unique index if not exists trade_records_user_import_hash_uidx
  on public.trade_records (user_id, import_hash)
  where import_hash is not null;

-- decision_logs.linked_trade_id 延后加 FK，避免与 trade_records 循环依赖建表失败
do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'decision_logs_linked_trade_id_fkey'
  ) then
    alter table public.decision_logs
      add constraint decision_logs_linked_trade_id_fkey
      foreign key (linked_trade_id)
      references public.trade_records (id)
      on delete set null;
  end if;
end $$;

create table if not exists public.cash_flows (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  flow_date date not null,
  type text not null,
  amount numeric(20, 8) not null,
  currency text not null,
  fx_rate_to_base numeric(18, 8) not null,
  amount_base numeric(20, 8) not null,
  instrument_id text,
  counter_currency text,
  counter_amount numeric(20, 8),
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint cash_flows_type_check
    check (
      type in (
        'deposit',
        'withdrawal',
        'dividend',
        'fee',
        'tax',
        'interest',
        'fx_exchange'
      )
    ),
  constraint cash_flows_currency_check check (currency in ('CNY', 'HKD', 'USD')),
  constraint cash_flows_counter_currency_check
    check (counter_currency is null or counter_currency in ('CNY', 'HKD', 'USD')),
  constraint cash_flows_fx_positive check (fx_rate_to_base > 0),
  constraint cash_flows_fx_exchange_legs_check
    check (
      type <> 'fx_exchange'
      or (
        counter_currency is not null
        and counter_amount is not null
        and counter_amount > 0
      )
    )
);

create index if not exists cash_flows_user_id_idx on public.cash_flows (user_id);
create index if not exists cash_flows_flow_date_idx on public.cash_flows (flow_date desc);
create index if not exists cash_flows_instrument_id_idx on public.cash_flows (instrument_id);

create table if not exists public.review_entries (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  period_start date not null,
  period_end date not null,
  report_markdown text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint review_entries_period_check check (period_end >= period_start)
);

create index if not exists review_entries_user_id_idx on public.review_entries (user_id);

create table if not exists public.portfolio_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  as_of_date date not null,
  total_market_value_base numeric(20, 8) not null,
  cash_value_base numeric(20, 8) not null,
  total_assets_base numeric(20, 8) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint portfolio_snapshots_user_date_unique unique (user_id, as_of_date)
);

create index if not exists portfolio_snapshots_user_id_idx
  on public.portfolio_snapshots (user_id);
create index if not exists portfolio_snapshots_as_of_date_idx
  on public.portfolio_snapshots (as_of_date desc);

-- ---------------------------------------------------------------------------
-- RLS：账本按 user_id；共享行情/汇率 authenticated 只读
-- ---------------------------------------------------------------------------
create or replace function public.cockpit_apply_owner_rls(p_table regclass)
returns void
language plpgsql
as $$
declare
  t text := p_table::text;
  short_name text := replace(t, 'public.', '');
  pol text;
begin
  execute format('alter table %s enable row level security', t);
  -- 不 FORCE：保留表所有者 / DATABASE_URL 运维角色在未走 JWT 时的管理能力；
  -- authenticated 客户端仍受 policy 约束。

  foreach pol in array array['select', 'insert', 'update', 'delete']
  loop
    execute format(
      'drop policy if exists %I on %s',
      short_name || '_' || pol || '_own',
      t
    );
  end loop;

  execute format(
    'create policy %I on %s for select to authenticated using ((select auth.uid()) = user_id)',
    short_name || '_select_own',
    t
  );
  execute format(
    'create policy %I on %s for insert to authenticated with check ((select auth.uid()) = user_id)',
    short_name || '_insert_own',
    t
  );
  execute format(
    'create policy %I on %s for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id)',
    short_name || '_update_own',
    t
  );
  execute format(
    'create policy %I on %s for delete to authenticated using ((select auth.uid()) = user_id)',
    short_name || '_delete_own',
    t
  );
end;
$$;

select public.cockpit_apply_owner_rls('public.portfolio_settings');
select public.cockpit_apply_owner_rls('public.etf_instruments');
select public.cockpit_apply_owner_rls('public.target_allocations');
select public.cockpit_apply_owner_rls('public.positions');
select public.cockpit_apply_owner_rls('public.cash_accounts');
select public.cockpit_apply_owner_rls('public.rebalance_plans');
select public.cockpit_apply_owner_rls('public.grid_plans');
select public.cockpit_apply_owner_rls('public.decision_logs');
select public.cockpit_apply_owner_rls('public.trade_records');
select public.cockpit_apply_owner_rls('public.cash_flows');
select public.cockpit_apply_owner_rls('public.review_entries');
select public.cockpit_apply_owner_rls('public.portfolio_snapshots');

-- 共享只读策略（job 使用 DATABASE_URL / 表所有者绕过 RLS）
create or replace function public.cockpit_apply_authenticated_read(p_table regclass)
returns void
language plpgsql
as $$
declare
  t text := p_table::text;
  short_name text := replace(t, 'public.', '');
begin
  execute format('alter table %s enable row level security', t);
  execute format('drop policy if exists %I on %s', short_name || '_select_authenticated', t);
  execute format(
    'create policy %I on %s for select to authenticated using (true)',
    short_name || '_select_authenticated',
    t
  );
end;
$$;

select public.cockpit_apply_authenticated_read('public.fx_rates');

-- 已有共享行情表：补 authenticated 只读（不改表结构）
do $$
begin
  if to_regclass('public.etf_daily') is not null then
    perform public.cockpit_apply_authenticated_read('public.etf_daily');
  end if;
  if to_regclass('public.etf_pool_snapshots') is not null then
    perform public.cockpit_apply_authenticated_read('public.etf_pool_snapshots');
  end if;
  if to_regclass('public.indices') is not null then
    perform public.cockpit_apply_authenticated_read('public.indices');
  end if;
  if to_regclass('public.index_daily_prices') is not null then
    perform public.cockpit_apply_authenticated_read('public.index_daily_prices');
  end if;
end $$;

-- 显式授权：避免仅有 policy、无 table privilege 时 authenticated 仍 permission denied
grant select, insert, update, delete on
  public.portfolio_settings,
  public.etf_instruments,
  public.target_allocations,
  public.positions,
  public.cash_accounts,
  public.rebalance_plans,
  public.grid_plans,
  public.decision_logs,
  public.trade_records,
  public.cash_flows,
  public.review_entries,
  public.portfolio_snapshots
to authenticated;

grant select on public.fx_rates to authenticated;

do $$
begin
  if to_regclass('public.etf_daily') is not null then
    execute 'grant select on public.etf_daily to authenticated';
  end if;
  if to_regclass('public.etf_pool_snapshots') is not null then
    execute 'grant select on public.etf_pool_snapshots to authenticated';
  end if;
  if to_regclass('public.indices') is not null then
    execute 'grant select on public.indices to authenticated';
  end if;
  if to_regclass('public.index_daily_prices') is not null then
    execute 'grant select on public.index_daily_prices to authenticated';
  end if;
end $$;
