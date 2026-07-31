-- 入池：513970 景顺长城恒生消费 ETF（QDII），跟踪恒生消费指数。
-- 基金与指数信息分别由上交所基金公告、恒生指数公司指数目录核实。

begin;

insert into public.indices (code, name, category, display_order)
values ('HSCGSI.HI', '恒生消费指数', '跨境', 1302)
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
values (
  '513970',
  '景顺长城恒生消费ETF（QDII）',
  '跨境',
  '港股消费',
  '预计算',
  'HSCGSI.HI',
  '恒生消费指数',
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
