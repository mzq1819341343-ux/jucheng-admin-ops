/**
 * /status「10 月对账」卡 × docs/fetch-pipeline/10月抓取运行单.md 一致性审计
 * （指令59 A4）：同一事实两处落字（页面给老板看、运行单给执行看），逐字段
 * 比对防文案漂移。任一字段任一侧缺失/不一致 → FAIL 并指出字段与缺失侧。
 *
 * 比对方式：字段 = 一组关键 token，要求 token 在该侧全文中全部出现
 * （页面取老板演示会话渲染文本，运行单读仓库文件原文）。
 * 启用步骤数单独按 DOM 数：状态卡「一键启用步骤」必须恰好 4 步。
 * 指令74 A4 追加：真实模式空态分支（!fromDb && !demo）的源码机理
 * 静态守卫 + 演示渲染不误伤反证（见文件末段 A4 断言）。
 *
 * 用法：node scripts/regression/audit-status-consistency.mjs（本地演示服务）
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { launchBrowser, demoContext, Reporter, innerText, BASE, webPath } from "./lib.mjs";

const here = dirname(fileURLToPath(import.meta.url));
// 运行单留在本仓 docs/；状态页源码在前端仓。
const runbook = readFileSync(join(here, "..", "..", "docs", "fetch-pipeline", "10月抓取运行单.md"), "utf8");
const statusPath = webPath("app", "status", "page.tsx");
let statusSrc;
try {
  statusSrc = readFileSync(statusPath, "utf8");
} catch (e) {
  console.error(
    `错误：无法读取前端状态页 ${statusPath}。REG_WEB_ROOT 须指向含 app/status/page.tsx 的 jucheng-admin-web 检出。${e.message}`
  );
  process.exit(2);
}

// [字段名, status 侧 tokens, 运行单侧 tokens]
const FIELDS = [
  ["应抓区间（完成时间起止）", ["2026-10-01 00:00:00", "2026-10-25 23:59:59"], ["2026-10-01 00:00:00", "2026-10-25 23:59:59"]],
  ["对账单账单时间", ["账单时间 2026-10"], ["账单时间", "2026-10"]],
  ["两店店号", ["4594", "5511"], ["4594", "5511"]],
  ["基线单量", ["3,958"], ["3,958"]],
  ["基线分润", ["416,661.32"], ["416,661.32"]],
  ["跨月去重口径", ["跨月去重"], ["跨月去重"]],
  ["待登录状态", ["need_user_login"], ["need_user_login"]],
  ["7 单待核对", ["7 单", "待财务核对"], ["7 单", "待财务核对"]],
  ["启用步：首抓 asof", ["asof=2026-10-25"], ["asof", "2026-10-25"]],
  ["启用步：日增量时间", ["09:30"], ["09:30"]],
  ["启用步：月整月时间", ["3 日 10:00"], ["3 日 10:00"]],
  ["时区", ["Asia/Shanghai"], ["Asia/Shanghai"]],
];

const rep = new Reporter("audit-status");
const browser = await launchBrowser();
const ctx = await demoContext(browser, { role: "owner", viewport: { width: 1440, height: 1000 } });
const page = await ctx.newPage();
const resp = await page.goto(BASE + "/status", { waitUntil: "networkidle", timeout: 30000 }).catch(() => null);
rep.check("/status HTTP 200", !!resp && resp.status() === 200, resp ? `status=${resp.status()}` : "无响应");
const statusText = await innerText(page);

for (const [field, statusTokens, runbookTokens] of FIELDS) {
  const missS = statusTokens.filter((t) => !statusText.includes(t));
  rep.check(`字段「${field}」status 卡在位`, missS.length === 0, missS.length ? `缺：${missS.join(" / ")}` : "");
  const missR = runbookTokens.filter((t) => !runbook.includes(t));
  rep.check(`字段「${field}」运行单在位`, missR.length === 0, missR.length ? `缺：${missR.join(" / ")}` : "");
}

// —— 指令74 A4 裁定（修·方向①）断言 ——
// 真实模式空态分支无法用演示会话触发（演示恒走兜底、生产未登录恒 307），
// 故对源码机理做静态守卫，并以演示渲染反证不误伤演示轨：
// 源码必须消费 fromDb、以 !fromDb && !user.demo 为空态条件、含空态文案；
// 演示渲染必须仍见兜底日志行且不见空态文案。
rep.check("A4 源码记录节消费 fromDb（解构 getFetchLogs）", statusSrc.includes("const { logs, fromDb } = await getFetchLogs()"));
rep.check("A4 源码空态条件「!fromDb && !user.demo」在位", statusSrc.includes("!fromDb && !user.demo"));
rep.check("A4 源码空态文案「暂无抓取记录（管道已备未启用）」在位", statusSrc.includes("暂无抓取记录（管道已备未启用）"));
rep.check("A4 演示渲染兜底日志行在位「aihuishou_cockpit_v3_3」", statusText.includes("aihuishou_cockpit_v3_3"));
rep.check("A4 演示渲染不出现真实空态文案", !statusText.includes("暂无抓取记录（管道已备未启用）"));

const stepCount = await page.evaluate(() => {
  const heads = [...document.querySelectorAll("h2.section-title")];
  const h = heads.find((e) => e.textContent.includes("10 月对账"));
  const card = h?.nextElementSibling;
  return card ? card.querySelectorAll("ol.steps li").length : -1;
});
rep.check("一键启用步骤恰好 4 步", stepCount === 4, `count=${stepCount}`);

await browser.close();
process.exit(rep.finish());
