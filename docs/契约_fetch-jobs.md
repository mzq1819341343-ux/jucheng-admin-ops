# 契约 · fetch-jobs（文件态 + Mac runner）

工资页「数据抓取」只下发任务、只读上次结果。本仓没有 HTTP。任务是 Mac 上的 JSON 文件：后台（或本机 CLI）负责写入 `queued`，实抓在老板的 Mac 上做。

云端 agent、云主机、Vercel 的出口 IP 会被加盟后台拦住。那些环境只可以下发和读状态，不能登录，也不能把占位行数写成生产结果。

本文件对应前端 `jucheng-admin-web` 的 `lib/fetch-jobs.ts`（`parseFetchJobCreate` / `parseFetchJobLatest`）。页面卡片是 `components/FetchJobsCard.tsx`。

## 不做的事

- 不新增 migration，不写 `salary_realtime_draft`，不调用 `scripts/apply_realtime_payroll_draft.py`。
- 不把 `docs/fetch-pipeline/pipeline_config.json` 的 `UPLOAD` / `APPLY` 改成 true。本模块另有 `ALLOW_UPLOAD = False`、`ALLOW_APPLY = False`，写入时再强制这两个字段为 false。
- 不灌 2026-10，不读 `docs/fetch-pipeline/samples/`，不改底薪和员工占位。
- 不在本进程登录 `https://franchise-admin.aihuishou.com/`，不读保险柜，不把密码、Cookie、token 写进任务文件。
- 不安装 crontab / launchd。调度仍以 `docs/fetch-pipeline/待启用配置.md` 为准，当前未启用。

## 前端必须能解析的形状

POST `/api/ops/fetch-jobs`

```json
{ "month": "2026-11", "scope": "all" }
```

`scope` 只允许 `all` 或 `accessories`。`all` 含旧机和配件，`accessories` 只含配件。成功响应 HTTP 200 或 202，正文：

```json
{ "job_id": "fj_20261102T090000_a1b2c3" }
```

前端也认 `jobId`。没有 `job_id` 的 200 不算下发成功。

GET `/api/ops/fetch-jobs/latest?month=2026-11`

有记录时 HTTP 200：

```json
{
  "latest": {
    "job_id": "fj_20261102T090000_a1b2c3",
    "finished_at": "2026-11-02T09:00:06+08:00",
    "at": "2026-11-02T09:00:06+08:00",
    "updated_at": "2026-11-02T09:00:06+08:00",
    "old_machine": { "status": "success", "row_count": 2, "message": "占位，未登录加盟后台" },
    "accessories": { "status": "success", "row_count": 1, "message": "占位，未登录加盟后台" }
  }
}
```

这个月没有任何任务时，HTTP 200，正文是：

```json
{ "latest": null }
```

空月用 200 + `latest: null`。404 会被前端当成「任务接口未就绪」。

还在排队或执行时可以没有 `finished_at`。此时带 `updated_at` 和 `at`，腿的 `status` 用 `pending`（排队）或 `running`（已认领）。前端把这两种都显示成「进行中」。

`accessories` 范围的任务不带 `old_machine`。前端把缺的腿显示成「未返回」。

写出的字段（左）和前端额外接受的别名（右）：

| 本模块写出 | 前端也认 |
|---|---|
| `job_id` | `jobId` |
| `finished_at`，其次 `updated_at`、`at` | `created_at`、`last_at` |
| `old_machine` | `used`、`oldMachine` |
| `accessories` | `accessory` |
| `status` + `row_count` | `ok`、`rows`、`count` |
| `error` | `message` |

`status` 取值：`pending`、`running`、`success`、`failed`。成功也认 `ok`，失败也认 `error`。本模块对外写 `status` 和 `row_count`。

月份不合法或 `scope` 不是这两个值时，进程退出码 2，正文 `{"error":"月份或范围不正确"}`。Route Handler 要把退出码 2 变成 **HTTP 400**，并且 `Content-Type` 含 `json`。400 若不是 JSON，前端会把状态当成 0，显示「任务接口未就绪」。把这份错误 JSON 以 200 返回时，前端显示「下发没有返回任务号」。

## 本仓怎么调用

没有 Route。Route Handler 在能看见同一目录的机器上调用下面两个函数，把返回值原样当作响应体。

```python
import importlib.util
from pathlib import Path

path = Path("scripts/fetch_jobs.py")  # 按 ops 仓位置调整
spec = importlib.util.spec_from_file_location("fetch_jobs", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

try:
    body = mod.create_job(month, scope)          # {"job_id": "..."}
    # body = mod.latest_payload(month)           # {"latest": ... | None}
except mod.JobError as exc:
    # HTTP 400, Content-Type: application/json
    body = {"error": str(exc)}
```

等价 CLI（标准输出只有一个 JSON，退出码 0 成功，2 是月份/范围/状态错误）：

```bash
python3 scripts/fetch_jobs.py create --month 2026-11 --scope all
python3 scripts/fetch_jobs.py create --month 2026-11 --scope accessories
python3 scripts/fetch_jobs.py latest --month 2026-11
```

子进程要用参数数组，不要套 shell。`month` 和 `scope` 来自请求正文，原样放进参数。

目录优先级：`--jobs-dir` > 环境变量 `FETCH_JOBS_DIR` > 默认路径。

本模块不鉴权。接到 HTTP 时沿用工资页：未登录 401；POST 只允许老板，其他人 403；GET 老板和合伙人可以读。店员不进这张卡。

Vercel 上看不见 Mac 磁盘。在那边接这条 CLI 之前，页面会继续显示「任务接口未就绪」。本 PR 不在云端开这个路由，也不把队列搬进数据库。

## 落盘目录

默认：

```text
~/Desktop/05_Muse督工/fetch_jobs/
```

与现有样本目录 `~/Desktop/05_Muse督工/fetch_samples_186` 同级。任务文件留在这台 Mac 上，不入库。

```text
fetch_jobs/
  fj_20261102T090000_a1b2c3.json
  locks/fj_20261102T090000_a1b2c3.lock
```

`locks/` 是认领锁。`*.json.tmp` 是原子写入的临时文件。`latest` 只读文件名符合 `fj_YYYYMMDDTHHMMSS_` + 6 位十六进制的 JSON。坏文件跳过，不让整月查询失败。

## 任务文件

`create_job` 写入的 `scope=all` 例子：

```json
{
  "job_id": "fj_20261102T090000_a1b2c3",
  "month": "2026-11",
  "scope": "all",
  "status": "queued",
  "created_at": "2026-11-02T09:00:00+08:00",
  "updated_at": "2026-11-02T09:00:00+08:00",
  "started_at": null,
  "finished_at": null,
  "claimed_by": null,
  "dry": false,
  "entry_url": "https://franchise-admin.aihuishou.com/",
  "source": "franchise-admin",
  "old_machine": { "status": "pending", "row_count": null, "error": null, "message": null },
  "accessories": { "status": "pending", "row_count": null, "error": null, "message": null },
  "upload": false,
  "apply": false,
  "login": false
}
```

`scope=accessories` 时 `old_machine` 为 `null`。

| 字段 | 含义 |
|---|---|
| `status` | 任务：`queued` → `running` → `success` 或 `failed` |
| 腿 `status` | `pending` → `running` → `success` 或 `failed` |
| `created_at` | 下发时间。`latest` 用它选该月最近一次，含尚未跑完的 |
| `started_at` / `claimed_by` | 认领时写入 |
| `finished_at` | 回写结束时写入。成功和失败都写 |
| `dry` | `--dry` 或占位回写为 true。`complete` 默认 false |
| `login` | 恒为 false。表示这个 Python 进程没有登录 |
| `upload` / `apply` | 恒为 false。文件里若被改成 true，runner 不认领 |

每次 `create` 新开一条，不覆盖同月旧任务。`latest` 取该月 `created_at` 最大的一条（相同则比 `job_id`）。runner 则先做 `created_at` 最早的 `queued`，后点的任务不插队。

## 状态流转与认领

```text
queued --claim--> running --各腿都 success--> success
                     \----任一腿 failed------> failed
```

1. `claim` 用 `O_EXCL` 创建 `locks/<job_id>.lock`。锁已存在则返回「任务已被认领」，不改文件。
2. 读到的状态必须是 `queued`，且 `upload` / `apply` 都不是 true。然后把任务和范围内的腿写成 `running`，写入 `started_at`、`claimed_by`。锁留着。
3. `complete` 只接受 `running`。按结果回写每条腿，任务改为 `success` 或 `failed`，写 `finished_at`，删锁。
4. 范围内缺一条结果，该腿记 `failed` / `未回写`。范围外的腿直接拒绝，文件不动。
5. 结果里出现密码、Cookie、token，或 `upload` / `apply` 为 true，拒绝写入。

崩溃时：

- 锁已建立、状态仍是 `queued`：删掉对应锁后再 `claim`。
- 状态已是 `running`：可以直接 `complete`，结束时会删锁。不要对 `queued` 调用 `complete`。

`run` 不加 `--dry` 时退出码 2，不创建锁，不改任何任务文件。

## Mac runner

占位（不登录，用假行数走完状态机）：

```bash
python3 scripts/fetch_jobs.py run --dry
python3 scripts/fetch_jobs.py run --dry --job-id <job_id>
python3 scripts/fetch_jobs.py run --dry --fixture scripts/fetch_jobs_dry_rows.json
python3 scripts/fetch_jobs.py run --dry --fail --error 空文件
python3 scripts/fetch_jobs.py run --dry --fail-leg accessories --error 空文件
```

不加 `--fixture` 时旧机 2 行、配件 1 行。这两个数是占位，不是生产导出。`--dry` 成功的腿会带 `message`：`占位，未登录加盟后台`，页面显示在行数后面，避免把假行数看成实抓。

`--fail` 让本次范围内的腿都失败。`--fail-leg` 只让指定的那条腿失败，其余腿仍按占位行数成功，任务整体是 `failed`。

假数据文件只提供行数，例如：

```json
{
  "old_machine": { "row_count": 2 },
  "accessories": { "row_count": 1 }
}
```

不要把 2026-10 的导出或 `samples/191` 填进来。

真抓不走 `run`。Muse 在 Mac 上分两步调用，中间自己导出：

```bash
python3 scripts/fetch_jobs.py claim --job-id <job_id>
python3 scripts/fetch_jobs.py complete --job-id <job_id> \
  --results '{"old_machine":{"ok":true,"row_count":123},"accessories":{"ok":false,"row_count":0,"error":"空文件"}}'
```

`complete` 只把行数写回文件。它不会因此去登录，也不会 APPLY。`results` 也认 `status`（`success` / `failed`）和 `rows`。

### 真抓步骤（留给 Muse，本仓库不执行）

1. 只在老板的 Mac 上做，并且这台机器的浏览器能打开加盟后台。云端 IP 会被拦，云端进程停在下发。
2. `claim` 认领 `queued`。入口固定为 `https://franchise-admin.aihuishou.com/`。`afl-ump.aihuishou.com` 是已弃用的错站。
3. 用这台 Mac 保险柜里已有的 aihuishou.com 凭据登录。密码留在保险柜里，不写进任务文件，不向老板再要。登不上就停，把对应腿 `complete` 成 `failed`，`error` 写需要登录。不重试刷密码，不绕验证码。
4. `scope=all`：导出该月旧机订单，再导出配件进销存。`scope=accessories`：只导出配件。行数按当次导出计，不抄历史样本。
5. `complete` 回写。不点确认对账、申诉、提现、发起结算、提交。不跑工资灌数脚本，不改 `UPLOAD` / `APPLY`。

登录和停手的规矩与 `docs/fetch-pipeline/README.md` 一致。规范化样本仍由已有的 `auto_fetch.py` 在拿到导出文件之后处理；那一步不在本队列里，本队列也不替它打开上传。

## 冒烟

```bash
python3 scripts/smoke_fetch_jobs.py
```

或 `npm run fetch-jobs:smoke`。临时目录里覆盖：创建 `all` 与 `accessories`、空月 `latest: null`、有记录、无 `--dry` 时文件不变、认领与重复认领、成功/失败回写、假数据路径、带 `upload: true` 的文件被跳过。冒烟不写桌面默认目录，不改管道配置。
