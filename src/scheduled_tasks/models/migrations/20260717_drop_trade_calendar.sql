-- Drop unused trade_calendar table（BaoStock 日历同步已停用）。

drop policy if exists trade_calendar_authenticated_select on public.trade_calendar;
drop table if exists public.trade_calendar;
