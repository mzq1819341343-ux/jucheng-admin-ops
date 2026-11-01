-- ============================================================
-- migration_016 · salary_realtime_draft 补贴/奖金列
-- 归档：本 DDL 已在线上执行（supabase_migrations.schema_migrations
-- version 20261101142056）。
-- 本文件只把已落库语句进仓。不要在本变更里再执行，不要灌数。
-- 源稿（自下一节起至文件末）整文件 SHA256
-- 3b02c9711e729e45ed43d9e9410858f9ade0f73e4a5ed61b6054e13aca9b2f61
-- DDL 正文（自加列语句 alter table 起至文件末，含行尾换行）SHA256
-- 5497d2e8e58e6f0a1cc1f0832fb387bf32350d11cd60dffa904f4a64d090ad99
-- ============================================================

-- ============================================================
-- 聚成公司后台 · 迁移 016：salary_realtime_draft 补贴/奖金列
-- 依据：产品｜老板原话过关 + 库/前端短方案过关（2026-11-01）
-- 状态：已 apply（version 20261101142056）· 待推 ops 仓归档
-- 硬流程：方案 → 执行 → 检查 → 推送
-- 红线：仅加两列；零改 employees / detail_import / monthly /
--       salary_mistake_deduction；不灌数；十月禁灌
-- Down：alter table public.salary_realtime_draft
--         drop column if exists subsidy_amount,
--         drop column if exists bonus_amount;
-- ============================================================

alter table public.salary_realtime_draft
  add column if not exists subsidy_amount numeric(12, 2) not null default 0
    check (subsidy_amount >= 0),
  add column if not exists bonus_amount numeric(12, 2) not null default 0
    check (bonus_amount >= 0);
