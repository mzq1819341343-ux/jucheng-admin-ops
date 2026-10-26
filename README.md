# 聚成 · 公司业务总后台

「聚成」公司业务总后台：老板、合伙人、店员共用一套经营数据与报表。
标语：**聚天下英豪，成全球唯一**（公司在新疆）。

技术栈（全部免费额度，零额外付费）：

- 代码托管：GitHub
- 部署：Vercel（免费版，免服务器）
- 数据库 / 登录：Supabase（免费版 Postgres + Auth + RLS）
- 框架：Next.js（App Router + TypeScript）

## 红线（本仓库硬性约定）

- **禁止**把工资原表、个人工资明细、任何「个人姓名对应金额」放进仓库或前端；本站只存**汇总**数据（`monthly_metrics`）。
- **禁止**把任何密钥、密码、钱包信息写进代码；真实环境变量只填本地 `.env.local` 与 Vercel 控制台，仓库里只有 `.env.example` 占位。
- 爱回收自动抓取涉及后台登录时，只由老板本人登录，系统不存密码、不绕过验证。

## 本地运行

```bash
npm install
cp .env.example .env.local   # 填入自己的 Supabase URL 与 anon key
npm run dev                  # http://localhost:3000
npm run build                # 生产构建验证
```

未填 `.env.local` 时站点以**演示模式**运行：数据为爱回收驾驶舱 v3.3 汇总样例口径，
页面顶部可切换老板 / 合伙人 / 店员演示角色，直观看到权限差异（店员看不到净利与工资占比）。

## Supabase 初始化（老板操作，一次性）

1. 在 [Supabase 控制台](https://supabase.com) 新建项目（选离新疆较近的区域即可，如 Singapore；免费版）。
2. 打开项目 → SQL Editor，**按顺序**执行：
   1. `supabase/schema.sql`（建表、视图、RLS、自动建档触发器）
   2. `supabase/seed.sql`（门店与 2026-01~09 汇总种子，全部来自驾驶舱 v3.3 锁定口径）
3. 项目 → Settings → API，复制 `Project URL` 与 `anon public` key。
4. Authentication → Providers → Email 保持开启（邮箱 + 密码登录）。

### 角色与权限模型

| 角色 | 能看 | 不能看 |
| --- | --- | --- |
| 老板 owner | 全部汇总数据、成员管理、抓取日志 | —（个人明细源头不入库，老板也没有） |
| 合伙人 partner | 全部经营汇总（含工资成本、工资占分润比、人均、净利暂定） | 成员管理只读，不能建号/改角色 |
| 店员 staff | 单量、分润、上门单量、单均分润（仅 `v_ops_metrics` 视图） | 工资/净利类敏感列（RLS + 视图双层隔离，数据层即拿不到）、成员列表、抓取日志 |

敏感列隔离做法：`monthly_metrics` 本表只给 owner/partner 的 SELECT 策略；
staff 无策略命中，同时只被授予 `v_ops_metrics` 视图（只含月份、单量、分润、上门、单均）的 SELECT。

## 建号与设置角色（老板操作）

1. Supabase 控制台 → Authentication → Users → **Add user**：填成员邮箱与初始密码（或用 Invite 发邀请）。
2. 该用户首次登录后，`profiles` 会自动生成一行（默认 `staff`）。
3. 在 Table Editor → `profiles` 里把该行 `role` 改成 `owner` / `partner` / `staff`，
   `store_scope` 按需设 `all` / `wanda` / `friendship`，`display_name` 填显示名。
4. **第一个老板账号**：先按上面建号，再在 SQL Editor 执行 `schema.sql` 末尾注释里的
   owner 插入示例（把占位 UUID 换成该用户 UUID），把自己提升为 owner。
5. 成员重新登录后，页面即按新角色渲染。

## Vercel 部署（老板操作）

1. 把本仓库推到 GitHub（私有仓库）。
2. [Vercel 控制台](https://vercel.com) → Add New → Project → 选择该仓库导入（框架自动识别 Next.js）。
3. 在 Project → Settings → Environment Variables 添加：
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - （可选）`SUPABASE_SERVICE_ROLE_KEY`：仅服务端导入脚本用，**不要**加 `NEXT_PUBLIC_` 前缀。
4. Deploy，得到 `*.vercel.app` 预览/生产链接。
5. 自定义域名到位后：Vercel 项目 → Settings → Domains 添加域名，按提示在域名商处加 DNS 记录
   （子域名加 CNAME 指向 `cname.vercel-dns.com`；根域名按 Vercel 提示加 A 记录），解析生效后自动签 HTTPS 证书。

## 数据口径

- 爱回收汇总：2026-01~09 共 3,958 单 / 分润 416,661.32 元（爱回收驾驶舱 v3.3 已锁定台账）。
- 上门单量只看两店合计，不算单店业绩；门店行均为**到店口径**。
- 净利为「暂定」：到店分润 − 按到店分润比分摊的工资 − 对账单已知扣款，**未扣房租水电**；
  逐月无现成数的月份留空，不估算。目前仅 2026-09 有净利暂定（万达 3,290.59 / 友谊 1,580.27 元）。
- 工资只存汇总：逐月工资成本、固定/提成拆分与人效汇总在驾驶舱 v3.3 侧维护；
  本库 `monthly_metrics` 只取工资成本、工资占分润比、人均分润、人均单量四项月度汇总。
- 2026-09 的 1 月工资说明：1 月只有银行代发汇总（无拆分），已在种子 `note` 中注明。

## 零成本说明

- Vercel 免费版：个人项目、带宽与构建额度足够本后台现阶段使用。
- Supabase 免费版：500MB 数据库、5 万月活用户量级，本项目只有汇总表，远用不满；
  注意免费项目闲置 1 周会暂停，访问时自动唤醒（首屏可能慢几秒）。
- 不使用任何付费 API / 付费 UI 库 / 付费监控。

## 目录

```
app/                 页面：/（仪表盘） /login /reports/aihuishou /members /status
components/          KPI 卡、迷你趋势线、导航、演示角色切换
lib/supabase/        浏览器/服务端客户端
lib/data.ts          数据访问层（Supabase 优先，未配置时用与 seed 一致的兜底汇总）
supabase/schema.sql  建表 + RLS + 视图
supabase/seed.sql    v3.3 汇总种子数据
middleware.ts        路由登录保护（已配置 Supabase 时生效）
```
