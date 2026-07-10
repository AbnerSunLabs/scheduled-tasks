create table if not exists indices (
  code text primary key,
  name text not null,
  category text not null,
  display_order integer not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint indices_code_format check (code ~ '^[0-9]{6}\.(SH|SZ|CSI)$'),
  constraint indices_category_not_blank check (length(trim(category)) > 0)
);

create table if not exists index_daily_prices (
  index_code text not null references indices(code) on delete cascade,
  trade_date date not null,
  close numeric(18, 4) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (index_code, trade_date),
  constraint index_daily_prices_close_positive check (close > 0)
);

create index if not exists idx_index_daily_prices_trade_date
  on index_daily_prices (trade_date desc);

create table if not exists index_daily_valuations (
  index_code text not null references indices(code) on delete cascade,
  trade_date date not null,
  pe_ttm numeric(18, 4),
  pb numeric(18, 4),
  source text not null default 'index_dailybasic',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (index_code, trade_date),
  constraint index_daily_valuations_pe_positive check (pe_ttm is null or pe_ttm > 0),
  constraint index_daily_valuations_pb_positive check (pb is null or pb > 0)
);

create index if not exists idx_index_daily_valuations_trade_date
  on index_daily_valuations (trade_date desc);

create table if not exists index_industry_weights (
  index_code text not null references indices(code) on delete cascade,
  as_of_date date not null,
  sw_level text not null,
  industry_name text not null,
  weight_pct numeric(10, 4) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (index_code, as_of_date, sw_level, industry_name),
  constraint index_industry_weights_level check (sw_level in ('sw1', 'sw2', 'sw3')),
  constraint index_industry_weights_pct check (weight_pct > 0 and weight_pct <= 100)
);

create index if not exists idx_index_industry_weights_as_of_date
  on index_industry_weights (as_of_date desc);

-- index_codes 为历史命名遗留：语义为「本 run 涉及的标的代码」，不限于指数。
create table if not exists sync_runs (
  id bigserial primary key,
  job_name text not null,
  status text not null,
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  index_codes text[] not null default '{}',
  success_codes text[] not null default '{}',
  failure_count integer not null default 0,
  success_count integer not null default 0,
  error_summary jsonb not null default '[]'::jsonb,
  meta jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint sync_runs_status check (status in ('running', 'success', 'partial', 'failed'))
);

create index if not exists idx_sync_runs_started_at
  on sync_runs (started_at desc);

-- ETF 表：索引命名沿用线上 Supabase 默认风格（后缀 _idx），与指数表 idx_ 前缀不同。
-- 指数相关表/视图暂不维护；本仓库主写 etf_daily，只读 etf_pool_snapshots。
create table if not exists etf_pool_snapshots (
  etf_code text primary key,
  etf_name text not null,
  category text not null,
  direction text,
  source text not null default '预计算',
  tracking_index_code text,
  tracking_index_name text,
  aum_yi numeric,
  avg_daily_turnover_yi numeric,
  premium_discount numeric,
  expense_ratio numeric,
  snapshot_date date not null,
  updated_at timestamptz not null default now()
);

create index if not exists etf_pool_snapshots_snapshot_date_idx
  on etf_pool_snapshots (snapshot_date desc);

create table if not exists etf_daily (
  etf_code text not null,
  trade_date date not null,
  open numeric,
  high numeric,
  low numeric,
  close numeric not null,
  volume numeric,
  amount numeric,
  nav numeric,
  premium_rate numeric,
  fund_size numeric,
  listing_days integer,
  updated_at timestamptz not null default now(),
  bid_price numeric,
  ask_price numeric,
  -- 不复权 open/high/low/close；*_qfq 前复权；*_hfq 后复权
  open_qfq numeric(18, 4),
  high_qfq numeric(18, 4),
  low_qfq numeric(18, 4),
  close_qfq numeric(18, 4),
  open_hfq numeric(18, 4),
  high_hfq numeric(18, 4),
  low_hfq numeric(18, 4),
  close_hfq numeric(18, 4),
  -- 仅表示不复权 OHLCV 写入来源；adj_check 不更新本列
  price_source text,
  primary key (etf_code, trade_date)
);

create index if not exists etf_daily_trade_date_idx
  on etf_daily (trade_date desc);

create table if not exists etf_valuation_snapshots (
  tracking_index_code text primary key,
  trade_date date not null,
  current_pe_ttm numeric,
  pe_ttm_avg_5y numeric,
  pe_ttm_avg_10y numeric,
  updated_at timestamptz not null default now()
);

-- 汇率（共享只读；RLS 与用户账本见 migrations/20260710_cockpit_ledger_and_fx_rates.sql）
create table if not exists fx_rates (
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
  on fx_rates (rate_date desc);

-- 用户账本 12 表（依赖 auth.users）不放本文件，见：
-- models/migrations/20260710_cockpit_ledger_and_fx_rates.sql

-- 以下指数视图依赖基表；指数侧暂不维护，视图随基表停更而过期。
create or replace view index_latest_snapshot as
with latest_price as (
  select distinct on (index_code)
    index_code,
    trade_date,
    close
  from index_daily_prices
  order by index_code, trade_date desc
),
history_high as (
  select
    index_code,
    max(close) as history_high
  from index_daily_prices
  group by index_code
),
latest_valuation as (
  select distinct on (index_code)
    index_code,
    trade_date,
    pe_ttm,
    pb
  from index_daily_valuations
  order by index_code, trade_date desc
),
valuation_percentiles as (
  select
    v.index_code,
    v.trade_date,
    case
      when v.pe_ttm is null then null
      else round(
        (
          count(*) filter (where all_v.pe_ttm is not null and all_v.pe_ttm <= v.pe_ttm)
          * 1000.0
          / nullif(count(*) filter (where all_v.pe_ttm is not null), 0)
        ),
        1
      ) / 10.0
    end as pe_percentile_current,
    case
      when v.pb is null then null
      else round(
        (
          count(*) filter (where all_v.pb is not null and all_v.pb <= v.pb)
          * 1000.0
          / nullif(count(*) filter (where all_v.pb is not null), 0)
        ),
        1
      ) / 10.0
    end as pb_percentile_current
  from latest_valuation v
  join index_daily_valuations all_v
    on all_v.index_code = v.index_code
  group by v.index_code, v.trade_date, v.pe_ttm, v.pb
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
  v.pe_ttm,
  vp.pe_percentile_current,
  null::numeric as percentile_5y_pe,
  null::numeric as percentile_10y_pe,
  v.pb,
  vp.pb_percentile_current,
  null::numeric as pb_percentile_5y,
  null::numeric as pb_percentile_10y
from indices i
left join latest_price p on p.index_code = i.code
left join history_high h on h.index_code = i.code
left join latest_valuation v on v.index_code = i.code
left join valuation_percentiles vp on vp.index_code = i.code
order by i.display_order;

create or replace view index_detail_snapshot as
select
  i.code,
  i.name,
  i.category,
  i.display_order,
  (
    select max(trade_date)
    from index_daily_prices p
    where p.index_code = i.code
  ) as latest_price_date,
  (
    select max(trade_date)
    from index_daily_valuations v
    where v.index_code = i.code
  ) as latest_valuation_date,
  (
    select max(as_of_date)
    from index_industry_weights w
    where w.index_code = i.code
  ) as latest_industry_date
from indices i
order by i.display_order;
