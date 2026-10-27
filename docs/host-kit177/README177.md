> Mac 侧使用：先 git pull，再 `cd docs/host-kit177 && npm i playwright && node measure177-host.mjs`
> 登录在弹出的系统 Chrome 里手动完成一次（脚本不含账号密码，登录态存本目录持久化资料夹）。
> 输出 measure177-host.json 按脚本自身目录落在同目录；把 JSON 发回主线，回填 docs/验收_指令177.md §2。

# 指令177 宿主测速工具包（Mac 系统 Chrome）

1. 在本文件夹打开终端：`npm i playwright`（只需一次；已装可跳过）。
2. 跑正式域：`node measure177-host.mjs` → 弹出的 Chrome 里手动登录 jcxj.cc 一次，回终端按 Enter，脚本自动测完（375 + PC 三路各 3 次）；测本地演示构建则：`node measure177-host.mjs --base=http://localhost:3100 --demo`。
3. 结果在 `measure177-host.json`（三路 median/max）+ `shot177-*.png`：把 375 与 PC 的中位/最慢秒数回填到仓内 `docs/验收_指令177.md` 的「375/PC 数字表」，或把 JSON 发回给 Muse 代填。
