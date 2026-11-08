-- ============================================================
-- 聚成公司后台 · 迁移 019：daily_person_store_sales 人×店×日销售
-- 归档：本 DDL 已在线上执行（supabase_migrations.schema_migrations
-- version 20261108175643，name = daily_person_store_sales，
-- project mhhjujgmkpkxeqexktbv）。
-- 本文件只把已落库语句进仓。不要在本变更里再执行，不要 apply。
-- 幂等：create table if not exists / create index if not exists /
-- drop policy if exists / create policy。
-- store_name 为磁盘原名（如 万达/友谊），不是 stores.id。
-- staff：无策略 = 不可见、不可写。
-- ============================================================

create table if not exists public.daily_person_store_sales (
  id uuid primary key default gen_random_uuid(),
  day date not null,
  month text not null check (month ~ '^\d{4}-\d{2}$'),
  person_name text not null,
  store_name text not null,  -- disk original names e.g. 万达/友谊
  recycle_store_amount numeric(14, 2) not null default 0
    check (recycle_store_amount >= 0),
  recycle_online_amount numeric(14, 2) not null default 0
    check (recycle_online_amount >= 0),
  accessories_sales numeric(14, 2) not null default 0
    check (accessories_sales >= 0),
  n_old_machine integer check (n_old_machine is null or n_old_machine >= 0),
  n_accessories integer check (n_accessories is null or n_accessories >= 0),
  updated_at timestamptz not null default now(),
  unique (day, person_name, store_name)
);

create index if not exists daily_person_store_sales_day_idx
  on public.daily_person_store_sales (day);

create index if not exists daily_person_store_sales_month_idx
  on public.daily_person_store_sales (month);

create index if not exists daily_person_store_sales_store_name_idx
  on public.daily_person_store_sales (store_name);

create index if not exists daily_person_store_sales_person_name_idx
  on public.daily_person_store_sales (person_name);

alter table public.daily_person_store_sales enable row level security;

drop policy if exists daily_person_store_sales_owner_all on public.daily_person_store_sales;
create policy daily_person_store_sales_owner_all on public.daily_person_store_sales
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

drop policy if exists daily_person_store_sales_partner_select on public.daily_person_store_sales;
create policy daily_person_store_sales_partner_select on public.daily_person_store_sales
  for select to authenticated
  using (public.current_role() = 'partner');

-- staff：无策略 = 不可见、不可写

grant select, insert, update, delete on public.daily_person_store_sales to authenticated;
