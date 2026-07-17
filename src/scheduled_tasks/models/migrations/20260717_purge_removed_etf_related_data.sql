-- 清理已出池 / 换码 ETF 的关联数据（幂等）。
-- 标的：159828（旧医疗）、512400（有色）、515030/516160（新能源车）、562800（稀有金属）。
-- 保留 512170 与其中证医疗指数 399989.SZ。
-- 删除仅被上述标的使用的 indices 时，ON DELETE CASCADE 会清 index_daily_*。

begin;

delete from public.etf_daily
where etf_code in ('159828', '512400', '515030', '516160', '562800');

delete from public.etf_valuation_snapshots
where tracking_index_code in ('000819.SH', '399976.SZ', '930632.CSI');

delete from public.indices
where code in ('000819.SH', '399976.SZ', '930632.CSI')
  and not exists (
    select 1
    from public.etf_pool as p
    where p.tracking_index_code = indices.code
  );

commit;
