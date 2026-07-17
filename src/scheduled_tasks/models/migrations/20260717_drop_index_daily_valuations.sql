-- Drop obsolete daily index valuation table.
-- 估值改由 etf_valuation_snapshots（当日 PE + 5y/10y 均值）承担；视图同步改写。

drop view if exists public.index_latest_snapshot;
drop view if exists public.index_detail_snapshot;

drop table if exists public.index_daily_valuations;

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
left join public.etf_valuation_snapshots s on s.tracking_index_code = i.code
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
    from public.etf_valuation_snapshots s
    where s.tracking_index_code = i.code
  ) as latest_valuation_date,
  (
    select max(as_of_date)
    from public.index_industry_weights w
    where w.index_code = i.code
  ) as latest_industry_date
from public.indices i
order by i.display_order;
