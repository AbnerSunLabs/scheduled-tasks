-- Drop unused trade_calendar table（BaoStock 日历同步已停用）。
-- 可重复执行：表已删时跳过 DROP POLICY（否则 ON 不存在的关系会失败）。

do $$
begin
  if to_regclass('public.trade_calendar') is not null then
    drop policy if exists trade_calendar_authenticated_select on public.trade_calendar;
    drop table if exists public.trade_calendar;
  end if;
end $$;
