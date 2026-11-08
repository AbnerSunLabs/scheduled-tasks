-- 场外基金单位净值。当前同步 004752 与 502010。
-- 闹钟在库里：工作日北京 21:30 / 22:30 / 23:30 叫醒 sync-fund-nav.yml。
-- 当天净值已经在库里就不再叫。令牌仍用 Vault：github_actions_dispatch_token。

create table if not exists public.fund_nav_daily (
  fund_code text not null,
  nav_date date not null,
  unit_nav numeric not null,
  source text not null,
  updated_at timestamptz not null default now(),
  primary key (fund_code, nav_date),
  constraint fund_nav_daily_code_format check (fund_code ~ '^[0-9]{6}$'),
  constraint fund_nav_daily_unit_nav_positive check (unit_nav > 0)
);

create index if not exists fund_nav_daily_nav_date_idx
  on public.fund_nav_daily (nav_date desc);

comment on table public.fund_nav_daily is '场外基金单位净值（按披露日）';
comment on column public.fund_nav_daily.fund_code is '基金代码';
comment on column public.fund_nav_daily.nav_date is '净值日期，上海日历日';
comment on column public.fund_nav_daily.unit_nav is '单位净值';
comment on column public.fund_nav_daily.source is '写入来源，当前为 eastmoney';

alter table public.fund_nav_daily enable row level security;

drop policy if exists fund_nav_daily_select_anon on public.fund_nav_daily;
create policy fund_nav_daily_select_anon
  on public.fund_nav_daily
  for select
  to anon
  using (true);

drop policy if exists fund_nav_daily_select_authenticated on public.fund_nav_daily;
create policy fund_nav_daily_select_authenticated
  on public.fund_nav_daily
  for select
  to authenticated
  using (true);

grant select on public.fund_nav_daily to anon, authenticated;

create or replace function private.dispatch_fund_nav_sync()
returns void
language plpgsql
security definer
set search_path = pg_catalog, public, pg_temp
as $$
declare
  session_day date;
begin
  session_day := (timezone('Asia/Shanghai', clock_timestamp()))::date;

  if (
    select count(distinct fund_code)
    from public.fund_nav_daily
    where fund_code in ('004752', '502010')
      and nav_date = session_day
  ) = 2 then
    return;
  end if;

  perform private.github_workflow_dispatch(
    'sync-fund-nav.yml',
    jsonb_build_object('session_date', to_char(session_day, 'YYYY-MM-DD'))
  );
end;
$$;

revoke all on function private.dispatch_fund_nav_sync() from public;
grant execute on function private.dispatch_fund_nav_sync() to postgres;

do $$
declare
  job_name text;
begin
  foreach job_name in array array[
    'dispatch-fund-nav-2130',
    'dispatch-fund-nav-2230',
    'dispatch-fund-nav-2330'
  ]
  loop
    if exists (select 1 from cron.job where jobname = job_name) then
      perform cron.unschedule(job_name);
    end if;
  end loop;
end;
$$;

-- 北京时间 21:30 / 22:30 / 23:30。库时钟是 UTC，没有夏令时。
select cron.schedule(
  'dispatch-fund-nav-2130',
  '30 13 * * 1-5',
  $$select private.dispatch_fund_nav_sync()$$
);
select cron.schedule(
  'dispatch-fund-nav-2230',
  '30 14 * * 1-5',
  $$select private.dispatch_fund_nav_sync()$$
);
select cron.schedule(
  'dispatch-fund-nav-2330',
  '30 15 * * 1-5',
  $$select private.dispatch_fund_nav_sync()$$
);
