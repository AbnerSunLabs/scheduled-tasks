-- 收录 index_daily_metrics（与 Live / schema.sql 对齐）。
-- 幂等：Live 已有表时 CREATE IF NOT EXISTS 为 no-op；仍补索引、COMMENT、公开读 RLS。
-- 新库若已跑含本表的 schema.sql，仍可执行本文件以补 RLS（schema.sql 不含 RLS）。

create table if not exists public.index_daily_metrics (
  index_code text not null references public.indices(code) on delete cascade,
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
  on public.index_daily_metrics (trade_date desc);

comment on table public.index_daily_metrics is '指数日指标（收盘点位、PE TTM、PB）';
comment on column public.index_daily_metrics.index_code is '指数代码（FK → indices.code）';
comment on column public.index_daily_metrics.trade_date is '交易日';
comment on column public.index_daily_metrics.close is '收盘点位';
comment on column public.index_daily_metrics.pe_ttm is '市盈率 TTM';
comment on column public.index_daily_metrics.pb is '市净率';
comment on column public.index_daily_metrics.price_source is '收盘点位最终采用来源';
comment on column public.index_daily_metrics.valuation_source is 'PE/PB 最终采用来源';
comment on column public.index_daily_metrics.updated_at is '更新时间';

do $$
begin
  if to_regclass('public.index_daily_metrics') is null then
    return;
  end if;

  if exists (
    select 1
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'cockpit_apply_public_read'
  ) then
    perform public.cockpit_apply_public_read('public.index_daily_metrics');
  else
    alter table public.index_daily_metrics enable row level security;
    drop policy if exists index_daily_metrics_select_anon on public.index_daily_metrics;
    create policy index_daily_metrics_select_anon
      on public.index_daily_metrics for select to anon using (true);
    drop policy if exists index_daily_metrics_select_authenticated on public.index_daily_metrics;
    create policy index_daily_metrics_select_authenticated
      on public.index_daily_metrics for select to authenticated using (true);
    grant select on public.index_daily_metrics to anon, authenticated;
  end if;
end $$;
