-- 用前一交易日复权因子回填 2025-10-24 缺复权 OHLC（幂等）。
-- 背景：该日有 close 但 qfq/hfq 为空、price_source 为空（半截写入）。

begin;

with prev as (
  select distinct on (d.etf_code)
    d.etf_code,
    d.close as prev_close,
    d.close_qfq as prev_qfq,
    d.close_hfq as prev_hfq
  from public.etf_daily d
  where d.trade_date < date '2025-10-24'
    and d.close is not null and d.close <> 0
    and d.close_qfq is not null
    and d.close_hfq is not null
  order by d.etf_code, d.trade_date desc
)
update public.etf_daily as t
set
  open_qfq = round(t.open * (p.prev_qfq / p.prev_close), 4),
  high_qfq = round(t.high * (p.prev_qfq / p.prev_close), 4),
  low_qfq = round(t.low * (p.prev_qfq / p.prev_close), 4),
  close_qfq = round(t.close * (p.prev_qfq / p.prev_close), 4),
  open_hfq = round(t.open * (p.prev_hfq / p.prev_close), 4),
  high_hfq = round(t.high * (p.prev_hfq / p.prev_close), 4),
  low_hfq = round(t.low * (p.prev_hfq / p.prev_close), 4),
  close_hfq = round(t.close * (p.prev_hfq / p.prev_close), 4),
  price_source = coalesce(t.price_source, 'adj_gap_fill'),
  updated_at = now()
from prev p
where t.etf_code = p.etf_code
  and t.trade_date = date '2025-10-24'
  and (t.close_qfq is null or t.close_hfq is null);

commit;
