-- ============================================================
-- migration_010 · salary_rules 生效月落地（指令192）
-- 老板 2026-10-28 拍板：现行工资规则（通用工资体系·老板口述版）
-- effective_from = '2026-10' —— 自 2026-10 起用现行规则算实时工资；
-- 更早月份若需双版本回算，保留原口径不动。
-- 执行方式：老板在 Supabase SQL Editor 执行本文件一次即可。
-- （Muse 不代执行；脚本侧对应常量见 scripts/payroll/realtime_payroll.py
--   的 EFFECTIVE_FROM = "2026-10"。）
-- 幂等：重复执行结果不变。
-- ============================================================

update public.salary_rules
set effective_from = '2026-10'
where title = '通用工资体系（老板口述版）'
  and (effective_from is distinct from '2026-10');

-- 校验（执行后应返回一行，且 effective_from = '2026-10'）
select id, title, base_salary, effective_from, updated_at
from public.salary_rules
where title = '通用工资体系（老板口述版）';
