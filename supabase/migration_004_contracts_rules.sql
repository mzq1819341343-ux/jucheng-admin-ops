-- ============================================================
-- 聚成公司后台 · 迁移 004：两店合同台账 + 房租成本种子 + 工资规则落库（指令42 / 42b）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 数据来源：老板 2026-10-26 提供的两份合同关键数字与口述工资体系。
-- 红线不变：保证金只登记、不进月成本；万达电费按量预缴、实报实销，无读数不插行；
-- 友谊「联营扣点 8%」为合同避税写法、非实际成本，绝不入账（42b，老板确认）；
-- 友谊管理费未实际发生，不记；salary_monthly 不动任何行。
-- 万达分时段（42b）：上一期单价 341.67 元/㎡/月（微信与万达负责人王州确认）至 2026-11-27；
-- 新合同 2026-11-28 生效（单价 375.8 元/㎡/月），11 月整月按旧期口径，2026-12 起按新价拆分记账。
-- ============================================================

-- ---------- 合同台账（两店租赁 / 服务合同，只登记，不直接进成本） ----------
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

-- store_contracts：老板全权
drop policy if exists store_contracts_owner_all on public.store_contracts;
create policy store_contracts_owner_all on public.store_contracts
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

-- store_contracts：合伙人只读；staff 无策略 = 不可见
drop policy if exists store_contracts_partner_read on public.store_contracts;
create policy store_contracts_partner_read on public.store_contracts
  for select to authenticated
  using (public.current_role() = 'partner');

grant select, insert, update, delete on public.store_contracts to authenticated;

-- ---------- 工资规则：补「生效起始月」+ 全员可读 ----------
-- 生效起始月（'YYYY-MM'，可空 = 待老板补填；补填后支持新旧双版本规则回算）
alter table public.salary_rules
  add column if not exists effective_from text;

-- 工资「怎么算」对全员公开（/salary 顶部规则卡全角色可见）：
-- 全体已登录用户可读 salary_rules；写入仍仅 owner（salary_rules_owner_all 收口）。
drop policy if exists salary_rules_read_all on public.salary_rules;
create policy salary_rules_read_all on public.salary_rules
  for select to authenticated using (true);

-- ---------- 种子：合同台账两行 ----------
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

-- ---------- 种子：两店成本（store_costs） ----------
-- 友谊房租月摊：2026-02 ~ 2027-01（合同期），每月 7083.33
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

-- 万达上一期月租（房租口径，未拆分展示/物业）：2026-01 ~ 2026-11 共 11 行，每月 4100.04
-- 单价 341.67 元/㎡/月 × 12㎡，经微信与万达负责人王州确认；旧期至 2026-11-27。
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

-- 万达新合同期展示费（房租口径）：2026-12 ~ 2027-11 共 12 行，每月 3921.60
-- 新合同 2026-11-28 生效（单价 375.8 元/㎡/月）；2026-11 整月按旧期口径，2026-12 起新价。
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

-- 万达新合同期物业费（其他口径）：同上 12 个月，每月 588.00
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

-- 不插行：万达电费（utilities，自装智能电表按量预缴、实报实销，无读数不记）；
-- 友谊联营扣点 8% 为合同避税写法、非实际成本，绝不入账；友谊管理费未实际发生，不记；
-- 友谊 2026-01 为开业前，不插任何成本行。

-- ---------- 种子：工资体系规则一行（老板口述版） ----------
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
