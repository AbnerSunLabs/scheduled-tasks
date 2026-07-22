-- 家庭财务业务表 RLS：在 owner 策略上叠加白名单 RPC
-- 幂等：先 drop 再 create

do $$
declare
  t text;
  tables text[] := array[
    'family_members',
    'family_ledger_items',
    'family_snapshots',
    'family_snapshot_items',
    'insurance_policies'
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
