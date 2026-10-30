-- ============================================================
-- migration_012 · employees 计薪名单
-- 归档：本 DDL 已在线上执行（supabase_migrations.schema_migrations
-- version 20261030194357，name = employees）。
-- 本文件只把已落库语句进仓。不要在本变更里再执行，不要另写 migration。
-- 正文自下一节「migration_012 employees」起，与库内 statements[0] 一致。
-- ============================================================

-- migration_012 employees
create table if not exists public.employees (
  id uuid primary key default gen_random_uuid(),
  person_name text not null,
  base_salary numeric(12, 2) not null default 2000
    check (base_salary >= 0),
  work_role text not null
    check (work_role in ('上门', '门店')),
  employment_status text not null default '在职'
    check (employment_status in ('在职', '离职')),
  user_id uuid references public.profiles (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (person_name)
);

create index if not exists employees_employment_status_idx
  on public.employees (employment_status);

create index if not exists employees_work_role_idx
  on public.employees (work_role);

create index if not exists employees_user_id_idx
  on public.employees (user_id)
  where user_id is not null;

alter table public.employees enable row level security;

drop policy if exists employees_owner_all on public.employees;
create policy employees_owner_all on public.employees
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

drop policy if exists employees_partner_select on public.employees;
create policy employees_partner_select on public.employees
  for select to authenticated
  using (public.current_role() = 'partner');

grant select, insert, update, delete on public.employees to authenticated;
