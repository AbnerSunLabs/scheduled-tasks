-- etf_pool：补 authenticated 只读 RLS + GRANT。
-- 背景：新库由 schema.sql 创建 etf_pool；旧版 20260710 只认 etf_pool_snapshots，
-- 导致新库缺策略/表权限。本脚本可重复执行。

do $$
begin
  if to_regclass('public.etf_pool') is null then
    raise notice 'public.etf_pool missing; skip authenticated read';
    return;
  end if;

  if exists (
    select 1
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname = 'cockpit_apply_authenticated_read'
  ) then
    perform public.cockpit_apply_authenticated_read('public.etf_pool');
  else
    alter table public.etf_pool enable row level security;
    drop policy if exists etf_pool_select_authenticated on public.etf_pool;
    create policy etf_pool_select_authenticated on public.etf_pool
      for select to authenticated using (true);
  end if;

  execute 'grant select on public.etf_pool to authenticated';
end $$;
