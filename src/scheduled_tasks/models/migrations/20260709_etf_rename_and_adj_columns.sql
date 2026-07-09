-- 可直接重复执行。双表并存必须硬失败，禁止静默给空/半成品新表加列而留下旧表数据。
do $$
begin
  -- 1) 双表并存 → 硬失败（手工测试 / 半次迁移常见坑）
  if to_regclass('public.etf_grid_pool_snapshots') is not null
     and to_regclass('public.etf_pool_snapshots') is not null then
    raise exception
      'both etf_grid_pool_snapshots and etf_pool_snapshots exist; manual migration required';
  end if;

  if to_regclass('public.etf_grid_daily') is not null
     and to_regclass('public.etf_daily') is not null then
    raise exception
      'both etf_grid_daily and etf_daily exist; manual migration required';
  end if;

  if to_regclass('public.etf_grid_valuation_snapshots') is not null
     and to_regclass('public.etf_valuation_snapshots') is not null then
    raise exception
      'both etf_grid_valuation_snapshots and etf_valuation_snapshots exist; manual migration required';
  end if;

  -- 2) 仅旧名存在 → RENAME；仅新名存在 → 跳过
  if to_regclass('public.etf_grid_pool_snapshots') is not null
     and to_regclass('public.etf_pool_snapshots') is null then
    alter table public.etf_grid_pool_snapshots rename to etf_pool_snapshots;
  end if;

  if to_regclass('public.etf_grid_daily') is not null
     and to_regclass('public.etf_daily') is null then
    alter table public.etf_grid_daily rename to etf_daily;
  end if;

  if to_regclass('public.etf_grid_valuation_snapshots') is not null
     and to_regclass('public.etf_valuation_snapshots') is null then
    alter table public.etf_grid_valuation_snapshots rename to etf_valuation_snapshots;
  end if;

  -- 3) 迁移后断言：三张新表必须存在，否则后续 ADD COLUMN 会写到错误对象或直接失败含糊
  if to_regclass('public.etf_pool_snapshots') is null then
    raise exception 'etf_pool_snapshots missing after rename step';
  end if;
  if to_regclass('public.etf_daily') is null then
    raise exception 'etf_daily missing after rename step';
  end if;
  if to_regclass('public.etf_valuation_snapshots') is null then
    raise exception 'etf_valuation_snapshots missing after rename step';
  end if;

  -- 4) 索引改名（旧索引存在且新名不存在时；双索引并存 raise）
  if to_regclass('public.etf_grid_pool_snapshots_snapshot_date_idx') is not null
     and to_regclass('public.etf_pool_snapshots_snapshot_date_idx') is not null then
    raise exception
      'both etf_grid_pool_snapshots_snapshot_date_idx and etf_pool_snapshots_snapshot_date_idx exist; manual migration required';
  end if;
  if to_regclass('public.etf_grid_pool_snapshots_snapshot_date_idx') is not null
     and to_regclass('public.etf_pool_snapshots_snapshot_date_idx') is null then
    alter index public.etf_grid_pool_snapshots_snapshot_date_idx
      rename to etf_pool_snapshots_snapshot_date_idx;
  end if;

  if to_regclass('public.etf_grid_daily_trade_date_idx') is not null
     and to_regclass('public.etf_daily_trade_date_idx') is not null then
    raise exception
      'both etf_grid_daily_trade_date_idx and etf_daily_trade_date_idx exist; manual migration required';
  end if;
  if to_regclass('public.etf_grid_daily_trade_date_idx') is not null
     and to_regclass('public.etf_daily_trade_date_idx') is null then
    alter index public.etf_grid_daily_trade_date_idx
      rename to etf_daily_trade_date_idx;
  end if;

  -- 5) 主键约束名：表 RENAME 后隐式 *_pkey 通常仍带旧前缀，去 grid 需显式改名
  if exists (
    select 1 from pg_constraint
    where conname = 'etf_grid_pool_snapshots_pkey'
      and conrelid = 'public.etf_pool_snapshots'::regclass
  ) then
    if exists (
      select 1 from pg_constraint where conname = 'etf_pool_snapshots_pkey'
    ) then
      raise exception
        'both etf_grid_pool_snapshots_pkey and etf_pool_snapshots_pkey exist; manual migration required';
    end if;
    alter table public.etf_pool_snapshots
      rename constraint etf_grid_pool_snapshots_pkey to etf_pool_snapshots_pkey;
  end if;

  if exists (
    select 1 from pg_constraint
    where conname = 'etf_grid_daily_pkey'
      and conrelid = 'public.etf_daily'::regclass
  ) then
    if exists (
      select 1 from pg_constraint where conname = 'etf_daily_pkey'
    ) then
      raise exception
        'both etf_grid_daily_pkey and etf_daily_pkey exist; manual migration required';
    end if;
    alter table public.etf_daily
      rename constraint etf_grid_daily_pkey to etf_daily_pkey;
  end if;

  if exists (
    select 1 from pg_constraint
    where conname = 'etf_grid_valuation_snapshots_pkey'
      and conrelid = 'public.etf_valuation_snapshots'::regclass
  ) then
    if exists (
      select 1 from pg_constraint where conname = 'etf_valuation_snapshots_pkey'
    ) then
      raise exception
        'both etf_grid_valuation_snapshots_pkey and etf_valuation_snapshots_pkey exist; manual migration required';
    end if;
    alter table public.etf_valuation_snapshots
      rename constraint etf_grid_valuation_snapshots_pkey
      to etf_valuation_snapshots_pkey;
  end if;
end $$;

-- sync_runs：结构化运行上下文（ADD COLUMN IF NOT EXISTS 本身幂等）
alter table public.sync_runs
  add column if not exists meta jsonb not null default '{}'::jsonb;

-- etf_daily：前/后复权 OHLC + 行情来源审计（均可空；上一段已断言表存在）
alter table public.etf_daily
  add column if not exists open_qfq numeric(18, 4),
  add column if not exists high_qfq numeric(18, 4),
  add column if not exists low_qfq numeric(18, 4),
  add column if not exists close_qfq numeric(18, 4),
  add column if not exists open_hfq numeric(18, 4),
  add column if not exists high_hfq numeric(18, 4),
  add column if not exists low_hfq numeric(18, 4),
  add column if not exists close_hfq numeric(18, 4),
  add column if not exists price_source text;
