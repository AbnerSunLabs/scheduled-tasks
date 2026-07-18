-- 清理不在 etf_pool.tracking_index_code 中的残留 indices（幂等）。
-- 删除 indices 时 ON DELETE CASCADE 会清 index_industry_weights。
-- etf_valuation 为软关联，需显式删除。

begin;

delete from public.etf_valuation v
where not exists (
  select 1
  from public.etf_pool p
  where p.tracking_index_code = v.tracking_index_code
);

delete from public.indices i
where not exists (
  select 1
  from public.etf_pool p
  where p.tracking_index_code = i.code
);

commit;
