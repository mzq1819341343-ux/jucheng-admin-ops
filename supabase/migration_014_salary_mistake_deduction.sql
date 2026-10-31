-- ============================================================
-- migration_014 · salary_mistake_deduction 失误扣款明细
-- 归档：本 DDL 已在线上执行（supabase_migrations.schema_migrations
-- version 20261031072806，name = salary_mistake_deduction）。
-- 本文件只把已落库语句进仓。不要在本变更里再执行，不要灌数。
-- 源稿（自下一节起至文件末）整文件 SHA256
-- 174e38ebae9bf3a1be171730e9fc2412745acd63fff422d1661786b103f603ee
-- DDL 正文（自 create table 起，含行尾换行）SHA256
-- eff56d7a2e482d9047c6c63d8706d16fc931fbf07eb6a2097f202d7d36afd829
-- ============================================================

-- ============================================================
-- 聚成公司后台 · 迁移 014：失误扣款明细表 salary_mistake_deduction
-- 依据：产品｜过关·开工（2026-10-31）+ 后端短方案
-- 状态：已 apply（version 20261031072806）· 待推 ops 仓归档
-- 硬流程：方案 → 执行 → 检查 → 推送
-- 红线：本 migration 仅建新表/本表 RLS；零触及 salary_detail_import /
--       salary_realtime_draft / salary_monthly / salary_rules 等既有表结构与写权
--       禁双闸灌；不灌任何失误金额
-- Down：drop table if exists public.salary_mistake_deduction;
-- ============================================================

-- 失误扣款明细：人·月·多条（事由+金额）；合计 = SUM(amount)
create table if not exists public.salary_mistake_deduction (
  id uuid primary key default gen_random_uuid(),
  month text not null
    check (month ~ '^\d{4}-\d{2}$'),
  person_name text not null,
  reason text not null,
  amount numeric(12, 2) not null
    check (amount > 0),
  note text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  created_by uuid references public.profiles (id)
);

-- 同人同月允许多条；按人月聚合用
create index if not exists salary_mistake_deduction_month_person_idx
  on public.salary_mistake_deduction (month, person_name);

alter table public.salary_mistake_deduction enable row level security;

-- owner：全权（含写）；partner/staff：无 SELECT 明细策略 = 不可见、不可写
drop policy if exists salary_mistake_deduction_owner_all
  on public.salary_mistake_deduction;
create policy salary_mistake_deduction_owner_all
  on public.salary_mistake_deduction
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

grant select, insert, update, delete
  on public.salary_mistake_deduction to authenticated;
