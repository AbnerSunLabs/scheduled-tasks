-- 下线驾驶舱用户账本 12 表：Job 不读写，前端未接入，Live 几乎无数据。
-- 不删：family_* / insurance_policies / grid_strategies；
-- 不删：cockpit_apply_owner_rls / cockpit_apply_authenticated_read（家庭财务与共享行情 RLS 仍用）。
-- 幂等：表不存在时跳过。
-- 注意：20260710 仍会 CREATE TABLE IF NOT EXISTS 这 12 张表；跑完 20260710 后必须再执行本文件。

do $$
begin
  -- CASCADE：decision_logs ↔ trade_records 循环 FK，以及指向 rebalance_plans / grid_plans 的引用。
  drop table if exists
    public.decision_logs,
    public.trade_records,
    public.rebalance_plans,
    public.grid_plans,
    public.portfolio_settings,
    public.etf_instruments,
    public.target_allocations,
    public.positions,
    public.cash_accounts,
    public.cash_flows,
    public.review_entries,
    public.portfolio
    cascade;
end
$$;
