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
  created_at timestamptz not null default now(),
  constraint sync_runs_status check (status in ('running', 'success', 'partial', 'failed'))
);

create index if not exists idx_sync_runs_started_at
  on sync_runs (started_at desc);

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
