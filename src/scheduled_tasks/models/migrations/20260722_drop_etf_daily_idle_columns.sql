-- Drop etf_daily 闲置列（本仓库 job 从不写入，与 COMMENT「非本 job 字段」一致）。
-- nav / premium_rate / fund_size / listing_days / bid_price / ask_price

alter table public.etf_daily
  drop column if exists nav,
  drop column if exists premium_rate,
  drop column if exists fund_size,
  drop column if exists listing_days,
  drop column if exists bid_price,
  drop column if exists ask_price;
