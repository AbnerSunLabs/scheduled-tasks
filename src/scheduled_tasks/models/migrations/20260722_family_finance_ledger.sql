-- 家庭财务：白名单对齐 + 成员/账本/快照/保单 + RLS
-- 目标库：Supabase Postgres（需已有 auth.users）
-- 幂等：可重复执行
-- 金额一律 numeric(18,2)；成员 FK ON DELETE RESTRICT
-- 白名单表/RPC 若已存在则对齐策略与授权，不破坏手工数据

-- ---------------------------------------------------------------------------
-- 访问白名单（Dashboard / service_role 维护；客户端不可读写）
-- ---------------------------------------------------------------------------
create table if not exists public.family_access_allowlist (
  id uuid primary key default gen_random_uuid(),
  github_user_id text not null,
  github_login text,
  note text,
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint family_access_allowlist_github_user_id_key unique (github_user_id)
);

comment on table public.family_access_allowlist is
  '家庭财务访问白名单；仅 Dashboard/service_role 维护，客户端不可读写';

alter table public.family_access_allowlist enable row level security;

revoke all on public.family_access_allowlist from anon, authenticated;

create or replace function public.is_family_access_allowed()
returns boolean
language sql
stable
security definer
set search_path to 'public'
as $$
  select exists (
    select 1
    from public.family_access_allowlist as a
    inner join auth.identities as i
      on i.provider = 'github'
     and i.provider_id = a.github_user_id
    where i.user_id = auth.uid()
      and a.is_active is true
  );
$$;

revoke all on function public.is_family_access_allowed() from public;
grant execute on function public.is_family_access_allowed() to authenticated;

-- ---------------------------------------------------------------------------
-- 家庭成员
-- ---------------------------------------------------------------------------
create table if not exists public.family_members (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null,
  role text not null,
  sort_order integer not null default 0,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint family_members_role_check
    check (role in ('self', 'spouse', 'child', 'other')),
  constraint family_members_name_nonempty check (char_length(trim(name)) > 0)
);

create index if not exists family_members_user_id_idx
  on public.family_members (user_id);

comment on table public.family_members is '家庭成员（每用户至少一条 self，由应用层幂等保证）';

-- ---------------------------------------------------------------------------
-- 当前账本条目
-- ---------------------------------------------------------------------------
create table if not exists public.family_ledger_items (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  member_id uuid references public.family_members (id) on delete restrict,
  side text not null,
  category text not null,
  name text not null,
  amount numeric(18, 2) not null,
  currency text not null default 'CNY',
  four_pot text,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint family_ledger_items_side_check
    check (side in ('asset', 'liability')),
  constraint family_ledger_items_category_check
    check (
      (side = 'asset' and category in (
        'cash', 'deposit', 'investment', 'property', 'vehicle', 'other_asset'
      ))
      or
      (side = 'liability' and category in (
        'mortgage', 'consumer_loan', 'credit_card', 'other_liability'
      ))
    ),
  constraint family_ledger_items_member_side_check
    check (
      (side = 'asset' and member_id is not null)
      or
      (side = 'liability' and member_id is null)
    ),
  constraint family_ledger_items_amount_nonneg check (amount >= 0),
  constraint family_ledger_items_currency_check check (currency = 'CNY'),
  constraint family_ledger_items_four_pot_check
    check (
      four_pot is null
      or four_pot in ('liquid', 'stable', 'long_term', 'insurance')
    ),
  constraint family_ledger_items_name_nonempty check (char_length(trim(name)) > 0)
);

create index if not exists family_ledger_items_user_id_idx
  on public.family_ledger_items (user_id);

comment on table public.family_ledger_items is
  '家庭资产负债活账；资产必挂成员，负债 member_id 为空（家庭债）';

-- ---------------------------------------------------------------------------
-- 快照
-- ---------------------------------------------------------------------------
create table if not exists public.family_snapshots (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  as_of_date date not null,
  total_assets numeric(18, 2) not null,
  total_liabilities numeric(18, 2) not null,
  net_worth numeric(18, 2) not null,
  note text,
  created_at timestamptz not null default now(),
  constraint family_snapshots_user_date_key unique (user_id, as_of_date),
  constraint family_snapshots_totals_nonneg
    check (total_assets >= 0 and total_liabilities >= 0)
);

create index if not exists family_snapshots_user_date_idx
  on public.family_snapshots (user_id, as_of_date desc);

comment on table public.family_snapshots is
  '家庭资产负债快照头；as_of_date 由应用层按 Asia/Shanghai 写入';

create table if not exists public.family_snapshot_items (
  id uuid primary key default gen_random_uuid(),
  snapshot_id uuid not null references public.family_snapshots (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  member_id uuid references public.family_members (id) on delete restrict,
  member_name text,
  side text not null,
  category text not null,
  name text not null,
  amount numeric(18, 2) not null,
  currency text not null default 'CNY',
  four_pot text,
  note text,
  constraint family_snapshot_items_side_check
    check (side in ('asset', 'liability')),
  constraint family_snapshot_items_amount_nonneg check (amount >= 0)
);

create index if not exists family_snapshot_items_snapshot_id_idx
  on public.family_snapshot_items (snapshot_id);

comment on table public.family_snapshot_items is '快照冻结明细（含当时成员名）';

-- ---------------------------------------------------------------------------
-- 保单
-- ---------------------------------------------------------------------------
create table if not exists public.insurance_policies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  member_id uuid not null references public.family_members (id) on delete restrict,
  policy_type text not null,
  insurer text,
  name text not null,
  coverage_amount numeric(18, 2) not null default 0,
  annual_premium numeric(18, 2) not null default 0,
  status text not null default 'active',
  start_date date,
  end_date date,
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint insurance_policies_type_check
    check (policy_type in (
      'life', 'critical_illness', 'medical', 'accident', 'property', 'other'
    )),
  constraint insurance_policies_status_check
    check (status in ('active', 'lapsed', 'pending')),
  constraint insurance_policies_amounts_nonneg
    check (coverage_amount >= 0 and annual_premium >= 0),
  constraint insurance_policies_name_nonempty check (char_length(trim(name)) > 0)
);

create index if not exists insurance_policies_user_id_idx
  on public.insurance_policies (user_id);

comment on table public.insurance_policies is
  '保单台账；保额/年缴不计入资产负债 KPI';

-- ---------------------------------------------------------------------------
-- RLS（复用 cockpit_apply_owner_rls；若函数不存在则内联创建）
-- ---------------------------------------------------------------------------
create or replace function public.cockpit_apply_owner_rls(p_table regclass)
returns void
language plpgsql
as $$
declare
  t text := p_table::text;
  short_name text := replace(t, 'public.', '');
  pol text;
begin
  execute format('alter table %s enable row level security', t);
  foreach pol in array array['select', 'insert', 'update', 'delete']
  loop
    execute format(
      'drop policy if exists %I on %s',
      short_name || '_' || pol || '_own',
      t
    );
  end loop;
  execute format(
    'create policy %I on %s for select to authenticated using ((select auth.uid()) = user_id)',
    short_name || '_select_own',
    t
  );
  execute format(
    'create policy %I on %s for insert to authenticated with check ((select auth.uid()) = user_id)',
    short_name || '_insert_own',
    t
  );
  execute format(
    'create policy %I on %s for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id)',
    short_name || '_update_own',
    t
  );
  execute format(
    'create policy %I on %s for delete to authenticated using ((select auth.uid()) = user_id)',
    short_name || '_delete_own',
    t
  );
end;
$$;

select public.cockpit_apply_owner_rls('public.family_members');
select public.cockpit_apply_owner_rls('public.family_ledger_items');
select public.cockpit_apply_owner_rls('public.family_snapshots');
select public.cockpit_apply_owner_rls('public.family_snapshot_items');
select public.cockpit_apply_owner_rls('public.insurance_policies');

grant select, insert, update, delete on
  public.family_members,
  public.family_ledger_items,
  public.family_snapshots,
  public.family_snapshot_items,
  public.insurance_policies
to authenticated;
