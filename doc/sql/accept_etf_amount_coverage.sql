-- 逐标的 amount 覆盖率验收（分母 = 该 ETF 自身最近 250 行主行情；不足 250 单列）
-- 在获授权回填后于 Supabase SQL Editor 执行；代码就绪阶段勿当作已达标证据。

with ranked as (
  select
    etf_code,
    trade_date,
    amount,
    row_number() over (partition by etf_code order by trade_date desc) as rn
  from public.etf_daily
),
latest as (
  select * from ranked where rn <= 250
),
per_etf as (
  select
    etf_code,
    count(*)::int as rows_in_denom,
    count(amount)::int as amount_nonnull,
    (count(amount)::float / nullif(count(*), 0)) as fill_rate
  from latest
  group by etf_code
)
select
  etf_code,
  rows_in_denom,
  amount_nonnull,
  fill_rate,
  case
    when rows_in_denom < 250 then 'insufficient_history'
    when fill_rate < 0.95 then 'below_95'
    else 'ok'
  end as verdict
from per_etf
order by
  case
    when rows_in_denom < 250 then 0
    when fill_rate < 0.95 then 1
    else 2
  end,
  fill_rate asc nulls first,
  etf_code;
