/**
 * /help 截图刷新（指令57 A7 固化，原指令55 ad-hoc 截图脚本正规化）。
 *
 * 固定口径（与 public/help-shots 既有图一致，覆盖同名文件、/help 引用不变）：
 * - 桌面：视口 1440×1000、deviceScaleFactor 1.5、老板演示会话；
 *   overview / expenses / compare 为视口截图（首屏），
 *   salary-rule-preview / costs-utilities / dashboard-drill 为区块元素截图；
 * - 手机：视口 390×844、DSF 2、fullPage 整页（mobile-overview / mobile-expenses）；
 * - expenses 落在「未报销」筛选（?status=unreimbursed 直达入参），并滚动到
 *   筛选 chips 处构图（与既有图一致，激活态 chips 与当前筛选小计入镜）；
 * - dashboard-drill 先展开「门店成本」一级下钻与两店二级下钻再截 net-panel。
 *
 * 全部为演示数据截图，/help 图注已统一标注「示例截图（演示数据）」与刷新日期。
 * 指令58 A2：截图完成后自动把 app/help/page.tsx 八处「截图刷新：YYYY-MM-DD」
 * 更新为运行当日（Asia/Shanghai），图注日期不再靠手工对齐。
 * 指令60 A3：两张手机长图截后经 quantize_png.py 做 256 色量化（全分辨率，
 * 体积约 −60%）；PIL 不可用时保留原图并警告。
 * 用法：node scripts/regression/capture-help-shots.mjs（需本地演示服务已启动）
 */
import { launchBrowser, BASE } from "./lib.mjs";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "public", "help-shots");
const browser = await launchBrowser();

// ---------- 桌面 ----------
const dctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1.5 });
await dctx.addCookies([
  { name: "demo_role", value: "owner", url: BASE },
  { name: "demo_biz", value: "all", url: BASE },
]);

async function shot(name, fn) {
  const page = await dctx.newPage();
  await fn(page);
  console.log(`captured ${name}`);
  await page.close();
}
const go = (page, path) => page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 });

await shot("overview.png", async (page) => {
  await go(page, "/");
  await page.waitForTimeout(900); // 等英雄区 CountUp 落定
  await page.screenshot({ path: join(OUT, "overview.png") });
});
await shot("expenses.png", async (page) => {
  await go(page, "/expenses?status=unreimbursed");
  await page.waitForTimeout(600);
  // 构图与既有图一致：滚动到「按状态筛选」chips 处，让激活态与当前筛选小计入镜
  const chipsLabel = page.locator("text=按状态筛选").first();
  await chipsLabel.evaluate((el) => {
    const y = el.getBoundingClientRect().top + window.scrollY - window.innerHeight * 0.62;
    window.scrollTo(0, Math.max(0, y));
  });
  await page.waitForTimeout(350);
  await page.screenshot({ path: join(OUT, "expenses.png") });
});
await shot("compare.png", async (page) => {
  await go(page, "/business/aihuishou/compare");
  await page.waitForTimeout(600);
  await page.screenshot({ path: join(OUT, "compare.png") });
});
await shot("salary-rule-preview.png", async (page) => {
  await go(page, "/salary");
  const h = page.locator("h3", { hasText: "双版本预览" }).first();
  await h.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await h.locator("..").screenshot({ path: join(OUT, "salary-rule-preview.png") });
});
await shot("costs-utilities.png", async (page) => {
  await go(page, "/business/aihuishou/costs");
  const h = page.locator("h3", { hasText: "电费实报" }).first();
  await h.scrollIntoViewIfNeeded();
  await page.waitForTimeout(300);
  await h.locator("..").screenshot({ path: join(OUT, "costs-utilities.png") });
});
await shot("dashboard-drill.png", async (page) => {
  await go(page, "/business/aihuishou");
  const panel = page.locator("section.net-panel").first();
  await panel.scrollIntoViewIfNeeded();
  // 展开一级「门店成本」与两店二级下钻
  const summaries = panel.locator("details.drill > summary");
  await summaries.nth(0).click();
  await page.waitForTimeout(250);
  const subs = panel.locator("details.drill details.drill > summary");
  for (let i = 0; i < (await subs.count()); i++) {
    await subs.nth(i).click();
    await page.waitForTimeout(150);
  }
  await panel.screenshot({ path: join(OUT, "dashboard-drill.png") });
});
await dctx.close();

// ---------- 手机 ----------
const mctx = await browser.newContext({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
});
await mctx.addCookies([
  { name: "demo_role", value: "owner", url: BASE },
  { name: "demo_biz", value: "all", url: BASE },
]);
for (const [name, path] of [["mobile-overview.png", "/"], ["mobile-expenses.png", "/expenses"]]) {
  const page = await mctx.newPage();
  await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(700);
  await page.screenshot({ path: join(OUT, name), fullPage: true });
  console.log(`captured ${name}`);
  await page.close();
}
await mctx.close();
await browser.close();

// ---------- 手机长图量化后处理（指令60 A3） ----------
// 两张手机全页长图体积最大，截后做 256 色调色板量化（全分辨率不变，
// 实测 −58%/−62%，目检无色带）。依赖 python3 + PIL：不可用时保留原图
// 并打印警告，不让截图刷新整体失败。
try {
  const script = join(dirname(fileURLToPath(import.meta.url)), "quantize_png.py");
  const out = execFileSync(
    "python3",
    [script, join(OUT, "mobile-overview.png"), join(OUT, "mobile-expenses.png")],
    { encoding: "utf8" }
  );
  console.log(out.trim());
} catch (e) {
  console.log(`warn: 手机长图量化跳过（python3/PIL 不可用），保留原图：${e.message}`);
}
console.log(`done -> ${OUT}`);

// ---------- 图注日期自动对齐（指令58 A2） ----------
const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
const helpPath = join(OUT, "..", "..", "app", "help", "page.tsx");
const helpSrc = readFileSync(helpPath, "utf8");
const captionDates = helpSrc.match(/截图刷新：\d{4}-\d{2}-\d{2}/g) || [];
const helpNext = helpSrc.replace(/截图刷新：\d{4}-\d{2}-\d{2}/g, `截图刷新：${today}`);
if (helpNext !== helpSrc) writeFileSync(helpPath, helpNext);
console.log(
  `help captions: ${captionDates.length} 处「截图刷新」日期 -> ${today}${helpNext === helpSrc ? "（已是当日，无改动）" : "（已更新）"}`
);
