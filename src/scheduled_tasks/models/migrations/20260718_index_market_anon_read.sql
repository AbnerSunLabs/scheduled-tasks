-- 共享行情表：补 anon + authenticated 只读 SELECT policy。
-- 背景：指数仪表盘为公开投研页，需 anon key 可读；
-- 指数估值表曾 ENABLE RLS 但无 policy（客户端全拒）；
-- etf_daily / etf_pool 仅有 authenticated policy。
-- 幂等；不改表结构、不开放写。

create or replace function public.cockpit_apply_public_read(p_table regclass)
returns void
language plpgsql
as $$
declare
  t text := p_table::text;
  short_name text := replace(t, 'public.', '');
begin
  execute format('alter table %s enable row level security', t);
  execute format('drop policy if exists %I on %s', short_name || '_select_anon', t);
  execute format(
    'create policy %I on %s for select to anon using (true)',
    short_name || '_select_anon',
    t
  );
  execute format('drop policy if exists %I on %s', short_name || '_select_authenticated', t);
  execute format(
    'create policy %I on %s for select to authenticated using (true)',
    short_name || '_select_authenticated',
    t
  );
  execute format('grant select on %s to anon, authenticated', t);
end;
$$;

do $$
begin
  if to_regclass('public.index_valuation') is not null then
    perform public.cockpit_apply_public_read('public.index_valuation');
  elsif to_regclass('public.etf_valuation') is not null then
    -- 兼容 20260719 rename 之前执行本迁移的数据库。
    perform public.cockpit_apply_public_read('public.etf_valuation');
  end if;
  if to_regclass('public.index_industry_weights') is not null then
    -- 保留已有 "Allow public read ..."，再补规范命名 policy（幂等 drop/create）
    perform public.cockpit_apply_public_read('public.index_industry_weights');
  end if;
  if to_regclass('public.etf_daily') is not null then
    perform public.cockpit_apply_public_read('public.etf_daily');
  end if;
  if to_regclass('public.etf_pool') is not null then
    perform public.cockpit_apply_public_read('public.etf_pool');
  end if;
  if to_regclass('public.indices') is not null then
    perform public.cockpit_apply_public_read('public.indices');
  end if;
end $$;
