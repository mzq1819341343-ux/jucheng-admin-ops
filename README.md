# 聚成 · 运维与校准

本仓是 [jucheng-admin](https://github.com/mzq1819341343-ux/jucheng-admin) 的软拆结果：Supabase schema / migrations / seed、抓数管道、工资演算脚本，以及原 `docs/` 全量（指令草稿、验收、运维清单都在）。历史用 `git filter-repo` 按路径从单体仓 `main` 滤出。

- 原单体仓（保留，不删除，不改写 `main`）：https://github.com/mzq1819341343-ux/jucheng-admin
- 前端：https://github.com/mzq1819341343-ux/jucheng-admin-web

基线是单体仓 `main` 的 `4160298`（指令171 验收）。未合并分支不在本仓。原仓 [PR #2](https://github.com/mzq1819341343-ux/jucheng-admin/pull/2)（指令195 对账单校准）没有合并，其文件也没有拷入。

## 红线

- `docs/fetch-pipeline/pipeline_config.json` 里 `UPLOAD` 与 `APPLY` 保持 false。`docs/fetch-pipeline/auto_fetch.py` 的 `ALLOW_UPLOAD_PHASE = False`，配置被改成 true 也不会上传。
- 个人工资明细、姓名对应金额、密钥不入库。实时工资脚本的个人产物只写到本机 `--out-dir`。
- 前端仓 `lib/realtime-payroll.ts` 的汇总数字保持基线上的指令194 原文。这次拆分没有改那个文件。

## 目录

```
supabase/                 schema.sql、seed.sql、migration_002 … migration_010
docs/                     原 docs 全量：fetch-pipeline、验收、指令草稿、运维清单
scripts/payroll/          实时工资草案（不连库）
scripts/regression/       前端回归（需原单体仓里的 Next 应用）
scripts/salary-promotion/ 工资晋升 dry-run（不连库）
```

## Supabase 初始化（一次性）

1. 在 Supabase 新建项目。
2. SQL Editor 先执行 `supabase/schema.sql`，再执行 `supabase/seed.sql`。之后的结构变更按 `supabase/migration_*.sql` 的编号顺序执行。`migration_010_salary_effective_from.sql` 把工资规则生效月定为 2026-10，由老板在 SQL Editor 执行一次。脚本不连库。
3. Settings → API 复制 Project URL 与 anon public key，填到前端仓 `.env.local`（模板在该仓 `.env.example`）。service role key 只留在服务端，不要加 `NEXT_PUBLIC_` 前缀，也不要提交。

## 抓数管道

细节见 `docs/fetch-pipeline/README.md` 与 `docs/fetch-pipeline/待启用配置.md`。

```bash
python3 docs/fetch-pipeline/auto_fetch.py --check
```

管道只落盘样本。crontab / launchd 示例是注释态，未安装。

## 实时工资草案（不写库、不入仓）

```bash
python3 scripts/payroll/realtime_payroll.py \
  --input <本机原生导出.xlsx> \
  --month 2026-10 \
  --active-roster <本机在职名单.json> \
  --out-dir <本机目录>
```

标准输出只打印汇总锚点。个人清单留在 `--out-dir`，不要提交。前端人效卡读 web 仓 `lib/realtime-payroll.ts`。在月结校准合并之前不要改那些汇总数。

## 运维清单

- 回归：`docs/运维_回归清单.md`（脚本在 `scripts/regression/`）
- 性能基线：`docs/运维_性能基线.md`

回归要在含有 Next 应用的目录里先启动演示构建（`NEXT_PUBLIC_ALLOW_DEMO=1 npm run build`，再 `PORT=3100 npm run start`）。这些 npm 脚本在前端仓已经卸下，原单体仓仍可按清单执行。

工资晋升样本校验不连库，在本仓即可跑：

```bash
node scripts/salary-promotion/dry-run.mjs
node scripts/salary-promotion/verify-expectations.mjs
```

字段口径对照见 `docs/工资字段映射_192.md`。
