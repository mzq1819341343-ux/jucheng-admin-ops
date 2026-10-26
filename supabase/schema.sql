-- ============================================================
-- 聚成公司后台 · Supabase Schema
-- 执行顺序：先在 Supabase 控制台 SQL Editor 跑本文件，再跑 seed.sql
-- 红线：本库只存数据。禁止建任何含个人工资明细、
-- 个人姓名对应金额、密钥/钱包信息的表。
-- ============================================================

-- ---------- 门店 ----------
create table if not exists public.stores (
id text primary key, -- 'wanda' | 'friendship'
name text not null -- '石河子万达店' | '石河子友谊商厦店'
);

-- ---------- 成员档案（与 auth.users 一一对应） ----------
create table if not exists public.profiles (
id uuid primary key references auth.users (id) on delete cascade,
role text not null default 'staff'
check (role in ('owner', 'partner', 'staff')), -- 老板 / 合伙人 / 店员
display_name text,
store_scope text not null default 'all'
check (store_scope in ('all', 'wanda', 'friendship')), -- 可见门店范围
created_at timestamptz not null default now()
);

-- ---------- 月度经营汇总（原 salary_agg 已并入本表，只存汇总） ----------
-- scope: 'combined' 两店合计 | 'wanda' 万达 | 'friendship' 友谊
-- 门店行（wanda/friendship）的 orders/profit 为；
-- 上门单量只看两店合计，只出现在 combined 行。
-- wage_cost / wage_ratio / per_capita_* / net_profit_provisional 为敏感汇总：
-- 仅 owner / partner 可通过 monthly_metrics 读取；staff 走 v_ops_metrics 视图。
create table if not exists public.monthly_metrics (
id bigint generated always as identity primary key,
month text not null check (month ~ '^\d{4}-\d{2}$'), -- '2026-09'
scope text not null check (scope in ('combined', 'wanda', 'friendship')),
orders integer, -- 单量（combined=总单量；门店行=到店单量）
profit numeric(14, 2), -- 分润（combined=总分润；门店行=到店分润）
door_orders integer, -- 上门单量（仅 combined 行有值）
avg_profit numeric(12, 2), -- 单均分润
wage_cost numeric(14, 2), -- 工资成本汇总（仅汇总，无个人明细）
wage_ratio numeric(6, 2), -- 工资占分润比（%）
per_capita_profit numeric(12, 2), -- 人均分润
per_capita_orders numeric(8, 2), -- 人均单量
net_profit_provisional numeric(14, 2), -- 净利暂定（未扣房租水电等，口径见 note）
note text,
created_at timestamptz not null default now(),
unique (month, scope)
);

-- ---------- 数据抓取 / 导入日志 ----------
create table if not exists public.fetch_logs (
id bigint generated always as identity primary key,
source text not null, -- 数据源标识，如 'aihuishou_cockpit_v3_3'
status text not null check (status in ('success', 'failed', 'pending', 'skipped')),
message text,
created_at timestamptz not null default now()
);

-- ---------- 当前登录用户角色（RLS 辅助函数，避免策略自引用递归） ----------
create or replace function public.current_role()
returns text
language sql
stable
security definer
set search_path = public
as $$
select role from public.profiles where id = auth.uid()
$$;

-- ---------- 运营视图（给 staff 用：只含非敏感列） ----------
-- 说明：monthly_metrics 本表对 staff 不授任何 SELECT（见下方 RLS），
-- staff 只能查这个视图。视图默认以视图属主权限执行（非 security_invoker），
-- 因此能读到本表数据，但只暴露 月份/单量/分润/上门/单均 五类非敏感字段，
-- 工资成本、工资占比、人均、净利等敏感列根本不在视图里，查不到。
create or replace view public.v_ops_metrics as
select
month,
scope,
orders,
profit,
door_orders,
avg_profit
from public.monthly_metrics;

-- ---------- RLS ----------
alter table public.stores enable row level security;
alter table public.profiles enable row level security;
alter table public.monthly_metrics enable row level security;
alter table public.fetch_logs enable row level security;

-- stores：所有已登录用户可读
drop policy if exists stores_read on public.stores;
create policy stores_read on public.stores
for select to authenticated using (true);

-- profiles：本人可读自己；owner 可读全部、可改全部（建号/改角色走 owner）
drop policy if exists profiles_read_self on public.profiles;
create policy profiles_read_self on public.profiles
for select to authenticated using (id = auth.uid());

drop policy if exists profiles_owner_all on public.profiles;
create policy profiles_owner_all on public.profiles
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

-- monthly_metrics：仅 owner / partner 可读本表；staff 无策略 = 读不到（含敏感列隔离）
drop policy if exists metrics_read_owner_partner on public.monthly_metrics;
create policy metrics_read_owner_partner on public.monthly_metrics
for select to authenticated
using (public.current_role() in ('owner', 'partner'));

-- 写入只走 service_role（导入脚本/后台任务），前端不直写
-- fetch_logs：owner / partner 可读
drop policy if exists fetch_logs_read on public.fetch_logs;
create policy fetch_logs_read on public.fetch_logs
for select to authenticated
using (public.current_role() in ('owner', 'partner'));

-- ---------- 授权（RLS 之上的表级权限） ----------
revoke all on public.monthly_metrics from authenticated;
grant select on public.monthly_metrics to authenticated; -- 实际行可见性由 RLS 策略决定（staff 命中不了策略）
grant select on public.v_ops_metrics to authenticated; -- staff 的唯一数据入口
grant select on public.stores to authenticated;
grant select, insert, update, delete on public.profiles to authenticated; -- 行级由 RLS 收口
grant select on public.fetch_logs to authenticated;

-- ---------- 新注册用户自动建档（默认 staff，老板再改角色） ----------
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
insert into public.profiles (id, role, display_name)
values (new.id, 'staff', coalesce(new.raw_user_meta_data ->> 'display_name', new.email))
on conflict (id) do nothing;
return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute function public.handle_new_user();

-- ============================================================
-- 首个 owner 建号步骤（老板本人操作）：
-- 1) 在 Supabase 控制台 Authentication → Users 用老板邮箱建号（或让老板先在 /login 注册页/邀请流程注册）；
-- 2) 复制该用户的 UUID，把下面语句的占位 UUID 换掉后执行：
--
-- insert into public.profiles (id, role, display_name, store_scope)
-- values ('00000000-0000-0000-0000-000000000000', 'owner', '老板', 'all')
-- on conflict (id) do update set role = 'owner', store_scope = 'all';
-- ============================================================
