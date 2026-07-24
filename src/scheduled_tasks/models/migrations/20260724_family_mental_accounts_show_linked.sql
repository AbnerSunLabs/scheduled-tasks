-- 心理账户：是否在水波图展示关联账户名称
-- 幂等：可重复执行

alter table public.family_mental_accounts
  add column if not exists show_linked_accounts boolean;

update public.family_mental_accounts
set show_linked_accounts = true
where show_linked_accounts is null;

alter table public.family_mental_accounts
  alter column show_linked_accounts set default true;

alter table public.family_mental_accounts
  alter column show_linked_accounts set not null;

comment on column public.family_mental_accounts.show_linked_accounts is
  '水波图是否展示关联账目名称；默认 true';
