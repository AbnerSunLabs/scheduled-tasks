-- 515170 华夏中证细分食品饮料产业主题 ETF 跟踪 000815，不是国证食品 399396。
-- 上交所基金产品资料概要的业绩比较基准是中证细分食品饮料产业主题指数。
-- 代码用 000815.SH：indices.code 必须带交易所后缀，与 000932.SH 同一写法。

begin;

insert into public.indices (code, name, category, display_order)
values ('000815.SH', '中证细分食品饮料', '行业', 210)
on conflict (code) do update
set
  name = excluded.name,
  category = excluded.category,
  updated_at = now();

update public.etf_pool
set
  tracking_index_code = '000815.SH',
  tracking_index_name = '中证细分食品饮料',
  updated_at = now()
where etf_code = '515170'
  and tracking_index_code = '399396.SZ';

delete from public.index_valuation
where tracking_index_code = '399396.SZ'
  and not exists (
    select 1
    from public.etf_pool
    where tracking_index_code = '399396.SZ'
  );

delete from public.indices
where code = '399396.SZ'
  and not exists (
    select 1
    from public.etf_pool
    where tracking_index_code = '399396.SZ'
  );

commit;
