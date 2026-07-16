-- 回填 etf_pool.tracking_index_*（准上市 / 基金合同口径）。
-- 幂等：按 etf_code 定点 UPDATE；可重复执行。
-- 同步把可入库的 A 股/中证指数写入 indices（格式须匹配 ^[0-9]{6}\.(SH|SZ|CSI)$）。
-- H 开头 CSI（如 H30184）及海外指数仅写 etf_pool，不进 indices（受 check 约束限制）。

begin;

-- 1) 补齐 / 修正跟踪指数（含名称纠偏）
update public.etf_pool as p
set
  tracking_index_code = v.tracking_index_code,
  tracking_index_name = v.tracking_index_name,
  etf_name = coalesce(v.etf_name, p.etf_name),
  snapshot_date = current_date,
  updated_at = now()
from (
  values
    -- 原空：境内
    ('159928', '000932.SH', '中证主要消费', '汇添富中证主要消费ETF'),
    ('512400', '000819.SH', '中证申万有色金属', '南方中证申万有色金属ETF'),
    ('512480', 'H30184.CSI', '中证全指半导体产品与设备', '国联安中证全指半导体产品与设备ETF'),
    ('512800', '399986.SZ', '中证银行', '华宝中证银行ETF'),
    ('515030', '399976.SZ', '中证新能源汽车', '华夏中证新能源汽车ETF'),
    ('515790', '931151.CSI', '中证光伏产业', '华泰柏瑞中证光伏产业ETF'),
    ('562500', 'H30590.CSI', '中证机器人', '华夏中证机器人ETF'),
    ('562800', '930632.CSI', '中证稀有金属主题', '嘉实中证稀有金属主题ETF'),
    -- 原空：跨境 / QDII
    ('159920', 'HSI.HI', '恒生指数', '华夏恒生ETF'),
    ('513050', 'H30533.CSI', '中证海外中国互联网50', '易方达中证海外中国互联网50ETF'),
    ('513100', 'NDX.NASDAQ', '纳斯达克100', '国泰纳斯达克100ETF'),
    ('513180', 'HSTECH.HI', '恒生科技', '华夏恒生科技ETF'),
    ('513500', 'SPX.OTH', '标普500', '博时标普500ETF'),
    -- 原有但代码纠偏：人工智能主题应为 930713（非 931071）
    ('159819', '930713.CSI', '中证人工智能主题', '易方达中证人工智能主题ETF'),
    -- 已有正确代码：仅补全/对齐名称
    ('516160', '399976.SZ', '中证新能源汽车', null::text)
) as v(etf_code, tracking_index_code, tracking_index_name, etf_name)
where p.etf_code = v.etf_code;

-- 2) 写入 indices 白名单（仅 6 位数字 + SH/SZ/CSI，供后续估值日更）
insert into public.indices (code, name, category, display_order)
values
  ('000819.SH', '中证申万有色金属', '行业', 1200),
  ('000932.SH', '中证主要消费', '行业', 1201),
  ('399976.SZ', '中证新能源汽车', '行业', 1202),
  ('399986.SZ', '中证银行', '行业', 1203),
  ('930632.CSI', '中证稀有金属主题', '主题', 1204),
  ('930713.CSI', '中证人工智能主题', '主题', 1205),
  ('931151.CSI', '中证光伏产业', '行业', 1206)
on conflict (code) do update
set
  name = excluded.name,
  category = excluded.category,
  updated_at = now();

commit;
