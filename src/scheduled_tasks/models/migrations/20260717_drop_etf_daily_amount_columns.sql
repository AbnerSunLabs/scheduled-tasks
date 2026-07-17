-- Drop etf_daily amount columns（成交额不再维护）。
-- amount / amount_source / amount_updated_at 一并删除；补数 job 已停用。

alter table public.etf_daily
  drop column if exists amount,
  drop column if exists amount_source,
  drop column if exists amount_updated_at;

comment on column public.etf_daily.updated_at is
  '价格侧（OHLCV/复权/price_source）最近写入时间';
