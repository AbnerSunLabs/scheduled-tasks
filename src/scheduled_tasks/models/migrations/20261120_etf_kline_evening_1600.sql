-- 当日 ETF 日 K 从北京 18:10 / 18:30 / 19:00 提前到 16:00 / 16:30 / 17:00。
-- 库时钟是 UTC，没有夏令时。沪深300ETF 当天收盘已在库里就不再叫，逻辑不变。

do $$
declare
  job_name text;
begin
  foreach job_name in array array[
    'dispatch-etf-kline-1810',
    'dispatch-etf-kline-1830',
    'dispatch-etf-kline-1900',
    'dispatch-etf-kline-1600',
    'dispatch-etf-kline-1630',
    'dispatch-etf-kline-1700'
  ]
  loop
    if exists (select 1 from cron.job where jobname = job_name) then
      perform cron.unschedule(job_name);
    end if;
  end loop;
end;
$$;

-- 北京时间 16:00 / 16:30 / 17:00。
select cron.schedule('dispatch-etf-kline-1600', '0 8 * * 1-5', $$select private.dispatch_etf_kline_evening()$$);
select cron.schedule('dispatch-etf-kline-1630', '30 8 * * 1-5', $$select private.dispatch_etf_kline_evening()$$);
select cron.schedule('dispatch-etf-kline-1700', '0 9 * * 1-5', $$select private.dispatch_etf_kline_evening()$$);
