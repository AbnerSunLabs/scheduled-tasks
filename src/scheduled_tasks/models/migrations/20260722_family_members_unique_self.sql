-- 每用户仅允许一条 role=self，防止 ensureSelfMember 并发竞态插入重复「我」
-- 幂等：可重复执行

create unique index if not exists family_members_user_id_self_uidx
  on public.family_members (user_id)
  where role = 'self';

comment on index public.family_members_user_id_self_uidx is
  '每用户唯一 self 成员；与应用层 ensureSelfMember 幂等配合';
