# 聚成 · 运维与校准

本仓是 [jucheng-admin](https://github.com/mzq1819341343-ux/jucheng-admin) 的软拆结果：Supabase schema / migrations / seed、抓数管道、工资演算脚本，以及原 `docs/` 全量（指令草稿、验收、运维清单都在）。历史用 `git filter-repo` 按路径从单体仓 `main` 滤出。

- 原单体仓（保留，不删除，不改写 `main`）：https://github.com/mzq1819341343-ux/jucheng-admin
- 前端：https://github.com/mzq1819341343-ux/jucheng-admin-web

基线是单体仓 `main` 的 `4160298`（指令171 验收）。未合并分支不在本仓。原仓 [PR #2](https://github.com/mzq1819341343-ux/jucheng-admin/pull/2)（指令195 对账单校准）没有合并，其文件也没有拷入。

## 红线

- `docs/fetch-pipeline/pipeline_config.json` 里 `UPLOAD` 与 `APPLY` 保持 false。`docs/fetch-pipeline/auto_fetch.py` 的 `ALLOW_UPLOAD_PHASE = False`，配置被改成 true 也不会上传。
- 个人工资明细、姓名对应金额、密钥不入库。实时工资脚本的个人产物只写到本机 `--out-dir`。在职角色只保留 `scripts/payroll/active_roster.example.json` 这一份样例，不要复制到文档或算数脚本里。
- 前端仓 `lib/realtime-payroll.ts` 的汇总数字保持基线上的指令194 原文。这次拆分没有改那个文件。

## 目录

```
supabase/                 schema.sql、seed.sql、migration_002 … migration_013
docs/                     原 docs 全量：fetch-pipeline、验收、指令草稿、运维清单
scripts/payroll/          实时工资草案（默认读 JSON 名单；employees 为可选只读）
scripts/apply_realtime_payroll_draft.py  草案 upsert（默认 dry-run）
scripts/regression/       前端回归（脚本在本仓；app/、public/、lib/ 经 REG_WEB_ROOT 读前端仓）
scripts/salary-promotion/ 工资晋升 dry-run（不连库）
```

## Supabase 初始化（一次性）

1. 在 Supabase 新建项目。
2. SQL Editor 先执行 `supabase/schema.sql`，再执行 `supabase/seed.sql`。之后的结构变更按 `supabase/migration_*.sql` 的编号顺序执行。`migration_010_salary_effective_from.sql` 把工资规则生效月定为 2026-10，由老板在 SQL Editor 执行一次。`migration_011_salary_realtime_draft.sql`、`migration_012_employees.sql`、`migration_013_salary_rules_accessories_pct.sql` 都是当前线上库已经执行过的归档，本仓不代执行、不在变更里重跑。脚本默认不连库。
3. Settings → API 复制 Project URL 与 anon public key，填到前端仓 `.env.local`（模板在该仓 `.env.example`）。service role key 只留在服务端，不要加 `NEXT_PUBLIC_` 前缀，也不要提交。

## 抓数管道

细节见 `docs/fetch-pipeline/README.md` 与 `docs/fetch-pipeline/待启用配置.md`。

```bash
python3 docs/fetch-pipeline/auto_fetch.py --check
```

管道只落盘样本。crontab / launchd 示例是注释态，未安装。

## 实时工资草案

```bash
python3 scripts/payroll/realtime_payroll.py \
  --input <本机原生导出.xlsx> \
  --month 2026-10 \
  --active-roster <本机在职名单.json> \
  --accessories-input <本机配件导出.csv> \
  --out-dir <本机目录>
```

名单 JSON 里每人要有 `role`（上门或门店），可省略 `base_salary`（缺省 2000）。缺 role 拒绝出草案。没给 `--accessories-input` 或文件不存在时配件提成为 0，并在 assumptions 注明；不要把旧机订单列当成配件。可选只读在职表：`--roster-from=employees`（或 `ROSTER_SOURCE=employees`），只 GET `employment_status=在职` 的 `work_role` 与 `base_salary`，不写库。当前在职样例是 `scripts/payroll/active_roster.example.json`，不要把名单再复制进别的文件。

标准输出只打印汇总锚点。个人清单留在 `--out-dir`，不要提交。前端人效卡读 web 仓 `lib/realtime-payroll.ts`。在月结校准合并之前不要改那些汇总数。口径说明见 `docs/工资字段映射_新口径.md`。

把这份 JSON upsert 进 `salary_realtime_draft`：

```bash
python3 scripts/apply_realtime_payroll_draft.py --input <payroll JSON>
ALLOW_APPLY_REALTIME_DRAFT=true \
  python3 scripts/apply_realtime_payroll_draft.py --input <payroll JSON> --apply
```

默认 dry-run。`pipeline_config.json` 的 `APPLY` 保持 false，灌数脚本不改这个键，也不靠它放行。真正写库要 `ALLOW_APPLY_REALTIME_DRAFT=true` 和 `--apply` 同时成立，并且只处理当月、只写 `salary_realtime_draft`，冲突键是 `(month, person_name)`。写库另需环境变量 `SUPABASE_URL` 与 `SUPABASE_SERVICE_ROLE_KEY`（不进仓库）。

## 运维清单

- 回归：`docs/运维_回归清单.md`（脚本在本仓 `scripts/regression/`）
- 性能基线：`docs/运维_性能基线.md`

回归分两仓。脚本与 `npm run regression` 在本仓；Next 应用在 [jucheng-admin-web](https://github.com/mzq1819341343-ux/jucheng-admin-web)。先在前端仓启动演示构建，再回到本仓设定 `REG_WEB_ROOT`（前端检出，必填）与 `REG_BASE`（正在跑的服务）。线上未登录侧用 `REG_ONLINE`。

```bash
# 前端仓：演示构建并监听 :3100（生产 Vercel 构建不要带 ALLOW_DEMO）
cd /path/to/jucheng-admin-web
npm install
NEXT_PUBLIC_ALLOW_DEMO=1 npm run build
PORT=3100 npm run start

# 本仓
cd /path/to/jucheng-admin-ops
npm install
export REG_WEB_ROOT=/path/to/jucheng-admin-web   # 绝对路径，或相对当前目录
export REG_BASE=http://localhost:3100
npm run regression

# 只打线上（HTTP 目标默认 https://jcxj.cc；实物图仍读 REG_WEB_ROOT/public/help-shots）
export REG_ONLINE=https://jcxj.cc
npm run regression:online
```

`npm run shots` 把八张图写进 `$REG_WEB_ROOT/public/help-shots`，并改 `$REG_WEB_ROOT/app/help/page.tsx` 的图注日期。这些产物属于前端仓，要在 jucheng-admin-web 另开 PR，不要提交进本仓。未设置或空白的 `REG_WEB_ROOT` 会直接非 0 退出。

工资晋升样本校验不连库，在本仓即可跑：

```bash
node scripts/salary-promotion/dry-run.mjs
node scripts/salary-promotion/verify-expectations.mjs
```

字段口径对照见 `docs/工资字段映射_新口径.md`（192 为旧定案历史）。
