-- ============================================================
-- 聚成公司后台 · 迁移 018：monthly_sales_targets 人×店×月销售目标
-- 产品过关字段；店级目标=同店在职人目标合计（应用层汇总，表不存店空行）；未令不 apply；本文件先归档进仓。
-- 幂等：create table if not exists / drop policy if exists / create policy。
-- 不改 site_targets。不要在本变更里执行。
-- ============================================================

create table if not exists public.monthly_sales_targets (
  id uuid primary key default gen_random_uuid(),
  month text not null check (month ~ '^\d{4}-\d{2}$'),
  store_id text not null references public.stores (id),
  employee_id uuid not null references public.employees (id),
  recycle_target numeric(14, 2) not null default 0
    check (recycle_target >= 0),
  accessories_sales_target numeric(14, 2) not null default 0
    check (accessories_sales_target >= 0),
  note text,
  updated_by uuid references public.profiles (id),
  updated_at timestamptz not null default now(),
  unique (month, store_id, employee_id)
);

create index if not exists monthly_sales_targets_month_idx
  on public.monthly_sales_targets (month);

create index if not exists monthly_sales_targets_store_id_idx
  on public.monthly_sales_targets (store_id);

create index if not exists monthly_sales_targets_employee_id_idx
  on public.monthly_sales_targets (employee_id);

alter table public.monthly_sales_targets enable row level security;

drop policy if exists monthly_sales_targets_owner_all on public.monthly_sales_targets;
create policy monthly_sales_targets_owner_all on public.monthly_sales_targets
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

drop policy if exists monthly_sales_targets_partner_select on public.monthly_sales_targets;
create policy monthly_sales_targets_partner_select on public.monthly_sales_targets
  for select to authenticated
  using (public.current_role() = 'partner');

-- staff：无策略 = 不可见、不可写

grant select, insert, update, delete on public.monthly_sales_targets to authenticated;
