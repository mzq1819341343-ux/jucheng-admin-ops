-- ============================================================
-- 聚成公司后台 · 迁移 002：多业务板块 + 按业务授权（指令33）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 红线不变：只存汇总，禁止个人工资明细 / 姓名对应金额 / 密钥。
-- ============================================================

-- ---------- 业务板块 ----------
create table if not exists public.businesses (
id text primary key, -- 'aihuishou' | 'future' | 后续新业务
name text not null, -- '爱回收' | '更多业务'
status text not null default 'active'
check (status in ('active', 'coming_soon')),
sort int not null default 0,
created_at timestamptz not null default now()
);

insert into public.businesses (id, name, status, sort) values
  ('aihuishou', '爱回收', 'active', 1),
  ('future', '更多业务', 'coming_soon', 99)
on conflict (id) do nothing;

-- ---------- 用户-业务授权（一人可多业务，按板块单独授权） ----------
create table if not exists public.user_business_access (
user_id uuid not null references public.profiles (id) on delete cascade,
business_id text not null references public.businesses (id),
granted_by uuid references public.profiles (id),
created_at timestamptz not null default now(),
primary key (user_id, business_id)
);

-- ---------- 月度汇总补业务维度（存量行默认爱回收） ----------
alter table public.monthly_metrics
  add column if not exists business_id text not null default 'aihuishou';

-- ---------- 业务授权判断（RLS 辅助，owner 不受限） ----------
create or replace function public.has_business_access(bid text)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
select
  public.current_role() = 'owner'
  or exists (
    select 1 from public.user_business_access a
    where a.user_id = auth.uid() and a.business_id = bid
  )
$$;

-- ---------- 运营视图：带 business_id 且按业务授权过滤 ----------
create or replace view public.v_ops_metrics as
select
month,
scope,
orders,
profit,
door_orders,
avg_profit,
business_id
from public.monthly_metrics
where public.has_business_access(business_id);

-- ---------- RLS ----------
alter table public.businesses enable row level security;
alter table public.user_business_access enable row level security;

-- businesses：所有已登录用户可读（入口展示用）
drop policy if exists businesses_read on public.businesses;
create policy businesses_read on public.businesses
for select to authenticated using (true);

-- user_business_access：本人可读自己的；owner 可读全部、可增删改
drop policy if exists access_read_self on public.user_business_access;
create policy access_read_self on public.user_business_access
for select to authenticated using (user_id = auth.uid());

drop policy if exists access_owner_all on public.user_business_access;
create policy access_owner_all on public.user_business_access
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

-- monthly_metrics：原 owner/partner 策略追加业务校验（owner 不受限）
drop policy if exists metrics_read_owner_partner on public.monthly_metrics;
drop policy if exists metrics_read_business on public.monthly_metrics;
create policy metrics_read_business on public.monthly_metrics
for select to authenticated
using (
  public.current_role() in ('owner', 'partner')
  and public.has_business_access(business_id)
);

-- ---------- 授权（表级） ----------
grant select on public.businesses to authenticated;
grant select, insert, update, delete on public.user_business_access to authenticated; -- 行级由 RLS 收口
grant select on public.v_ops_metrics to authenticated;

-- ---------- 存量 owner 补爱回收授权行（幂等） ----------
insert into public.user_business_access (user_id, business_id)
select id, 'aihuishou' from public.profiles where role = 'owner'
on conflict do nothing;
