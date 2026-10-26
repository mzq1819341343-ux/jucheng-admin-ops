-- ============================================================
-- 聚成公司后台 · 迁移 008：成员待邀请名单（指令59 A6 一期）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 定位：老板在 /members「待邀请名单」先登记意向（邮箱 + 意向角色 +
-- 意向业务授权），发邀仍走 Supabase 控制台 Authentication → Users
-- （本站不持有 service-role 密钥，不站内发信）；对方设密注册后，老板
-- 回名单点「一键对齐」，按邮箱匹配 profiles 后写入角色与业务授权。
-- 红线：仅老板可读写（其余角色无策略 = 整表不可见）；意向角色只允许
-- partner / staff，owner 不走名单登记，防权限误放大。
-- 状态：本文件随仓交付时尚未在生产库执行——待老板在 Supabase 控制台
-- SQL Editor 执行一次；执行前 /members 名单区优雅降级提示，不崩页。
-- ============================================================

create table if not exists public.member_invites (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  intended_role text not null default 'staff'
    check (intended_role in ('partner', 'staff')), -- 意向角色：不开放 owner
  intended_businesses text[] not null default '{}', -- 意向授权板块 id 数组（对齐时写入 user_business_access）
  note text,
  status text not null default 'pending'
    check (status in ('pending', 'aligned')),
  created_by uuid references public.profiles (id),
  created_at timestamptz not null default now(),
  aligned_at timestamptz,
  aligned_user_id uuid references public.profiles (id) -- 对齐到的账号（回执/追溯）
);

-- 同一邮箱只允许一条 pending 意向（aligned 历史行不拦）
create unique index if not exists member_invites_pending_email_uniq
  on public.member_invites (lower(email))
  where status = 'pending';

alter table public.member_invites enable row level security;

-- owner：全权（唯一策略；partner / staff 无策略 = 完全不可见）
drop policy if exists member_invites_owner_all on public.member_invites;
create policy member_invites_owner_all on public.member_invites
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

grant select, insert, update, delete on public.member_invites to authenticated;
