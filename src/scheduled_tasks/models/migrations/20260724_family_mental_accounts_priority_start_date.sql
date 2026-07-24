-- 心理账户：优先级 + 开始日期
-- 幂等：可重复执行
-- 存量 start_date 取 least(上海时区当天, target_date)，避免过去达成日导致 CHECK 失败

alter table public.family_mental_accounts
  add column if not exists priority text;

alter table public.family_mental_accounts
  add column if not exists start_date date;

update public.family_mental_accounts
set priority = 'P1'
where priority is null;

update public.family_mental_accounts
set start_date = least(
  (current_timestamp at time zone 'Asia/Shanghai')::date,
  target_date
)
where start_date is null;

alter table public.family_mental_accounts
  alter column priority set not null;

alter table public.family_mental_accounts
  alter column start_date set not null;

alter table public.family_mental_accounts
  drop constraint if exists family_mental_accounts_priority_check;

alter table public.family_mental_accounts
  add constraint family_mental_accounts_priority_check
  check (priority in ('P0', 'P1', 'P2'));

alter table public.family_mental_accounts
  drop constraint if exists family_mental_accounts_start_before_target_check;

alter table public.family_mental_accounts
  add constraint family_mental_accounts_start_before_target_check
  check (start_date <= target_date);

comment on column public.family_mental_accounts.priority is
  '心理账户优先级：P0 / P1 / P2';

comment on column public.family_mental_accounts.start_date is
  '心理账户开始日期（YYYY-MM-DD），须 ≤ target_date';
