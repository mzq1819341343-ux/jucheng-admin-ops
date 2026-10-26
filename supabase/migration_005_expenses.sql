-- ============================================================
-- 聚成公司后台 · 迁移 005：公司报销台账（指令44 块A）
-- 对已上线库执行本文件一次即可；全部语句幂等，可重复执行。
-- 口径：报销是公司已发生花费；未报销只表示尚未打款，不改变费用已发生的核算属性。
-- 种子仅 3 行 2026-10 示例，不进入 2026-09 已核算净利；标题均带「示例·」前缀，可直接删除。
-- ============================================================

create table if not exists public.expense_claims (
  id uuid primary key default gen_random_uuid(),
  claim_date date not null,
  title text not null,
  category text not null default '其他',
  amount numeric(12, 2) not null,
  scope text null check (scope in ('wanda', 'friendship', 'company')),
  claimant_name text,
  status text not null default 'unreimbursed'
    check (status in ('unreimbursed', 'reimbursed')),
  reimbursed_at timestamptz,
  note text,
  created_by uuid references public.profiles (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.expense_claims enable row level security;

-- owner：全权
drop policy if exists expense_claims_owner_all on public.expense_claims;
create policy expense_claims_owner_all on public.expense_claims
  for all to authenticated
  using (public.current_role() = 'owner')
  with check (public.current_role() = 'owner');

-- staff：只能看、建、改、删自己创建的行
drop policy if exists expense_claims_staff_read_own on public.expense_claims;
create policy expense_claims_staff_read_own on public.expense_claims
  for select to authenticated
  using (
    public.current_role() = 'staff'
    and created_by = auth.uid()
  );

drop policy if exists expense_claims_staff_insert_own on public.expense_claims;
create policy expense_claims_staff_insert_own on public.expense_claims
  for insert to authenticated
  with check (
    public.current_role() = 'staff'
    and created_by = auth.uid()
  );

drop policy if exists expense_claims_staff_update_own on public.expense_claims;
create policy expense_claims_staff_update_own on public.expense_claims
  for update to authenticated
  using (
    public.current_role() = 'staff'
    and created_by = auth.uid()
  )
  with check (
    public.current_role() = 'staff'
    and created_by = auth.uid()
  );

drop policy if exists expense_claims_staff_delete_own on public.expense_claims;
create policy expense_claims_staff_delete_own on public.expense_claims
  for delete to authenticated
  using (
    public.current_role() = 'staff'
    and created_by = auth.uid()
  );

-- partner：本表无策略（不可见明细），只读下方按月汇总视图。
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
  or (
    public.current_role() = 'staff'
    and created_by = auth.uid()
  )
group by to_char(claim_date, 'YYYY-MM');

grant select, insert, update, delete on public.expense_claims to authenticated;
grant select on public.expense_monthly_agg to authenticated;

-- ---------- 种子：3 行示例（created_by 留空 = 系统种子） ----------
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
