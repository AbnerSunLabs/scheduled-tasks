-- 删除指数日线表 index_daily_prices（全市场指数同步已停，残缺数据已无维护价值）。
-- 同步重建依赖视图（不再读日线；估值/行业日期仍可用）。

begin;

drop view if exists public.index_latest_snapshot;
drop view if exists public.index_detail_snapshot;

drop table if exists public.index_daily_prices;

create or replace view public.index_latest_snapshot as
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
from public.indices i
left join public.etf_valuation s on s.tracking_index_code = i.code
order by i.display_order;

create or replace view public.index_detail_snapshot as
select
  i.code,
  i.name,
  i.category,
  i.display_order,
  null::date as latest_price_date,
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

commit;
