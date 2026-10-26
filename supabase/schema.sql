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
business_id text not null default 'aihuishou', -- 所属业务板块（多业务架构，见文末 002 节）
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

-- ---------- 业务板块（多业务架构，指令33；与 migration_002 保持一致） ----------
create table if not exists public.businesses (
id text primary key, -- 'aihuishou' | 'future' | 后续新业务
name text not null,
status text not null default 'active'
check (status in ('active', 'coming_soon')),
sort int not null default 0,
created_at timestamptz not null default now()
);

insert into public.businesses (id, name, status, sort) values
  ('aihuishou', '爱回收', 'active', 1),
  ('future', '更多业务', 'coming_soon', 99)
on conflict (id) do nothing;

-- ---------- 用户-业务授权（按板块单独授权） ----------
create table if not exists public.user_business_access (
user_id uuid not null references public.profiles (id) on delete cascade,
business_id text not null references public.businesses (id),
granted_by uuid references public.profiles (id),
created_at timestamptz not null default now(),
primary key (user_id, business_id)
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

-- ---------- 业务授权判断（owner 不受限；其他角色看授权表） ----------
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

-- ---------- 运营视图（给 staff 用：只含非敏感列） ----------
-- 说明：monthly_metrics 本表对 staff 不授任何 SELECT（见下方 RLS），
-- staff 只能查这个视图。视图默认以视图属主权限执行（非 security_invoker），
-- 因此能读到本表数据，但只暴露 月份/单量/分润/上门/单均 五类非敏感字段，
-- 工资成本、工资占比、人均、净利等敏感列根本不在视图里，查不到。
-- 视图带 business_id 并按业务授权过滤（未授权板块的行查不到）。
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
alter table public.stores enable row level security;
alter table public.profiles enable row level security;
alter table public.monthly_metrics enable row level security;
alter table public.fetch_logs enable row level security;
alter table public.businesses enable row level security;
alter table public.user_business_access enable row level security;

-- businesses：所有已登录用户可读（入口展示用）
drop policy if exists businesses_read on public.businesses;
create policy businesses_read on public.businesses
for select to authenticated using (true);

-- user_business_access：本人可读自己的；owner 可读全部、可增删改（成员页勾选走 owner）
drop policy if exists access_read_self on public.user_business_access;
create policy access_read_self on public.user_business_access
for select to authenticated using (user_id = auth.uid());

drop policy if exists access_owner_all on public.user_business_access;
create policy access_owner_all on public.user_business_access
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

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

-- monthly_metrics：仅 owner / partner 可读本表且须有该业务授权（owner 不受限）；
-- staff 无策略 = 读不到本表（含敏感列隔离），只能走 v_ops_metrics 视图
drop policy if exists metrics_read_owner_partner on public.monthly_metrics;
drop policy if exists metrics_read_business on public.monthly_metrics;
create policy metrics_read_business on public.monthly_metrics
for select to authenticated
using (
  public.current_role() in ('owner', 'partner')
  and public.has_business_access(business_id)
);

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
grant select on public.businesses to authenticated;
grant select, insert, update, delete on public.user_business_access to authenticated; -- 行级由 RLS 收口

-- 存量/首个 owner 补爱回收授权行（幂等）
insert into public.user_business_access (user_id, business_id)
select id, 'aihuishou' from public.profiles where role = 'owner'
on conflict do nothing;

-- ---------- 工资体系 + 两店成本（指令41；与 migration_003 保持一致） ----------
-- 只建骨架、不插种子行：工资规则与成本数字等老板提供后录入。
-- salary_monthly 只存员工可见口径的月度构成，禁止工资原表整表入库上前台。
create table if not exists public.salary_rules (
id uuid primary key default gen_random_uuid(),
title text not null, -- 岗位 / 职级名
base_salary numeric(12, 2), -- 底薪（可空）
commission_rules jsonb not null default '{}'::jsonb, -- 提成规则（结构化配置）
note text,
effective_from text, -- 生效起始月 'YYYY-MM'（指令42，可空 = 待老板补填，支持双版本回算）
updated_by uuid references public.profiles (id),
updated_at timestamptz not null default now()
);

create table if not exists public.salary_monthly (
id uuid primary key default gen_random_uuid(),
user_id uuid not null references public.profiles (id) on delete cascade,
month text not null check (month ~ '^\d{4}-\d{2}$'),
scope text not null check (scope in ('wanda', 'friendship')), -- 所属门店
base_amount numeric(12, 2), -- 底薪部分
commission_amount numeric(12, 2), -- 提成部分
deduction_amount numeric(12, 2), -- 扣款部分
net_amount numeric(12, 2) not null, -- 实发
note text,
published boolean not null default false, -- 发布后员工才可见
created_by uuid references public.profiles (id),
updated_at timestamptz not null default now(),
unique (user_id, month)
);

create table if not exists public.store_costs (
id uuid primary key default gen_random_uuid(),
scope text not null check (scope in ('wanda', 'friendship')),
month text not null check (month ~ '^\d{4}-\d{2}$'),
cost_type text not null check (cost_type in ('rent', 'utilities', 'other')), -- 房租/水电/其他
amount numeric(12, 2) not null,
note text,
created_by uuid references public.profiles (id),
updated_at timestamptz not null default now(),
unique (scope, month, cost_type)
);

-- 门店月度工资汇总视图（partner 专用：只有汇总，无按人明细；行过滤见 where）
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

alter table public.salary_rules enable row level security;
alter table public.salary_monthly enable row level security;
alter table public.store_costs enable row level security;

-- salary_rules：仅老板可读可写
drop policy if exists salary_rules_owner_all on public.salary_rules;
create policy salary_rules_owner_all on public.salary_rules
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

-- salary_monthly：老板全权；店员只能读自己已发布的行；partner 无本表策略（走汇总视图）
drop policy if exists salary_monthly_owner_all on public.salary_monthly;
create policy salary_monthly_owner_all on public.salary_monthly
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

drop policy if exists salary_monthly_staff_read_own on public.salary_monthly;
create policy salary_monthly_staff_read_own on public.salary_monthly
for select to authenticated
using (
  public.current_role() = 'staff'
  and user_id = auth.uid()
  and published = true
);

-- store_costs：老板全权可写；合伙人可读；staff 无策略 = 不可见
drop policy if exists store_costs_owner_all on public.store_costs;
create policy store_costs_owner_all on public.store_costs
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

drop policy if exists store_costs_partner_read on public.store_costs;
create policy store_costs_partner_read on public.store_costs
for select to authenticated
using (public.current_role() = 'partner');

grant select, insert, update, delete on public.salary_rules to authenticated;
grant select, insert, update, delete on public.salary_monthly to authenticated;
grant select, insert, update, delete on public.store_costs to authenticated;
grant select on public.salary_store_monthly_agg to authenticated;

-- ---------- 两店合同台账 + 房租成本种子 + 工资规则落库（指令42 / 42b；与 migration_004 保持一致） ----------
-- 保证金只登记、不进月成本；万达电费实报实销无读数不插行；友谊联营扣点 8% 为合同避税写法、
-- 非实际成本，绝不入账；友谊管理费未实际发生，不记。万达分时段：上一期 341.67 元/㎡/月（王州确认）
-- 至 2026-11-27，新合同 2026-11-28 生效、2026-12 起按新价（375.8 元/㎡/月）拆分记账。
create table if not exists public.store_contracts (
id uuid primary key default gen_random_uuid(),
scope text not null check (scope in ('wanda', 'friendship')),
title text not null, -- 合同名
party_a text, -- 甲方
party_b text, -- 乙方
location text, -- 点位
area_sqm numeric(10, 2), -- 面积（㎡）
term_start date,
term_end date,
monthly_fee numeric(12, 2), -- 月费（综合服务费 / 月摊口径）
fee_breakdown jsonb not null default '{}'::jsonb, -- 费用构成
payment_cycle text, -- 支付方式说明
deposit_total numeric(12, 2), -- 保证金合计（只登记，不进月成本）
note text,
updated_by uuid references public.profiles (id),
updated_at timestamptz not null default now(),
unique (scope, term_start)
);

alter table public.store_contracts enable row level security;

drop policy if exists store_contracts_owner_all on public.store_contracts;
create policy store_contracts_owner_all on public.store_contracts
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

drop policy if exists store_contracts_partner_read on public.store_contracts;
create policy store_contracts_partner_read on public.store_contracts
for select to authenticated
using (public.current_role() = 'partner');

grant select, insert, update, delete on public.store_contracts to authenticated;

-- 工资规则全员可读（「工资怎么算」规则卡全角色可见；写入仍仅 owner）
drop policy if exists salary_rules_read_all on public.salary_rules;
create policy salary_rules_read_all on public.salary_rules
for select to authenticated using (true);

-- 合同台账种子（两行）
insert into public.store_contracts
  (scope, title, party_a, party_b, location, area_sqm, term_start, term_end,
   monthly_fee, fee_breakdown, payment_cycle, deposit_total, note)
values
  ('wanda', '万达广场展示点位综合服务合同',
   '石河子市聚成云科网络科技有限公司', '石河子市万达广场商业管理有限公司',
   '万达广场 2F JY-BX-206', 12, '2026-11-28', '2027-11-27',
   4509.60, '{"展示费": 3921.60, "物业费": 588.00, "新单价": "375.8 元/㎡/月", "上一期单价": "341.67 元/㎡/月", "上一期月费": 4100.04, "上一期到期": "2026-11-27"}'::jsonb,
   '按季支付；首期 13979.76（2026-11-28~2027-02-28）', 14019.20,
   '签约主体：石河子市聚成云科网络科技有限公司 × 石河子市万达广场商业管理有限公司；月综合服务费（含税）4509.60 = 展示费 3921.60 + 物业费 588.00，年合计 54115.20；保证金 = 履约 9019.20 + 质量 5000，只登记不进月成本；电费自装智能电表按量预缴，按月实填；上一期单价 341.67 元/㎡/月经微信与万达负责人王州确认（上一期月费 4100.04，未拆分展示/物业，旧期至 2026-11-27）'),
  ('friendship', '壹加壹小吃街租赁合同',
   '壹加壹小吃街（甲方，出租方）', '张森',
   '友谊大厦负一层摊位 111', 22, '2026-02-01', '2027-01-31',
   7083.33, '{"年租金": 85000, "月摊": 7083.33}'::jsonb,
   '年租金一次付清', 13000.00,
   '合同署名乙方为张森，业务口径门店名为友谊；年租金 85000 已一次付清，按月摊 7083.33 记房租；合同中联营扣点 8% 为避税写法，非实际成本，不计入核算（老板 2026-10-26 确认）；除年租金外无其他费用（管理费未实际发生，不记）；另有免租至 2027-02-07；保证金 = 履约 10000 + 设备押金 3000，只登记不进月成本；甲方名称以合同原文为准（此处按老板提供口径登记）')
on conflict (scope, term_start) do nothing;

-- 两店成本种子：友谊房租月摊 2026-02~2027-01（每月 7083.33）
insert into public.store_costs (scope, month, cost_type, amount, note) values
  ('friendship', '2026-02', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-03', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-04', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-05', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-06', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-07', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-08', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-09', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-10', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-11', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2026-12', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）'),
  ('friendship', '2027-01', 'rent', 7083.33, '年租金 85000 月摊（合同期至 2027-01-31）')
on conflict (scope, month, cost_type) do nothing;

-- 两店成本种子：万达上一期月租（未拆分）2026-01~2026-11（每月 4100.04，王州确认）
insert into public.store_costs (scope, month, cost_type, amount, note) values
  ('wanda', '2026-01', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-02', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-03', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-04', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-05', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-06', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-07', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-08', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-09', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-10', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）'),
  ('wanda', '2026-11', 'rent', 4100.04, '上一期综合服务费（单价 341.67 元/㎡×12㎡，王州确认，未拆分展示/物业；旧期至 2026-11-27）')
on conflict (scope, month, cost_type) do nothing;

-- 两店成本种子：万达新合同期展示费（房租）2026-12~2027-11（每月 3921.60）
insert into public.store_costs (scope, month, cost_type, amount, note) values
  ('wanda', '2026-12', 'rent', 3921.60, '合同综合服务费之展示费（新合同 2026-11-28 生效，11 月整月按旧期口径，2026-12 起新价）'),
  ('wanda', '2027-01', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-02', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-03', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-04', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-05', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-06', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-07', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-08', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-09', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-10', 'rent', 3921.60, '合同综合服务费之展示费'),
  ('wanda', '2027-11', 'rent', 3921.60, '合同综合服务费之展示费')
on conflict (scope, month, cost_type) do nothing;

-- 两店成本种子：万达新合同期物业费（其他）同上 12 个月（每月 588.00）
insert into public.store_costs (scope, month, cost_type, amount, note) values
  ('wanda', '2026-12', 'other', 588.00, '合同综合服务费之物业费（新合同 2026-11-28 生效，2026-12 起新价）'),
  ('wanda', '2027-01', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-02', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-03', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-04', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-05', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-06', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-07', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-08', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-09', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-10', 'other', 588.00, '合同综合服务费之物业费'),
  ('wanda', '2027-11', 'other', 588.00, '合同综合服务费之物业费')
on conflict (scope, month, cost_type) do nothing;

-- 工资体系规则种子（老板口述版，生效起始月待补填）
insert into public.salary_rules (title, base_salary, commission_rules, note, effective_from)
select
  '通用工资体系（老板口述版）',
  2000,
  '{"base_breakdown": {"basic": 1000, "attendance": 500, "new_customer": 500, "note": "拉新非交口径待工资表模板到后核对，先按老板口述原样记录"}, "online_order_pct": 1, "store_tiers": [{"threshold": 30000, "pct": 3}, {"threshold": 60000, "pct": 4, "note": "阶梯取最高达成档"}], "accessories_tiers": [{"below": 6500, "pct": 20}, {"threshold": 6500, "pct": 30}, {"threshold": 10000, "pct": 40}], "door_profit_pct": 50}'::jsonb,
  '老板 2026-10-26 口述；历史规则几个月前改过，生效起始月待老板补填后支持双版本回算',
  null
where not exists (
  select 1 from public.salary_rules where title = '通用工资体系（老板口述版）'
);

-- ---------- 公司报销台账（指令44 块A；与 migration_005 保持一致） ----------
-- 未报销也属于已发生公司花费；种子为 2026-10 示例，不影响 2026-09 已核算净利。
create table if not exists public.expense_claims (
id uuid primary key default gen_random_uuid(),
claim_date date not null,
title text not null,
category text not null default '其他',
amount numeric(12, 2) not null,
scope text null check (scope in ('wanda', 'friendship', 'company')),
claimant_name text,
status text not null default 'unreimbursed' check (status in ('unreimbursed', 'reimbursed')),
reimbursed_at timestamptz,
note text,
created_by uuid references public.profiles (id),
created_at timestamptz not null default now(),
updated_at timestamptz not null default now()
);

alter table public.expense_claims enable row level security;

drop policy if exists expense_claims_owner_all on public.expense_claims;
create policy expense_claims_owner_all on public.expense_claims
for all to authenticated
using (public.current_role() = 'owner')
with check (public.current_role() = 'owner');

drop policy if exists expense_claims_staff_read_own on public.expense_claims;
create policy expense_claims_staff_read_own on public.expense_claims
for select to authenticated
using (public.current_role() = 'staff' and created_by = auth.uid());

drop policy if exists expense_claims_staff_insert_own on public.expense_claims;
create policy expense_claims_staff_insert_own on public.expense_claims
for insert to authenticated
with check (public.current_role() = 'staff' and created_by = auth.uid());

drop policy if exists expense_claims_staff_update_own on public.expense_claims;
create policy expense_claims_staff_update_own on public.expense_claims
for update to authenticated
using (public.current_role() = 'staff' and created_by = auth.uid())
with check (public.current_role() = 'staff' and created_by = auth.uid());

drop policy if exists expense_claims_staff_delete_own on public.expense_claims;
create policy expense_claims_staff_delete_own on public.expense_claims
for delete to authenticated
using (public.current_role() = 'staff' and created_by = auth.uid());

create or replace view public.expense_monthly_agg as
select
  to_char(claim_date, 'YYYY-MM') as month,
  coalesce(sum(amount), 0) as total,
  coalesce(sum(amount) filter (where status = 'reimbursed'), 0) as reimbursed_total,
  coalesce(sum(amount) filter (where status = 'unreimbursed'), 0) as unreimbursed_total,
  count(*)::int as count
from public.expense_claims
where
  public.current_role() = 'owner'
  or public.current_role() = 'partner'
  or (public.current_role() = 'staff' and created_by = auth.uid())
group by to_char(claim_date, 'YYYY-MM');

grant select, insert, update, delete on public.expense_claims to authenticated;
grant select on public.expense_monthly_agg to authenticated;

insert into public.expense_claims
  (claim_date, title, category, amount, scope, status, reimbursed_at, note, created_by)
select '2026-10-12', '示例·差旅', '差旅', 320.00, 'company', 'unreimbursed', null,
       '演示种子，老板可直接删除', null
where not exists (
  select 1 from public.expense_claims
  where title = '示例·差旅' and claim_date = '2026-10-12' and amount = 320.00
    and note = '演示种子，老板可直接删除'
);

insert into public.expense_claims
  (claim_date, title, category, amount, scope, status, reimbursed_at, note, created_by)
select '2026-10-03', '示例·采购', '采购', 158.50, 'wanda', 'reimbursed', '2026-10-05T00:00:00+08:00'::timestamptz,
       '演示种子，老板可直接删除', null
where not exists (
  select 1 from public.expense_claims
  where title = '示例·采购' and claim_date = '2026-10-03' and amount = 158.50
    and note = '演示种子，老板可直接删除'
);

insert into public.expense_claims
  (claim_date, title, category, amount, scope, status, reimbursed_at, note, created_by)
select '2026-10-18', '示例·物流', '物流', 96.00, 'friendship', 'unreimbursed', null,
       '演示种子，老板可直接删除', null
where not exists (
  select 1 from public.expense_claims
  where title = '示例·物流' and claim_date = '2026-10-18' and amount = 96.00
    and note = '演示种子，老板可直接删除'
);

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
