-- 出池并清理上证50 ETF（510050）及其独占跟踪指数 000016.SH（幂等）。
-- 删除 indices 时 ON DELETE CASCADE 会清 index_industry_weights（若存在 FK）。

begin;

delete from public.etf_pool
where etf_code = '510050';

delete from public.etf_daily
where etf_code = '510050';

delete from public.etf_valuation
where tracking_index_code = '000016.SH';

delete from public.indices
where code = '000016.SH'
  and not exists (
    select 1
    from public.etf_pool as p
    where p.tracking_index_code = indices.code
  );

commit;
