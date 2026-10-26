-- ============================================================
-- 聚成公司后台 · 迁移 006：工资实表明细导入表（指令47）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 定位：老板 2026-10-26 提供的 2026 年 1–9 月工资实表，按 v3.2 锁定口径
-- 解析后逐人逐段落此表，作为「工资实发合计」的明细来源，与
-- monthly_metrics 的月度工资汇总交叉校验（年回报分母）。
-- 红线：本表仅老板可见（RLS 只有 owner 策略，其余角色无任何策略）；
-- 明细数据由私有导入文件写入，禁止把导入 SQL 或个人金额提交进仓库。
-- 段别：section='store' 门店工资表段 / 'door' 上门工资表段；同一人同月
-- 两段各一行（unique(employee_name, month, section)）。
-- 李东浩与李冬浩为同一人（老板 2026-10-26 确认），employee_name 统一记「李冬浩」。
-- ============================================================

create table if not exists public.salary_detail_import (
  id uuid primary key default gen_random_uuid(),
  month text not null check (month ~ '^\d{4}-\d{2}$'), -- '2026-09'
  section text not null check (section in ('store', 'door')), -- 门店段 / 上门段
  employee_name text not null, -- 源表姓名（李东浩已合并为李冬浩）
  base_amount numeric(12, 2), -- 固定部分：底薪+全勤奖金+非交奖金（门店段；1 月无源数据为空）
  commission_amount numeric(12, 2), -- 提成部分（门店段=线上+门店+配件提成；上门段=利润提成+配件提成）
  deduction_amount numeric(12, 2), -- 调整部分：扣款+次卡未达标+公费/公费工本费，按源表符号
  net_amount numeric(12, 2), -- 实发：最终打款列（2 月合计 / 3 月实际工资 / 4–9 月去除公费实际打款 / 1 月银行代发）
  note text,
  matched_user_id uuid references public.profiles (id), -- 员工账号建立后回填，晋升 salary_monthly 的依据
  imported_at timestamptz not null default now(),
  unique (employee_name, month, section)
);

alter table public.salary_detail_import enable row level security;

-- owner：全权（唯一策略；partner / staff 无策略 = 完全不可见，符合红线）
drop policy if exists salary_detail_import_owner_all on public.salary_detail_import;
create policy salary_detail_import_owner_all on public.salary_detail_import
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

grant select, insert, update, delete on public.salary_detail_import to authenticated;

-- ============================================================
-- 晋升路径（仅注释示例，不在本迁移执行）：
-- 员工账号建立（邀请 → profiles 有其一行）后，老板按姓名把明细晋升为
-- 员工可见的 salary_monthly 行，并回填 matched_user_id。scope（门店归属）
-- 由老板晋升时按当时口径指定；晋升后先 published=false，核对无误再发布。
--
-- -- 1) 回填账号匹配（同名唯一时）：
-- update public.salary_detail_import d
-- set matched_user_id = p.id
-- from public.profiles p
-- where d.matched_user_id is null
--   and p.display_name = d.employee_name;
--
-- -- 2) 按人按月汇总两段后写入 salary_monthly（scope 以老板指定为准，下例为占位）：
-- insert into public.salary_monthly
--   (user_id, month, scope, base_amount, commission_amount, deduction_amount, net_amount, published)
-- select d.matched_user_id,
--        d.month,
--        '<wanda|friendship>'::text,
--        sum(d.base_amount),
--        sum(d.commission_amount),
--        sum(d.deduction_amount),
--        sum(d.net_amount),
--        false
-- from public.salary_detail_import d
-- where d.matched_user_id is not null
-- group by d.matched_user_id, d.month
-- on conflict (user_id, month) do nothing;
-- ============================================================
