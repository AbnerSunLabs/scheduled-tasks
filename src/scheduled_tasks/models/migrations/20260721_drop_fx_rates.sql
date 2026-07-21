-- 下线汇率共享表：同步 job 已移除，驾驶舱暂不消费 fx_rates。
-- 幂等：表不存在时跳过。

do $$
begin
  if to_regclass('public.fx_rates') is not null then
    drop table public.fx_rates cascade;
  end if;
end
$$;
