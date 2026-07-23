-- 家庭财务：心理账户 + 活钱账目关联（互斥）
-- 目标库：Supabase Postgres
-- 幂等：可重复执行
-- 金额一律 numeric(18,2)

-- ---------------------------------------------------------------------------
-- 心理账户
-- ---------------------------------------------------------------------------
  create table if not exists public.family_mental_accounts (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null,
  target_amount numeric(18, 2) not null,
  target_date date not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint family_mental_accounts_name_nonempty
    check (char_length(trim(name)) > 0),
  constraint family_mental_accounts_target_positive
    check (target_amount > 0)
);

create index if not exists family_mental_accounts_user_id_idx
  on public.family_mental_accounts (user_id);

comment on table public.family_mental_accounts is
  '心理账户：命名资产目标；进度由关联活钱账目合计 / target_amount';

alter table public.family_mental_accounts enable row level security;

-- ---------------------------------------------------------------------------
-- 关联表（ledger_item_id UNIQUE → 互斥）
-- ---------------------------------------------------------------------------
create table if not exists public.family_mental_account_links (
  mental_account_id uuid not null
    references public.family_mental_accounts (id) on delete cascade,
  ledger_item_id uuid not null
    references public.family_ledger_items (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (mental_account_id, ledger_item_id),
  constraint family_mental_account_links_ledger_item_unique unique (ledger_item_id)
);

create index if not exists family_mental_account_links_user_id_idx
  on public.family_mental_account_links (user_id);

create index if not exists family_mental_account_links_account_id_idx
  on public.family_mental_account_links (mental_account_id);

comment on table public.family_mental_account_links is
  '心理账户 ↔ 活账条目；同一 ledger_item 仅可归属一个心理账户';

alter table public.family_mental_account_links enable row level security;

-- ---------------------------------------------------------------------------
-- RLS（与现有家庭表一致：owner + 白名单）
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
  tables text[] := array[
    'family_mental_accounts',
    'family_mental_account_links'
  ];
  pol text;
begin
  foreach t in array tables
  loop
    foreach pol in array array['select', 'insert', 'update', 'delete']
    loop
      execute format('drop policy if exists %I on public.%I', t || '_' || pol || '_own', t);
    end loop;

    execute format(
      'create policy %I on public.%I for select to authenticated
       using ((select auth.uid()) = user_id and public.is_family_access_allowed())',
      t || '_select_own', t
    );
    execute format(
      'create policy %I on public.%I for insert to authenticated
       with check ((select auth.uid()) = user_id and public.is_family_access_allowed())',
      t || '_insert_own', t
    );
    execute format(
      'create policy %I on public.%I for update to authenticated
       using ((select auth.uid()) = user_id and public.is_family_access_allowed())
       with check ((select auth.uid()) = user_id and public.is_family_access_allowed())',
      t || '_update_own', t
    );
    execute format(
      'create policy %I on public.%I for delete to authenticated
       using ((select auth.uid()) = user_id and public.is_family_access_allowed())',
      t || '_delete_own', t
    );
  end loop;
end $$;

grant select, insert, update, delete on public.family_mental_accounts to authenticated;
grant select, insert, update, delete on public.family_mental_account_links to authenticated;
