# 工资晋升 dry-run（指令59 A1）

把 migration_006 末尾注释的「晋升路径」做成**只读演算脚本**：员工账号到位后、
正式晋升前，先用它算清「哪些人能按姓名对上账号、同人同月两段汇总后将写入
`salary_monthly` 的行长什么样、谁没匹配上、谁重名有风险」，老板核对报告后再
按 migration_006 注释 SQL 正式执行（scope 由老板晋升时指定，晋升行先
`published=false`，核对无误再发布）。

## 零写入保证

- 脚本**不连数据库、不发网络请求**，只读一个本地 JSON 文件；
- 全文没有写库调用（可自检：`grep -nE '\.(insert|update|delete|upsert)\(' scripts/salary-promotion/dry-run.mjs` 应无命中）；
- 唯一的写动作是 `--out` 指定的本地 Markdown 报告文件（可选）。

## 用法

```bash
# 1) 样本（虚构数据）直接跑通：
node scripts/salary-promotion/dry-run.mjs

# 2) 真实数据：先在 Supabase 控制台 SQL Editor 依次导出两份 JSON
#    （结果区右键/下载为 JSON，或把结果粘贴成同结构文件）：
#    select id, display_name from public.profiles order by display_name;
#    select employee_name, month, section, base_amount, commission_amount,
#           deduction_amount, net_amount, matched_user_id
#      from public.salary_detail_import order by employee_name, month, section;
#    合成为 { "profiles": [...], "salary_detail_import": [...] } 一个文件后：
node scripts/salary-promotion/dry-run.mjs --input ~/导出.json --out ~/晋升演算报告.md
```

**真实姓名与金额只留在老板本地的导出文件与报告里，不进仓库**（本目录只有
虚构样本 sample-data.json）。

## 演算口径

1. 匹配：`salary_detail_import.employee_name` 与 `profiles.display_name`
   精确相等才算对上；明细行已回填 `matched_user_id` 的优先认领。
2. 同名多账号 → 列入「重名风险」，**不自动匹配**，须老板人工指定。
3. 无同名账号 → 列入「未匹配名单」，先建账号再晋升。
4. 汇总：同人同月 store/door 两段金额分别求和（与 migration_006 注释 SQL
   的 group by 口径一致）；分项合计与实发对不齐时只出「口径提示」不拦截
   （源表调整项带符号，属既有口径）。
5. scope（wanda / friendship）报告中一律标 `TODO（待老板指定）`，脚本不猜。

## 边界样本（指令62 A6，全虚构，预期与 2026-10-27 实跑逐项对位）

```bash
node scripts/salary-promotion/dry-run.mjs --input scripts/salary-promotion/sample-boundary.json
node scripts/salary-promotion/dry-run.mjs --input scripts/salary-promotion/sample-empty.json
```

| # | 边界 | 样本构造 | 实跑预期（已核对） |
|---|---|---|---|
| 1 | 同人同月仅一段（整月仅上门段） | 示例丁 2026-09 只有 door 行 | 正常晋升一行，底薪 0.00、段 = door，不因缺 store 段拦截 |
| 2 | 两端空格 vs 内部差异 | 账号名「 示例辛 」（两端空格）／明细名「示例 戊」（内部空格，账号为「示例戊」） | 两端空格 trim 后匹配成功；内部空格不容错 → 「示例 戊」进未匹配名单（全/半角差异同理，精确匹配不做模糊） |
| 3 | 净额为负（扣款大于应发） | 示例己：2000 + 300 − 2900 = −600 | 照常晋升，实发 −600.00；分项能对平则不出口径提示（负数不拦截） |
| 4 | 重名 + 已回填 matched_user_id | 示例庚同名两账号，明细行回填其一 id | 按回填 id 晋升（认领优先）；**同时**仍列在「重名风险」节——双列是现行口径：晋升前老板须人工确认回填 id 无误，不要只看一处 |
| 5 | 金额 2 位外尾差 | 示例丁 2026-08：底薪 100.006、实发 100.00 | 晋升行底薪显示 100.01，并出口径提示「分项合计 100.01 ≠ 实发 100.00」（对应真实导入的 3 位小数尾差类，只提示不拦截） |
| 6 | 空明细 | sample-empty.json：明细 0 行 | 全 0 报告（0 人 / 0 行），不报错不崩 |

零写入自检在本轮复跑仍无命中（grep 口径见上节）；原 sample-data.json 未动，
59 轮预期（5 行 / 3 人 → 晋升 2 行）复跑不变。
