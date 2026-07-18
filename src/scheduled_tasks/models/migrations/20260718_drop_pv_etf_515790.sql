-- 出池并清理光伏 ETF（515790）及其独占跟踪指数 931151.CSI（幂等）。
-- 删除 indices 时 ON DELETE CASCADE 会清 index_industry_weights（若存在）。

begin;

delete from public.etf_pool
where etf_code = '515790';

delete from public.etf_daily
where etf_code = '515790';

delete from public.etf_valuation
where tracking_index_code = '931151.CSI';

delete from public.indices
where code = '931151.CSI'
  and not exists (
    select 1
    from public.etf_pool as p
    where p.tracking_index_code = indices.code
  );

commit;
