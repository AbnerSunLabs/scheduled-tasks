-- ETF 池调整（幂等）：
-- 1) 出池：512800 银行、512480 半导体、159819 人工智能（并清独占指数）
-- 2) 入池：159939 全指信息技术、159307 红利低波100

begin;

-- 入池
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
    '159939',
    '广发中证全指信息技术ETF',
    '行业',
    '信息技术',
    '预计算',
    '000993.SH',
    '中证全指信息技术',
    current_date,
    now()
  ),
  (
    '159307',
    '博时中证红利低波100ETF',
    '策略',
    '红利低波',
    '预计算',
    '930955.CSI',
    '中证红利低波动100',
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

insert into public.indices (code, name, category, display_order)
values
  ('000993.SH', '中证全指信息技术', '行业', 1300),
  ('930955.CSI', '中证红利低波动100', '策略', 1301)
on conflict (code) do update
set
  name = excluded.name,
  category = excluded.category,
  updated_at = now();

-- 出池 + 清关联
delete from public.etf_pool
where etf_code in ('512800', '512480', '159819');

delete from public.etf_daily
where etf_code in ('512800', '512480', '159819');

delete from public.etf_valuation
where tracking_index_code in ('399986.SZ', 'H30184.CSI', '930713.CSI');

delete from public.indices
where code in ('399986.SZ', 'H30184.CSI', '930713.CSI')
  and not exists (
    select 1
    from public.etf_pool as p
    where p.tracking_index_code = indices.code
  );

commit;
