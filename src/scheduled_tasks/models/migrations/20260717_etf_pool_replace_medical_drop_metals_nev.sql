-- ETF 池调整（幂等）：
-- 1) 医疗 ETF：159828 → 512170（华宝中证医疗，跟踪仍为 399989.SZ）
-- 2) 出池：有色金属 512400、新能源汽车 515030、新能源车 516160、稀有金属主题 562800
-- 仅改 etf_pool；不删 etf_daily / indices（避免级联清行情）。

begin;

insert into public.etf_pool (
  etf_code,
  etf_name,
  category,
  direction,
  source,
  tracking_index_code,
  tracking_index_name,
  aum_yi,
  avg_daily_turnover_yi,
  premium_discount,
  expense_ratio,
  snapshot_date,
  updated_at
)
select
  '512170',
  '华宝中证医疗ETF',
  p.category,
  p.direction,
  p.source,
  p.tracking_index_code,
  p.tracking_index_name,
  p.aum_yi,
  p.avg_daily_turnover_yi,
  p.premium_discount,
  p.expense_ratio,
  current_date,
  now()
from public.etf_pool as p
where p.etf_code = '159828'
on conflict (etf_code) do update
set
  etf_name = excluded.etf_name,
  category = excluded.category,
  direction = excluded.direction,
  source = excluded.source,
  tracking_index_code = excluded.tracking_index_code,
  tracking_index_name = excluded.tracking_index_name,
  aum_yi = excluded.aum_yi,
  avg_daily_turnover_yi = excluded.avg_daily_turnover_yi,
  premium_discount = excluded.premium_discount,
  expense_ratio = excluded.expense_ratio,
  snapshot_date = excluded.snapshot_date,
  updated_at = now();

-- 若 159828 已不在池中，仍确保 512170 存在（兜底 upsert）
insert into public.etf_pool (
  etf_code,
  etf_name,
  category,
  direction,
  source,
  tracking_index_code,
  tracking_index_name,
  snapshot_date,
  updated_at
)
values (
  '512170',
  '华宝中证医疗ETF',
  '行业',
  '医疗',
  '预计算',
  '399989.SZ',
  '中证医疗',
  current_date,
  now()
)
on conflict (etf_code) do update
set
  etf_name = excluded.etf_name,
  tracking_index_code = coalesce(public.etf_pool.tracking_index_code, excluded.tracking_index_code),
  tracking_index_name = coalesce(public.etf_pool.tracking_index_name, excluded.tracking_index_name),
  snapshot_date = excluded.snapshot_date,
  updated_at = now();

delete from public.etf_pool
where etf_code in ('159828', '512400', '515030', '516160', '562800');

commit;
