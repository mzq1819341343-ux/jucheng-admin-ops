/**
 * 回归一键全跑（指令57 A8）：依次执行 audit-390、audit-staff、audit-online，
 * 任一失败整体退出码非 0。前两项需要本地演示服务（REG_BASE），第三项打线上。
 *
 * 前置（详见 docs/运维_回归清单.md）：
 *   NEXT_PUBLIC_ALLOW_DEMO=1 npm run build && PORT=3100 npm run start
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
  console.error("请先启动：NEXT_PUBLIC_ALLOW_DEMO=1 npm run build && PORT=3100 npm run start");
  process.exit(2);
}

let code = 0;
for (const f of ["audit-390.mjs", "audit-staff.mjs", "audit-online.mjs"]) {
  console.log(`\n===== ${f} =====`);
  const r = spawnSync(process.execPath, [`${here}/${f}`], { stdio: "inherit", env: process.env });
  if (r.status !== 0) code = 1;
}
console.log(`\n[run-all] ${code === 0 ? "全部通过" : "存在失败项，见上方 FAIL"}`);
process.exit(code);
