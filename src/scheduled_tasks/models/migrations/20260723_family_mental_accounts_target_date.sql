-- 心理账户：预期达成目标日期
-- 幂等：可重复执行

alter table public.family_mental_accounts
  add column if not exists target_date date;

-- 存量空值先回填当天，再加非空约束
update public.family_mental_accounts
set target_date = (created_at at time zone 'utc')::date
where target_date is null;

alter table public.family_mental_accounts
  alter column target_date set not null;

comment on column public.family_mental_accounts.target_date is
  '预期达到目标金额的日期（YYYY-MM-DD）';
