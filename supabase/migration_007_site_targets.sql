-- ============================================================
-- 聚成公司后台 · 迁移 007：月度目标（指令46 B8）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 口径：目标由老板设定；未设定即「未设定」，系统不预填任何数字。
-- metric：profit=当月分润 / net_profit=公司口径净利 / orders=当月单量。
-- ============================================================

create table if not exists public.site_targets (
  id uuid primary key default gen_random_uuid(),
  month text not null check (month ~ '^\d{4}-\d{2}$'),
  metric text not null check (metric in ('profit', 'net_profit', 'orders')),
  target_value numeric(14, 2) not null,
  note text,
  updated_by uuid references public.profiles (id),
  updated_at timestamptz not null default now(),
  unique (month, metric)
);

alter table public.site_targets enable row level security;

-- owner：全权
drop policy if exists site_targets_owner_all on public.site_targets;
create policy site_targets_owner_all on public.site_targets
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

-- 其余登录角色：只读（目标 vs 实际对全员透明，设定仅老板）
drop policy if exists site_targets_read_all on public.site_targets;
create policy site_targets_read_all on public.site_targets
  for select to authenticated
  using (true);

grant select, insert, update, delete on public.site_targets to authenticated;
