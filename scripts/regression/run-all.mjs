/**
 * 回归一键全跑（指令57 A8，指令58 A5 扩围，指令59 A4 新增状态页一致性，
 * 指令61 A4 新增复盘导出一致性）：
 * 依次执行 audit-390、audit-staff、audit-anchors、audit-status-consistency、
 * audit-review-export、audit-online，任一失败整体退出码非 0。前五项需要本地演示
 * 服务（REG_BASE），第六项打线上。
 *
 * 前置（详见 docs/运维_回归清单.md）：
 *   在 jucheng-admin-web：NEXT_PUBLIC_ALLOW_DEMO=1 npm run build && PORT=3100 npm run start
 *   在本仓：REG_WEB_ROOT=<前端检出> REG_BASE=http://localhost:3100 npm run regression
 * 用法：node scripts/regression/run-all.mjs
 */
import { spawnSync } from "node:child_process";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { BASE } from "./lib.mjs";

const here = dirname(fileURLToPath(import.meta.url));

// 本地服务可达性预检：不可达时给出启动命令提示，避免把环境问题记成页面 FAIL。
try {
  const r = await fetch(BASE + "/login");
  if (r.status !== 200) throw new Error(`status=${r.status}`);
} catch (e) {
  console.error(`本地演示服务不可达（${BASE}）：${e.message}`);
  console.error("请先在 jucheng-admin-web 启动：NEXT_PUBLIC_ALLOW_DEMO=1 npm run build && PORT=3100 npm run start");
  console.error("本仓另需 REG_WEB_ROOT（前端检出）与 REG_BASE（默认 http://localhost:3100）。");
  process.exit(2);
}

let code = 0;
for (const f of ["audit-390.mjs", "audit-staff.mjs", "audit-anchors.mjs", "audit-status-consistency.mjs", "audit-review-export.mjs", "audit-online.mjs"]) {
  console.log(`\n===== ${f} =====`);
  const r = spawnSync(process.execPath, [`${here}/${f}`], { stdio: "inherit", env: process.env });
  if (r.status !== 0) code = 1;
}
console.log(`\n[run-all] ${code === 0 ? "全部通过" : "存在失败项，见上方 FAIL"}`);
process.exit(code);
