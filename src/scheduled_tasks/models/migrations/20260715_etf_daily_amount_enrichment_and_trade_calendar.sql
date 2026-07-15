-- ETF 成交额补数列 + 全国市场级交易日历（CN）
-- 前置：须已部署并验证 20260715_rename_etf_pool_snapshots_to_etf_pool.sql（public.etf_pool 存在）。
-- 边界：本文件仅 DDL；不得与 rename 同批部署；live 须单独授权后执行。
-- 语义：etf_daily.updated_at = 价格侧新鲜度；amount 新鲜度看 amount_updated_at。

-- ---------------------------------------------------------------------------
-- 1) etf_daily：amount 来源 / 新鲜度（不碰 OHLC / price_source / updated_at）
-- ---------------------------------------------------------------------------
alter table public.etf_daily
  add column if not exists amount_source text,
  add column if not exists amount_updated_at timestamptz;

comment on column public.etf_daily.amount_source is
  '成交额写入来源（如 akshare）；与 price_source 独立';
comment on column public.etf_daily.amount_updated_at is
  '成交额最近补数时间；不等于价格新鲜度（价格看 updated_at）';
comment on column public.etf_daily.updated_at is
  '价格侧（OHLCV/复权/price_source）最近写入时间；enrichment 禁止刷新本列';

-- ---------------------------------------------------------------------------
-- 2) trade_calendar：全国 A 股市场级日历（market='CN'）
-- ---------------------------------------------------------------------------
create table if not exists public.trade_calendar (
  market text not null,
  cal_date date not null,
  is_open boolean not null,
  updated_at timestamptz not null default now(),
  primary key (market, cal_date)
);

comment on table public.trade_calendar is
  '市场级交易日历；本期仅写入 market=CN（沪深一致，不做 SSE/SZSE 分表）';

create index if not exists trade_calendar_cal_date_idx
  on public.trade_calendar (cal_date desc);

alter table public.trade_calendar enable row level security;

revoke all on table public.trade_calendar from public;
revoke all on table public.trade_calendar from anon;
revoke all on table public.trade_calendar from authenticated;

grant select on table public.trade_calendar to authenticated;
grant all on table public.trade_calendar to service_role;

drop policy if exists trade_calendar_authenticated_select on public.trade_calendar;
create policy trade_calendar_authenticated_select
  on public.trade_calendar
  for select
  to authenticated
  using (true);
