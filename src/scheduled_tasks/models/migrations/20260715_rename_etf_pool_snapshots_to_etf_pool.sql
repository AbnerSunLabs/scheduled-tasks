-- etf_pool_snapshots → etf_pool：语义是「当前池主数据」，不是历史快照。
-- 可重复执行；双表并存硬失败。
do $$
begin
  if to_regclass('public.etf_pool_snapshots') is not null
     and to_regclass('public.etf_pool') is not null then
    raise exception
      'both etf_pool_snapshots and etf_pool exist; manual migration required';
  end if;

  if to_regclass('public.etf_pool_snapshots') is not null
     and to_regclass('public.etf_pool') is null then
    alter table public.etf_pool_snapshots rename to etf_pool;
  end if;

  if to_regclass('public.etf_pool') is null then
    raise exception 'etf_pool missing after rename step';
  end if;

  -- 索引
  if to_regclass('public.etf_pool_snapshots_snapshot_date_idx') is not null
     and to_regclass('public.etf_pool_snapshot_date_idx') is not null then
    raise exception
      'both etf_pool_snapshots_snapshot_date_idx and etf_pool_snapshot_date_idx exist; manual migration required';
  end if;
  if to_regclass('public.etf_pool_snapshots_snapshot_date_idx') is not null
     and to_regclass('public.etf_pool_snapshot_date_idx') is null then
    alter index public.etf_pool_snapshots_snapshot_date_idx
      rename to etf_pool_snapshot_date_idx;
  end if;

  -- 主键约束名
  if exists (
    select 1 from pg_constraint
    where conname = 'etf_pool_snapshots_pkey'
      and conrelid = 'public.etf_pool'::regclass
  ) then
    if exists (
      select 1 from pg_constraint where conname = 'etf_pool_pkey'
    ) then
      raise exception
        'both etf_pool_snapshots_pkey and etf_pool_pkey exist; manual migration required';
    end if;
    alter table public.etf_pool
      rename constraint etf_pool_snapshots_pkey to etf_pool_pkey;
  end if;

  -- RLS 策略名（表 RENAME 后策略仍挂在新表上，仅改名）
  if exists (
    select 1 from pg_policy
    where polname = 'etf_pool_snapshots_select_authenticated'
      and polrelid = 'public.etf_pool'::regclass
  ) then
    if exists (
      select 1 from pg_policy
      where polname = 'etf_pool_select_authenticated'
        and polrelid = 'public.etf_pool'::regclass
    ) then
      raise exception
        'both etf_pool_snapshots_select_authenticated and etf_pool_select_authenticated exist; manual migration required';
    end if;
    alter policy etf_pool_snapshots_select_authenticated on public.etf_pool
      rename to etf_pool_select_authenticated;
  end if;
end $$;
