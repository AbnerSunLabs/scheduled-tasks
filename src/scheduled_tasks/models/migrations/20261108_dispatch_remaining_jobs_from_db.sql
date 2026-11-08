-- 其余定时同步也改由数据库叫醒。GitHub schedule 会晚几个小时，
-- 晚到周六就会被交易日闸门跳过。令牌仍用 Vault：github_actions_dispatch_token。
-- 库时钟是 UTC。北京时间 = UTC+8，没有夏令时。

create or replace function private.github_workflow_dispatch(
  workflow_file text,
  inputs jsonb
)
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, vault, net, pg_temp
as $$
declare
  token text;
begin
  if workflow_file !~ '^[A-Za-z0-9._-]+$' then
    raise exception 'invalid workflow file name';
  end if;

  select decrypted_secret
  into token
  from vault.decrypted_secrets
  where name = 'github_actions_dispatch_token'
  order by created_at desc
  limit 1;

  if token is null or length(btrim(token)) = 0 then
    raise warning 'github_actions_dispatch_token is missing; skip %', workflow_file;
    return;
  end if;

  perform net.http_post(
    url := 'https://api.github.com/repos/AbnerSunLabs/scheduled-tasks/actions/workflows/'
      || workflow_file || '/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || btrim(token),
      'Accept', 'application/vnd.github+json',
      'Content-Type', 'application/json',
      'User-Agent', 'scheduled-tasks-cron',
      'X-GitHub-Api-Version', '2022-11-28'
    ),
    body := jsonb_build_object('ref', 'main', 'inputs', coalesce(inputs, '{}'::jsonb)),
    timeout_milliseconds := 5000
  );
end;
$$;

revoke all on function private.github_workflow_dispatch(text, jsonb) from public;
grant execute on function private.github_workflow_dispatch(text, jsonb) to postgres;

-- 指数收盘沿用已有的三档闹钟，只把发请求收进上面的函数。
create or replace function private.dispatch_index_level_sync()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  session_day date;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  if exists (
    select 1
    from public.index_daily_metrics
    where index_code = '000905.SH'
      and trade_date = session_day
      and close is not null
  ) then
    return;
  end if;

  perform private.github_workflow_dispatch(
    'sync-index-levels.yml',
    jsonb_build_object('session_date', to_char(session_day, 'YYYY-MM-DD'))
  );
end;
$$;

revoke all on function private.dispatch_index_level_sync() from public;
grant execute on function private.dispatch_index_level_sync() to postgres;

-- 早上补前一交易日的 Yahoo 收盘。同一上午已经成功跑过就不再叫。
create or replace function private.dispatch_etf_kline_morning()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  session_day date;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  if exists (
    select 1
    from public.sync_runs
    where job_name = 'sync_etf_kline_yfinance'
      and status = 'success'
      and meta->>'mode' = 'incremental'
      and (started_at at time zone 'Asia/Shanghai')::date = session_day
      and extract(hour from started_at at time zone 'Asia/Shanghai') < 12
  ) then
    return;
  end if;

  perform private.github_workflow_dispatch(
    'sync-etf-kline.yml',
    jsonb_build_object(
      'mode', 'incremental',
      'session_date', to_char(session_day, 'YYYY-MM-DD'),
      'force', 'false'
    )
  );
end;
$$;

-- 收盘后试着写入当日 ETF 日 K。沪深300ETF 当天收盘已在库里就不再叫。
create or replace function private.dispatch_etf_kline_evening()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  session_day date;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  if exists (
    select 1
    from public.etf_daily
    where etf_code = '510300'
      and trade_date = session_day
      and close is not null
  ) then
    return;
  end if;

  perform private.github_workflow_dispatch(
    'sync-etf-kline.yml',
    jsonb_build_object(
      'mode', 'incremental',
      'session_date', to_char(session_day, 'YYYY-MM-DD'),
      'force', 'false'
    )
  );
end;
$$;

-- 周日复权巡检。当天已经成功跑过就不再叫。
create or replace function private.dispatch_etf_adj_check()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  session_day date;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  if exists (
    select 1
    from public.sync_runs
    where job_name = 'sync_etf_kline_yfinance'
      and status = 'success'
      and meta->>'mode' = 'adj_check'
      and (started_at at time zone 'Asia/Shanghai')::date = session_day
  ) then
    return;
  end if;

  perform private.github_workflow_dispatch(
    'sync-etf-kline.yml',
    jsonb_build_object('mode', 'adj_check', 'force', 'false')
  );
end;
$$;

-- 沪深300估值。快照日期已经是今天就不再叫。
create or replace function private.dispatch_index_valuation()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  session_day date;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  if exists (
    select 1
    from public.index_valuation
    where tracking_index_code = '000300.SH'
      and trade_date = session_day
  ) then
    return;
  end if;

  perform private.github_workflow_dispatch(
    'sync-index-valuation.yml',
    jsonb_build_object(
      'mode', 'valuation-only',
      'session_date', to_char(session_day, 'YYYY-MM-DD'),
      'etf_code', '510300',
      'index_code', '000300.SH'
    )
  );
end;
$$;

-- 官网双源校验只比对。当天已经成功跑过就不再叫。
create or replace function private.dispatch_official_cross_check()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  session_day date;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  if exists (
    select 1
    from public.sync_runs
    where job_name = 'sync_official_cross_check'
      and status = 'success'
      and (started_at at time zone 'Asia/Shanghai')::date = session_day
  ) then
    return;
  end if;

  perform private.github_workflow_dispatch(
    'sync-official-cross-check.yml',
    jsonb_build_object(
      'mode', 'incremental',
      'session_date', to_char(session_day, 'YYYY-MM-DD'),
      'from_pool', 'true',
      'etf_code', '512170',
      'lookback_bars', '30'
    )
  );
end;
$$;

revoke all on function private.dispatch_etf_kline_morning() from public;
revoke all on function private.dispatch_etf_kline_evening() from public;
revoke all on function private.dispatch_etf_adj_check() from public;
revoke all on function private.dispatch_index_valuation() from public;
revoke all on function private.dispatch_official_cross_check() from public;

grant execute on function private.dispatch_etf_kline_morning() to postgres;
grant execute on function private.dispatch_etf_kline_evening() to postgres;
grant execute on function private.dispatch_etf_adj_check() to postgres;
grant execute on function private.dispatch_index_valuation() to postgres;
grant execute on function private.dispatch_official_cross_check() to postgres;

do $$
declare
  job_name text;
begin
  foreach job_name in array array[
    'dispatch-etf-kline-0940',
    'dispatch-etf-kline-1040',
    'dispatch-etf-kline-1810',
    'dispatch-etf-kline-1830',
    'dispatch-etf-kline-1900',
    'dispatch-etf-adj-1000',
    'dispatch-etf-adj-1100',
    'dispatch-index-valuation-1915',
    'dispatch-index-valuation-2015',
    'dispatch-index-valuation-2115',
    'dispatch-official-cross-check-2000',
    'dispatch-official-cross-check-2100'
  ]
  loop
    if exists (select 1 from cron.job where jobname = job_name) then
      perform cron.unschedule(job_name);
    end if;
  end loop;
end;
$$;

-- 北京时间 09:40 / 10:40，补前一交易日 ETF 收盘。
select cron.schedule('dispatch-etf-kline-0940', '40 1 * * 1-5', $$select private.dispatch_etf_kline_morning()$$);
select cron.schedule('dispatch-etf-kline-1040', '40 2 * * 1-5', $$select private.dispatch_etf_kline_morning()$$);

-- 北京时间 18:10 / 18:30 / 19:00，试写当日 ETF 日 K。
select cron.schedule('dispatch-etf-kline-1810', '10 10 * * 1-5', $$select private.dispatch_etf_kline_evening()$$);
select cron.schedule('dispatch-etf-kline-1830', '30 10 * * 1-5', $$select private.dispatch_etf_kline_evening()$$);
select cron.schedule('dispatch-etf-kline-1900', '0 11 * * 1-5', $$select private.dispatch_etf_kline_evening()$$);

-- 北京时间周日 10:00 / 11:00，复权巡检。
select cron.schedule('dispatch-etf-adj-1000', '0 2 * * 0', $$select private.dispatch_etf_adj_check()$$);
select cron.schedule('dispatch-etf-adj-1100', '0 3 * * 0', $$select private.dispatch_etf_adj_check()$$);

-- 北京时间 19:15 / 20:15 / 21:15，沪深300估值。
select cron.schedule('dispatch-index-valuation-1915', '15 11 * * 1-5', $$select private.dispatch_index_valuation()$$);
select cron.schedule('dispatch-index-valuation-2015', '15 12 * * 1-5', $$select private.dispatch_index_valuation()$$);
select cron.schedule('dispatch-index-valuation-2115', '15 13 * * 1-5', $$select private.dispatch_index_valuation()$$);

-- 北京时间 20:00 / 21:00，官网双源校验。
select cron.schedule('dispatch-official-cross-check-2000', '0 12 * * 1-5', $$select private.dispatch_official_cross_check()$$);
select cron.schedule('dispatch-official-cross-check-2100', '0 13 * * 1-5', $$select private.dispatch_official_cross_check()$$);
