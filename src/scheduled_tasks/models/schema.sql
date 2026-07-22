-- 表/列中文注释见 migrations/20260716_add_chinese_comments.sql（新建库跑完本文件后执行该 migration）。

create table if not exists indices (
  code text primary key,
  name text not null,
  category text not null,
  display_order integer not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint indices_code_format check (code ~ '^[A-Z0-9]{2,12}\.(SH|SZ|CSI|HI|NASDAQ|OTH)$'),
  constraint indices_category_not_blank check (length(trim(category)) > 0)
);

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

-- 指数日指标（收盘点位 / PE TTM / PB）；与已删的 index_daily_prices 不同，同一主键可合入点位与估值。
-- 主写：sync_hongsehuojian_fill_validate（字段级 coalesce upsert）。
create table if not exists index_daily_metrics (
  index_code text not null references indices(code) on delete cascade,
  trade_date date not null,
  close numeric,
  pe_ttm numeric,
  pb numeric,
  price_source text,
  valuation_source text,
  updated_at timestamptz not null default now(),
  primary key (index_code, trade_date),
  constraint index_daily_metrics_close_positive check (close is null or close > 0),
  constraint index_daily_metrics_pe_positive check (pe_ttm is null or pe_ttm > 0),
  constraint index_daily_metrics_pb_positive check (pb is null or pb > 0),
  constraint index_daily_metrics_has_value check (num_nonnulls(close, pe_ttm, pb) > 0)
);

create index if not exists idx_index_daily_metrics_trade_date
  on index_daily_metrics (trade_date desc);

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
-- 指数相关：日指标见 index_daily_metrics；红色火箭可写估值 / 日指标 / 行业权重。
-- 本仓库主写 etf_daily，只读 etf_pool（当前池主数据）。
create table if not exists etf_pool (
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

create index if not exists etf_pool_snapshot_date_idx
  on etf_pool (snapshot_date desc);

create table if not exists etf_daily (
  etf_code text not null,
  trade_date date not null,
  open numeric,
  high numeric,
  low numeric,
  close numeric not null,
  volume numeric,
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

create table if not exists index_valuation (
  tracking_index_code text primary key,
  trade_date date not null,
  current_pe_ttm numeric,
  pe_ttm_avg_5y numeric,
  pe_ttm_avg_10y numeric,
  updated_at timestamptz not null default now()
);

-- 用户账本 12 表（依赖 auth.users）不放本文件，见：
-- models/migrations/20260710_cockpit_ledger_and_fx_rates.sql
-- fx_rates 已下线：见 migrations/20260721_drop_fx_rates.sql

-- 指数视图：日线表已删除；收盘相关列固定为 null，估值挂 index_valuation。
create or replace view index_latest_snapshot as
select
  i.code,
  i.name,
  i.category,
  i.display_order,
  null::date as as_of_date,
  null::numeric as close,
  null::numeric as history_high,
  null::numeric as drawdown_from_high_pct,
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
from indices i
left join index_valuation s on s.tracking_index_code = i.code
order by i.display_order;

create or replace view index_detail_snapshot as
select
  i.code,
  i.name,
  i.category,
  i.display_order,
  null::date as latest_price_date,
  (
    select s.trade_date
    from index_valuation s
    where s.tracking_index_code = i.code
  ) as latest_valuation_date,
  (
    select max(as_of_date)
    from index_industry_weights w
    where w.index_code = i.code
  ) as latest_industry_date
from indices i
order by i.display_order;
