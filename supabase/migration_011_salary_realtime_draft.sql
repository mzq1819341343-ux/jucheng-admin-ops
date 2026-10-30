-- ============================================================
-- migration_011 · salary_realtime_draft 实时工资草案
-- 归档：本 DDL 已在线上执行（supabase_migrations.schema_migrations
-- version 20261030040324，name = salary_realtime_draft）。
-- 本文件只把过关稿进仓。不要在本变更里再执行，不要另写 migration。
-- 正文与库内已落库语句逐字一致（正文 SHA256
-- b4cf161eeabaeb727b7d19ca41c81499969fafab70d9902bd53e13a94cf3988a，
-- 不含行尾换行）。幂等。唯一键 (month, person_name)。
-- 只建这一张表；不改其它表的权限与数据。
-- ============================================================

create table if not exists public.salary_realtime_draft (
  id uuid primary key default gen_random_uuid(),
  month text not null check (month ~ '^\d{4}-\d{2}$'),
  person_name text not null,
  user_id uuid references public.profiles (id),
  as_of timestamptz not null,
  base_amount numeric(12, 2) not null default 0,
  door_amount numeric(12, 2) not null default 0,
  online_amount numeric(12, 2) not null default 0,
  store_amount numeric(12, 2) not null default 0,
  accessories_amount numeric(12, 2) not null default 0,
  gross_amount numeric(12, 2) not null default 0,
  deduction_amount numeric(12, 2),
  net_amount numeric(12, 2),
  caliber text not null,
  source text not null default 'realtime_payroll',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (month, person_name)
);

create index if not exists salary_realtime_draft_month_idx
  on public.salary_realtime_draft (month);

create index if not exists salary_realtime_draft_user_id_idx
  on public.salary_realtime_draft (user_id)
  where user_id is not null;

alter table public.salary_realtime_draft enable row level security;

drop policy if exists salary_realtime_draft_owner_all on public.salary_realtime_draft;
create policy salary_realtime_draft_owner_all on public.salary_realtime_draft
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

drop policy if exists salary_realtime_draft_partner_select on public.salary_realtime_draft;
create policy salary_realtime_draft_partner_select on public.salary_realtime_draft
  for select to authenticated
  using (public.current_role() = 'partner');

grant select, insert, update, delete on public.salary_realtime_draft to authenticated;
