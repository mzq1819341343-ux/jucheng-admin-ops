# 抓数管道（爱回收官网后台 → 聚成驾驶舱）

> **拍机堂已取消（2026-10-28 老板定案）**：权威源＝爱回收**官网 / 加盟商后台**（能出单量、分润的那套；2026-10-26 batch002 加盟商对账单即出自此后台）。拍机堂网页导出**取消、不做备用**，本目录不再有任何拍机堂抓数路径。
>
> **暂缓上传（2026-10-28 老板定案）**：`UPLOAD / APPLY` 默认 **false**（见 `pipeline_config.json` 与 `auto_fetch.py` 常量 `ALLOW_UPLOAD_PHASE=False`）。管道只落盘样本与 draft 产物，**绝不** merge 生产业务表、不跑覆盖正式页的 cockpit 更新。开启上传须另行验收并经督工点头。
>
> **登录入口（指令191，2026-10-28 老板纠正）**：加盟商后台唯一正确入口＝`https://franchise-admin.aihuishou.com/`。`afl-ump.aihuishou.com` 是爱分类运营平台（**错站，已弃用**，仅作注记）；指令186/187/190 在错站得出的「被拒 / 自有可证源耗尽」结论只适用于错站——保险柜 aihuishou.com 凭据从未在 franchise-admin 提交过。191 已在正确入口自登成功（首登即通），`need_user_login` 已清除；此后抓数一律走 franchise-admin。

## 管道状态（指令186，2026-10-28）

- **规范化管道已实现并自测跑通**：`auto_fetch.py --once` 可把官网后台原生导出（订单明细 37 列 / 对账单结算明细 / 列表金额）规范化为样本落盘，退出码区分 ok / need_user_login(2) / export_incomplete(3) / error(1)。
- **浏览器取数**（登录官网后台 → 原生导出）由主线会话在已登录态执行；导出文件交给本管道规范化。本脚本离线运行、不碰凭据、不代登录。
- **调度未启用**：crontab / launchd 示例均为注释态；启用须老板点头（见《待启用配置.md》），且启用后仍 UPLOAD=false、只抓取落盘。
- 样本：`~/Desktop/05_Muse督工/fetch_samples_186/`（全量，勿外传）；仓内脱敏结构副本在 `samples/186/`（字段名与结构真，金额与单号已打码）。
- **实时抓数已过关（指令191，2026-10-28）**：franchise-admin 自登 → 加盟商订单管理 → 旧机销售订单（完成时间 2026-10 全月筛选）原生导出 458 行 → `auto_fetch.py --once` 退出 0 落盘 `~/Desktop/05_Muse督工/fetch_samples_191/`（含原生文件 + manifest）；仓内脱敏副本在 `samples/191/`。2026-10 对账单尚未生成（月未结），待生成后另行补抓；调度仍未启用。

## 本目录内容

| 文件 | 说明 |
|---|---|
| pipeline_config.json | 管道配置：SOURCE=爱回收官网后台、UPLOAD=false、APPLY=false、样本目录、两店对照（万达 4594 / 友谊 5511） |
| auto_fetch.py | 管道实现（Python 标准库）：`--check` 自检 / `--once --input <导出文件或目录>` 规范化落盘 / `--status` 最近状态；状态机与退出码见脚本头注 |
| samples/186/ | 指令186 自测样本（脱敏）：manifest.json + fields.json + normalized_sample.csv（源＝batch002 万达 2026-09 对账单原生导出） |
| samples/191/ | 指令191 实时样本（脱敏）：manifest.json + fields.json + normalized_sample.csv（源＝franchise-admin 旧机销售订单 2026-10 原生导出 458 行，orders37 结构） |
| 待启用配置.md | 调度与启用配置（官网口径）：日 09:30 增量、每月 3 日 10:00 整月（Asia/Shanghai）；会话失效只记 need_user_login 并停手；含启用条件与动作清单（暂缓上传期间第 3 节并入步骤暂停） |
| crontab示例.txt / com.aihuishou.cockpit.plist | 调度示例（**全注释态、未安装未启用**）；启用前须老板点头 |
| 10月抓取运行单.md | 2026-10 抓取运行单（官网口径已更正，见文首更正条；暂缓上传期间只执行落盘部分） |
| check_2026-10-27.txt | 2026-10-27 旧版 `--check` 历史输出存档（当时口径，不再更新） |

## 启用后（未来，须另行定案）

上传开关打开并完成受控上传验收后，抓取成功才写入 fetch_logs，爱回收仪表盘与 /status 的「数据刷新」标记随之显示最近成功时间与状态；会话过期时只记录 need_user_login 并停手。**当前阶段不适用本节。**
