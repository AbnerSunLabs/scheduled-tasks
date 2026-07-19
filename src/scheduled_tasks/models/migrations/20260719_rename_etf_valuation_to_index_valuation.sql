-- etf_valuation 实际按 tracking_index_code 存指数估值；改为名实相符的 index_valuation。
-- ALTER TABLE RENAME 保留数据、GRANT、RLS、依赖视图与表注释；本迁移另行规范约束和 policy 名。

begin;

set local lock_timeout = '5s';
set local statement_timeout = '30s';

do $$
begin
  if to_regclass('public.etf_valuation') is not null
     and to_regclass('public.index_valuation') is not null then
    raise exception
      'both etf_valuation and index_valuation exist; manual migration required';
  end if;

  if to_regclass('public.etf_valuation') is not null then
    alter table public.etf_valuation rename to index_valuation;
  end if;

  if to_regclass('public.index_valuation') is null then
    raise exception 'index_valuation missing after rename step';
  end if;
end $$;

do $$
begin
  if exists (
    select 1
    from pg_constraint
    where conname = 'etf_valuation_pkey'
      and conrelid = 'public.index_valuation'::regclass
  ) then
    if exists (
      select 1
      from pg_constraint
      where conname = 'index_valuation_pkey'
        and conrelid = 'public.index_valuation'::regclass
    ) then
      raise exception
        'both etf_valuation_pkey and index_valuation_pkey exist; manual migration required';
    end if;
    alter table public.index_valuation
      rename constraint etf_valuation_pkey to index_valuation_pkey;
  end if;
end $$;

do $$
begin
  if exists (
    select 1 from pg_policy
    where polrelid = 'public.index_valuation'::regclass
      and polname = 'etf_valuation_select_anon'
  ) then
    if exists (
      select 1 from pg_policy
      where polrelid = 'public.index_valuation'::regclass
        and polname = 'index_valuation_select_anon'
    ) then
      raise exception
        'both etf_valuation_select_anon and index_valuation_select_anon exist';
    end if;
    alter policy etf_valuation_select_anon on public.index_valuation
      rename to index_valuation_select_anon;
  end if;

  if exists (
    select 1 from pg_policy
    where polrelid = 'public.index_valuation'::regclass
      and polname = 'etf_valuation_select_authenticated'
  ) then
    if exists (
      select 1 from pg_policy
      where polrelid = 'public.index_valuation'::regclass
        and polname = 'index_valuation_select_authenticated'
    ) then
      raise exception
        'both etf_valuation_select_authenticated and index_valuation_select_authenticated exist';
    end if;
    alter policy etf_valuation_select_authenticated on public.index_valuation
      rename to index_valuation_select_authenticated;
  end if;
end $$;

comment on table public.index_valuation is
  '指数估值最新快照（当日 PE + 5y/10y 均值）';

commit;
