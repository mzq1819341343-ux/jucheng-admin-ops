-- ============================================================
-- 聚成公司后台 · 迁移 003：工资体系可见 + 两店成本进核算（指令41）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 本迁移只建骨架：不插任何工资 / 成本种子行，数字等老板提供后录入。
-- 红线不变：禁止工资原表整表上前台；salary_monthly 只存「员工可见口径」
-- 的月度构成（底薪/提成/扣款/实发），且 staff 只能读到自己已发布的行。
-- ============================================================

-- ---------- 工资体系规则（岗位/职级/底薪/提成规则，老板配置） ----------
create table if not exists public.salary_rules (
  id uuid primary key default gen_random_uuid(),
  title text not null, -- 岗位 / 职级名
  base_salary numeric(12, 2), -- 底薪（可空，待老板提供）
  commission_rules jsonb not null default '{}'::jsonb, -- 提成规则（结构化配置）
  note text,
  updated_by uuid references public.profiles (id),
  updated_at timestamptz not null default now()
);

-- ---------- 员工月度可见工资（按人按月，仅发布后员工可见） ----------
create table if not exists public.salary_monthly (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.profiles (id) on delete cascade,
  month text not null check (month ~ '^\d{4}-\d{2}$'), -- '2026-09'
  scope text not null check (scope in ('wanda', 'friendship')), -- 所属门店
  base_amount numeric(12, 2), -- 底薪部分
  commission_amount numeric(12, 2), -- 提成部分
  deduction_amount numeric(12, 2), -- 扣款部分
  net_amount numeric(12, 2) not null, -- 实发（员工可见的大数字）
  note text,
  published boolean not null default false, -- 发布后员工才可见
  created_by uuid references public.profiles (id),
  updated_at timestamptz not null default now(),
  unique (user_id, month)
);

-- ---------- 门店成本（两店分月：房租 / 水电 / 其他，进净利核算） ----------
create table if not exists public.store_costs (
  id uuid primary key default gen_random_uuid(),
  scope text not null check (scope in ('wanda', 'friendship')),
  month text not null check (month ~ '^\d{4}-\d{2}$'),
  cost_type text not null check (cost_type in ('rent', 'utilities', 'other')),
  amount numeric(12, 2) not null,
  note text,
  created_by uuid references public.profiles (id),
  updated_at timestamptz not null default now(),
  unique (scope, month, cost_type)
);

-- ---------- 门店月度工资汇总视图（给 partner：只有汇总，没有按人明细） ----------
-- 视图以属主权限执行，但 where 里用 current_role()/has_business_access() 做行过滤：
-- owner 全量；partner 须有爱回收业务授权才返回行；staff 恒为空。
-- 只聚合 published 行。
create or replace view public.salary_store_monthly_agg as
select
  scope,
  month,
  count(*)::int as headcount,
  sum(net_amount) as total_net,
  sum(base_amount) as total_base,
  sum(commission_amount) as total_commission
from public.salary_monthly
where published = true
  and (
    public.current_role() = 'owner'
    or (
      public.current_role() = 'partner'
      and public.has_business_access('aihuishou')
    )
  )
group by scope, month;

-- ---------- RLS ----------
alter table public.salary_rules enable row level security;
alter table public.salary_monthly enable row level security;
alter table public.store_costs enable row level security;

-- salary_rules：仅老板可读可写（体系配置）
drop policy if exists salary_rules_owner_all on public.salary_rules;
create policy salary_rules_owner_all on public.salary_rules
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

-- salary_monthly：老板全权
drop policy if exists salary_monthly_owner_all on public.salary_monthly;
create policy salary_monthly_owner_all on public.salary_monthly
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

-- salary_monthly：店员只能读自己已发布的行；partner 无本表策略（走汇总视图）
drop policy if exists salary_monthly_staff_read_own on public.salary_monthly;
create policy salary_monthly_staff_read_own on public.salary_monthly
  for select to authenticated
  using (
    public.current_role() = 'staff'
    and user_id = auth.uid()
    and published = true
  );

-- store_costs：老板全权可写
drop policy if exists store_costs_owner_all on public.store_costs;
create policy store_costs_owner_all on public.store_costs
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

-- store_costs：合伙人可读（呈现范围由应用层按授权控制）；staff 无策略 = 不可见
drop policy if exists store_costs_partner_read on public.store_costs;
create policy store_costs_partner_read on public.store_costs
  for select to authenticated
  using (public.current_role() = 'partner');

-- ---------- 授权（表级，行级由 RLS 收口） ----------
grant select, insert, update, delete on public.salary_rules to authenticated;
grant select, insert, update, delete on public.salary_monthly to authenticated;
grant select, insert, update, delete on public.store_costs to authenticated;
grant select on public.salary_store_monthly_agg to authenticated;

-- 不插任何种子行：工资体系与成本数字等老板提供后录入（页面为空态占位）。
