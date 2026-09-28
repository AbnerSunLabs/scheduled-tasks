-- 确保创业板 ETF 159915 在池，并新增中证 A500 ETF 563360（幂等）。
-- 基金名称与跟踪指数分别以基金管理人、上交所及中证指数官方资料为准。

begin;

insert into public.indices (code, name, category, display_order)
values
  ('399006.SZ', '创业板指数', '宽基', 1303),
  ('000510.SH', '中证A500指数', '宽基', 1304)
on conflict (code) do update
set
  name = excluded.name,
  category = excluded.category,
  updated_at = now();

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
values
  (
    '159915',
    '易方达创业板ETF',
    '宽基',
    '创业板',
    '预计算',
    '399006.SZ',
    '创业板指数',
    current_date,
    now()
  ),
  (
    '563360',
    '华泰柏瑞中证A500ETF',
    '宽基',
    '中证A500',
    '预计算',
    '000510.SH',
    '中证A500指数',
    current_date,
    now()
  )
on conflict (etf_code) do update
set
  etf_name = excluded.etf_name,
  category = excluded.category,
  direction = excluded.direction,
  source = excluded.source,
  tracking_index_code = excluded.tracking_index_code,
  tracking_index_name = excluded.tracking_index_name,
  snapshot_date = excluded.snapshot_date,
  updated_at = now();

commit;
