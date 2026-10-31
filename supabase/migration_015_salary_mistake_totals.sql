-- ============================================================
-- migration_015 · salary_mistake_totals 失误扣款合计
-- 归档：本 DDL 已在线上执行（supabase_migrations.schema_migrations
-- version 20261031074338，name = salary_mistake_totals_security_definer）。
-- 本文件只把已落库语句进仓。不要在本变更里再执行，不要灌数。
-- 源稿（自下一节起至文件末）整文件 SHA256
-- 26fd75e5c054049616b8a2acf133faddbd6c611614315ea63914d3db07244b74
-- DDL 正文（自 create or replace function 起，含行尾换行）SHA256
-- 82c1b4f6992bfb13367876f5b34f2a12c3af3c9ecc02d992f53e46e3ede95269
-- ============================================================

-- ============================================================
-- 聚成公司后台 · 迁移 015：salary_mistake_totals（security definer 合计）
-- 依据：后端｜请确认并归档 015（2026-10-31）；web PR#8 依赖
-- 状态：已在库（version 20261031074338 · name salary_mistake_totals_security_definer）
--       · 待推 ops 仓归档
-- 硬流程：方案 → 执行 → 检查 → 推送
-- 红线：仅本函数；零触及 salary_detail_import / salary_realtime_draft /
--       salary_monthly / salary_mistake_deduction 表结构；不灌数
-- 说明：仅返回 person_name + SUM(amount)；函数内拒非 owner/partner；
--       revoke anon；partner 经此见合计、不见明细事由
-- Down：drop function if exists public.salary_mistake_totals(text);
-- ============================================================

create or replace function public.salary_mistake_totals(p_month text)
returns table (person_name text, mistake_total numeric)
language plpgsql
stable
security definer
set search_path = public
as $$
begin
  if public."current_role"() is distinct from 'owner'
     and public."current_role"() is distinct from 'partner' then
    raise exception 'forbidden' using errcode = '42501';
  end if;
  if p_month is null or p_month !~ '^\d{4}-\d{2}$' then
    raise exception 'bad month' using errcode = '22023';
  end if;
  return query
    select d.person_name, sum(d.amount)::numeric
    from public.salary_mistake_deduction d
    where d.month = p_month
    group by d.person_name;
end;
$$;

revoke all on function public.salary_mistake_totals(text) from public;
revoke all on function public.salary_mistake_totals(text) from anon;
grant execute on function public.salary_mistake_totals(text) to authenticated;
