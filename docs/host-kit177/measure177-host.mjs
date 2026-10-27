/**
 * 指令177 宿主测速工具包：在老板/督工的 Mac 上用系统 Chrome 跑 375 + PC 点击导航实测。
 * 前置：本目录执行 `npm i playwright`（或全局已装 playwright）；系统已装 Google Chrome。
 * 用法：
 *   node measure177-host.mjs                          → 测 https://jcxj.cc（有头窗口，先手动登录一次，登录态存 ./pw-profile177 复用）
 *   node measure177-host.mjs --base=http://localhost:3100 --demo   → 测本地演示构建（自动写 demo 会话 Cookie，无头）
 * 口径（同 170/173/175）：点顶栏/汉堡链接 → 目标页主元素可交互；375×667（DSF2、触控、汉堡动线）与 PC 1440×900 各跑 --runs 次（默认 3）。
 * 输出：measure177-host.json + shot177-{overview,expenses,salary}.png（375 截图）。
 * 本脚本不含任何账号/密码；登录由运行人在打开的 Chrome 里自己完成。
 */
import { writeFileSync } from "node:fs";
import { createInterface } from "node:readline";
import { chromium } from "playwright";

const args = process.argv.slice(2);
const opt = (k, d) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : d; };
const BASE = opt("--base", "https://jcxj.cc").replace(/\/$/, "");
const DEMO = args.includes("--demo");
const RUNS = Number(opt("--runs", "3"));
const PROFILE = new URL("./pw-profile177/", import.meta.url).pathname;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function poll(page, fn, deadlineMs = 60000) {
  const start = Date.now();
  for (;;) {
    try { const v = await page.evaluate(fn); if (v) return Date.now(); } catch { /* 导航中 */ }
    if (Date.now() - start > deadlineMs) return null;
    await sleep(30);
  }
}
const TARGETS = {
  overview: { href: "/", ready: () => {
    const el = document.querySelector("section.hero-net");
    if (!el) return false;
    const b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0 && /[0-9]/.test(el.textContent || "");
  } },
  expenses: { href: "/expenses", ready: () => {
    const h1 = document.querySelector("h1.page-title");
    if (!h1 || !(h1.textContent || "").includes("报销台账")) return false;
    return document.querySelectorAll(".kpi-card, .card, section").length >= 2;
  } },
  salary: { href: "/salary", ready: () => {
    const h1 = document.querySelector("h1.page-title");
    if (!h1 || !(h1.textContent || "").includes("工资")) return false;
    return document.querySelectorAll(".kpi-card, .card, section").length >= 2;
  } },
};
async function goTo(page, key, mobile) {
  const spec = TARGETS[key];
  if (mobile) {
    const burger = page.locator("button.nav-burger").first();
    if (await burger.isVisible().catch(() => false)) {
      const expanded = await page.evaluate(() => document.querySelector("header.topbar")?.classList.contains("nav-open"));
      if (!expanded) await burger.tap();
    }
  }
  const link = page.locator(`nav.menu a[href="${spec.href}"]`).first();
  await link.waitFor({ state: "visible", timeout: 10000 });
  const t0 = Date.now();
  if (mobile) await link.tap(); else await link.click();
  const readyAt = await poll(page, spec.ready);
  return { interactiveMs: readyAt ? readyAt - t0 : null };
}
async function fadeSample(page) {
  const samples = [];
  for (let i = 0; i < 3; i++) {
    try {
      const s = await page.evaluate(() => {
        const m = document.querySelector("main.container");
        if (!m) return null;
        const cs = getComputedStyle(m);
        return { cls: m.className, name: cs.animationName, duration: cs.animationDuration };
      });
      if (s) samples.push(s);
    } catch { /* 导航中 */ }
    await sleep(120);
  }
  return samples;
}
const median = (xs) => { const s = [...xs].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : null; };
const waitEnter = (msg) => new Promise((res) => {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  rl.question(msg, () => { rl.close(); res(); });
});

async function runPass(browserCtxOpts, vp) {
  const ctx = await chromium.launchPersistentContext(PROFILE, {
    channel: "chrome", headless: DEMO, viewport: vp.viewport,
    ...(vp.mobile ? { deviceScaleFactor: 2, isMobile: true, hasTouch: true } : {}),
    ...browserCtxOpts,
  });
  try {
    if (DEMO) await ctx.addCookies([
      { name: "demo_role", value: "owner", url: BASE },
      { name: "demo_biz", value: "all", url: BASE },
    ]);
    const page = ctx.pages()[0] || (await ctx.newPage());
    await page.goto(BASE + "/", { waitUntil: "load" });
    if (!DEMO && page.url().includes("/login")) {
      console.log("\n>>> 当前未登录。请在打开的 Chrome 窗口里登录 " + BASE + " ，登录完成回到终端按 Enter 继续（登录态会存进 ./pw-profile177，下次免登）。");
      await waitEnter(">>> 登录好了按 Enter：");
      await page.goto(BASE + "/", { waitUntil: "load" });
    }
    await poll(page, TARGETS.overview.ready);
    await goTo(page, "expenses", vp.mobile); await goTo(page, "salary", vp.mobile); await goTo(page, "overview", vp.mobile);
    const res = { viewport: vp.viewport, mobile: vp.mobile, paths: {}, fade: {}, shots: {} };
    for (const key of ["overview", "expenses", "salary"]) {
      const trials = [];
      for (let i = 0; i < RUNS; i++) {
        if (key === "overview") await goTo(page, "salary", vp.mobile); else await goTo(page, "overview", vp.mobile);
        trials.push(await goTo(page, key, vp.mobile));
      }
      const iv = trials.map((t) => t.interactiveMs).filter((v) => v !== null);
      res.paths[key] = { href: TARGETS[key].href, trials, median: median(iv), max: iv.length ? Math.max(...iv) : null };
      if (vp.mobile) {
        const shot = new URL(`./shot177-${key}.png`, import.meta.url).pathname;
        await page.screenshot({ path: shot });
        res.shots[key] = shot;
        if (key !== "overview") res.fade[key] = await fadeSample(page);
      }
    }
    return res;
  } finally { await ctx.close().catch(() => {}); }
}

const out = { label: "measure177-host", base: BASE, demo: DEMO, engine: "system Chrome via Playwright channel=chrome (host macOS)", at: new Date().toISOString(), viewports: {} };
for (const vp of [
  { key: "mobile375", viewport: { width: 375, height: 667 }, mobile: true },
  { key: "pc1440", viewport: { width: 1440, height: 900 }, mobile: false },
]) {
  out.viewports[vp.key] = await runPass({}, vp);
  console.log(vp.key, JSON.stringify(Object.fromEntries(Object.entries(out.viewports[vp.key].paths).map(([k, v]) => [k, { median: v.median, max: v.max }]))));
}
const file = new URL("./measure177-host.json", import.meta.url).pathname;
writeFileSync(file, JSON.stringify(out, null, 2));
console.log("written:", file);
