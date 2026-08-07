-- 网格计算器策略保存表（配置 + 结果快照，非驾驶舱 grid_plans）
-- 目标库：Supabase Postgres
-- 幂等：可重复执行
-- 业务行由 stock-charts UI 写入；本仓仅 DDL/RLS

create table if not exists public.grid_strategies (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users (id) on delete cascade,
  name text not null,
  schema_version integer not null default 1,
  config jsonb not null,
  result_snapshot jsonb not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint grid_strategies_name_check
    check (char_length(btrim(name)) between 1 and 50),
  constraint grid_strategies_schema_version_check
    check (schema_version = 1),
  constraint grid_strategies_config_object_check
    check (jsonb_typeof(config) = 'object'),
  constraint grid_strategies_result_object_check
    check (jsonb_typeof(result_snapshot) = 'object')
);

comment on table public.grid_strategies is
  '网格计算器保存：命名配置与成功结果快照；非 grid_plans 交易执行账本；由 stock-charts 按 owner RLS 写入';

create unique index if not exists grid_strategies_user_name_uidx
  on public.grid_strategies (user_id, lower(btrim(name)));

create index if not exists grid_strategies_user_updated_idx
  on public.grid_strategies (user_id, updated_at desc);

alter table public.grid_strategies enable row level security;

revoke all on table public.grid_strategies
from public, anon, authenticated;

grant select, insert, update, delete
on public.grid_strategies
to authenticated;

drop policy if exists grid_strategies_select_own on public.grid_strategies;
create policy grid_strategies_select_own
  on public.grid_strategies
  for select
  to authenticated
  using ((select auth.uid()) = user_id);

drop policy if exists grid_strategies_insert_own on public.grid_strategies;
create policy grid_strategies_insert_own
  on public.grid_strategies
  for insert
  to authenticated
  with check ((select auth.uid()) = user_id);

drop policy if exists grid_strategies_update_own on public.grid_strategies;
create policy grid_strategies_update_own
  on public.grid_strategies
  for update
  to authenticated
  using ((select auth.uid()) = user_id)
  with check ((select auth.uid()) = user_id);

drop policy if exists grid_strategies_delete_own on public.grid_strategies;
create policy grid_strategies_delete_own
  on public.grid_strategies
  for delete
  to authenticated
  using ((select auth.uid()) = user_id);

-- 验收 SQL（应用后由人工 / MCP 只读执行）：
-- select column_name, data_type, is_nullable
-- from information_schema.columns
-- where table_schema = 'public' and table_name = 'grid_strategies'
-- order by ordinal_position;
--
-- select policyname, cmd, roles, qual, with_check
-- from pg_policies
-- where schemaname = 'public' and tablename = 'grid_strategies'
-- order by cmd;
--
-- select indexname, indexdef
-- from pg_indexes
-- where schemaname = 'public' and tablename = 'grid_strategies'
-- order by indexname;
--
-- select grantee, privilege_type
-- from information_schema.role_table_grants
-- where table_schema = 'public' and table_name = 'grid_strategies'
-- order by grantee, privilege_type;
