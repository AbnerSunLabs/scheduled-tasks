-- 指数收盘的闹钟改到数据库。GitHub 的 schedule 会晚几个小时，
-- 拖过零点后交易日闸门会把周五收盘当成周六休市跳过。
-- 令牌不写在这个文件里，放到 Vault：github_actions_dispatch_token。

create schema if not exists private;

revoke all on schema private from public;
grant usage on schema private to postgres;

create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;

create or replace function private.dispatch_index_level_sync()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, vault, net, pg_temp
as $$
declare
  token text;
  session_day date;
  already boolean;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  select exists (
    select 1
    from public.index_daily_metrics
    where index_code = '000905.SH'
      and trade_date = session_day
      and close is not null
  )
  into already;
  if already then
    return;
  end if;

  select decrypted_secret
  into token
  from vault.decrypted_secrets
  where name = 'github_actions_dispatch_token'
  order by created_at desc
  limit 1;

  if token is null or length(btrim(token)) = 0 then
    raise warning 'github_actions_dispatch_token is missing; skip index level dispatch';
    return;
  end if;

  perform net.http_post(
    url := 'https://api.github.com/repos/AbnerSunLabs/scheduled-tasks/actions/workflows/sync-index-levels.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || btrim(token),
      'Accept', 'application/vnd.github+json',
      'Content-Type', 'application/json',
      'User-Agent', 'scheduled-tasks-cron',
      'X-GitHub-Api-Version', '2022-11-28'
    ),
    body := jsonb_build_object(
      'ref', 'main',
      'inputs', jsonb_build_object('session_date', to_char(session_day, 'YYYY-MM-DD'))
    ),
    timeout_milliseconds := 5000
  );
end;
$$;

revoke all on function private.dispatch_index_level_sync() from public;
grant execute on function private.dispatch_index_level_sync() to postgres;

do $$
declare
  job_name text;
begin
  foreach job_name in array array[
    'dispatch-index-levels-1930',
    'dispatch-index-levels-2030',
    'dispatch-index-levels-2130'
  ]
  loop
    if exists (select 1 from cron.job where jobname = job_name) then
      perform cron.unschedule(job_name);
    end if;
  end loop;
end;
$$;

-- 北京时间 19:30 / 20:30 / 21:30。库时钟是 UTC，没有夏令时。
select cron.schedule(
  'dispatch-index-levels-1930',
  '30 11 * * 1-5',
  $$select private.dispatch_index_level_sync()$$
);
select cron.schedule(
  'dispatch-index-levels-2030',
  '30 12 * * 1-5',
  $$select private.dispatch_index_level_sync()$$
);
select cron.schedule(
  'dispatch-index-levels-2130',
  '30 13 * * 1-5',
  $$select private.dispatch_index_level_sync()$$
);
