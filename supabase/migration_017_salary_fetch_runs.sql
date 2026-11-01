-- ============================================================
-- 聚成公司后台 · 迁移 017：salary_fetch_runs 抓取任务态真源
-- 依据：产品过关 F1/F3/F4 + 字段对齐补丁开工令（2026-11-02）
-- 状态：已 apply（version 20261101185044）· 待推 ops 仓归档
-- 硬流程：方案 → 执行 → 检查 → 推送
-- 红线：仅建新表/本表 RLS；零改 draft/employees/mistake/detail_import；
--       不存订单明细；十月禁灌；upload/apply/login 恒 false
-- Down：drop table if exists public.salary_fetch_runs;
-- ============================================================

create table if not exists public.salary_fetch_runs (
  job_id text primary key,
  month text not null,
  scope text not null
    check (scope in ('all', 'accessories')),
  status text not null
    check (status in ('queued', 'running', 'success', 'failed')),
  started_at timestamptz,
  finished_at timestamptz,
  old_machine jsonb,
  accessories jsonb,
  claimed_by text,
  dry boolean not null default false,
  upload boolean not null default false,
  apply boolean not null default false,
  login boolean not null default false,
  message text,
  created_by uuid references public.profiles (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint salary_fetch_runs_gates_false_chk
    check (upload = false and apply = false and login = false)
);

create index if not exists salary_fetch_runs_month_created_at_idx
  on public.salary_fetch_runs (month, created_at desc);

create index if not exists salary_fetch_runs_status_active_idx
  on public.salary_fetch_runs (status)
  where status in ('queued', 'running');

alter table public.salary_fetch_runs enable row level security;

drop policy if exists salary_fetch_runs_owner_all on public.salary_fetch_runs;
create policy salary_fetch_runs_owner_all on public.salary_fetch_runs
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

drop policy if exists salary_fetch_runs_partner_select on public.salary_fetch_runs;
create policy salary_fetch_runs_partner_select on public.salary_fetch_runs
  for select to authenticated
  using (public.current_role() = 'partner');

-- staff：无策略 = 不可见、不可写

grant select, insert, update, delete on public.salary_fetch_runs to authenticated;
