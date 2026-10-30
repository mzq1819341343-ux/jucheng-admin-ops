-- ============================================================
-- migration_013 · salary_rules.commission_rules accessories_pct
-- 归档：本 UPDATE 已在线上执行（supabase_migrations.schema_migrations
-- version 20261030194412，name = salary_rules_accessories_pct）。
-- 本文件只把已落库语句进仓。不要在本变更里再执行，不要另写改 rules 的 SQL。
-- 正文自下一节起，与库内 statements[0] 一致。
-- 库内现行值：commission_rules.accessories_pct = 30，已无 accessories_tiers。
-- ============================================================

-- migration_013: accessories_tiers -> accessories_pct:30
update public.salary_rules
set
  commission_rules =
    (commission_rules - 'accessories_tiers')
    || jsonb_build_object('accessories_pct', 30),
  updated_at = now()
where commission_rules ? 'accessories_tiers'
   or coalesce((commission_rules->>'accessories_pct')::numeric, -1) is distinct from 30;
