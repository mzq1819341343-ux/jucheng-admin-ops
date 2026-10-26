/**
 * 生产性能基线采样（指令59 A7）：只测不改。
 *
 * 线上（https://jcxj.cc，未登录侧）：
 * - /login（200）与六条登录后路径（307）的响应时间（≈TTFB）与总耗时；
 * - /help-shots 八张图（200）的总耗时与体积；
 * 每条 5 个样本取中位数。
 * 本地（演示生产构建，老板会话，1440 视口）：五页 LCP 近似
 * （PerformanceObserver largest-contentful-paint），每页 3 次取中位数；
 * 本地服务不可达时跳过并注明。
 *
 * 结果人工转录进 docs/运维_性能基线.md（脚本只打印，不落档）。
 * 用法：node scripts/regression/perf-baseline.mjs
 */
import { launchBrowser, demoContext, BASE, ONLINE } from "./lib.mjs";

const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
};
const round = (n) => Math.round(n);

async function sample(url, headers = {}) {
  const t0 = performance.now();
  const r = await fetch(url, { redirect: "manual", headers });
  const tHead = performance.now();
  const buf = await r.arrayBuffer();
  const tEnd = performance.now();
  return { status: r.status, ttfb: tHead - t0, total: tEnd - t0, bytes: buf.byteLength };
}

async function series(label, url, n = 5) {
  const rows = [];
  for (let i = 0; i < n; i++) rows.push(await sample(url));
  const m = {
    status: rows[0].status,
    ttfb: median(rows.map((r) => r.ttfb)),
    total: median(rows.map((r) => r.total)),
    bytes: rows[0].bytes,
  };
  console.log(
    `${label}\tstatus=${m.status}\tTTFB中位=${round(m.ttfb)}ms\t总耗时中位=${round(m.total)}ms\t体积=${m.bytes}B\t样本=${n}`
  );
  return m;
}

console.log(`== 线上 ${ONLINE} ==`);
await series("/login", ONLINE + "/login");
for (const p of ["/", "/expenses", "/business/aihuishou", "/salary", "/help", "/status"]) {
  await series(p + "（未登录）", ONLINE + p);
}
for (const f of ["overview.png", "expenses.png", "salary-rule-preview.png", "costs-utilities.png", "dashboard-drill.png", "compare.png", "mobile-overview.png", "mobile-expenses.png"]) {
  await series(`/help-shots/${f}`, ONLINE + `/help-shots/${f}`, 3);
}

console.log(`\n== 本地演示构建 ${BASE}（LCP 近似，老板会话 1440） ==`);
try {
  const probe = await fetch(BASE + "/login");
  if (probe.status !== 200) throw new Error(`status=${probe.status}`);
  const browser = await launchBrowser();
  const ctx = await demoContext(browser, { role: "owner", viewport: { width: 1440, height: 1000 } });
  for (const [name, path] of [
    ["总览", "/"],
    ["报销", "/expenses"],
    ["工资", "/salary"],
    ["报表", "/business/aihuishou/reports"],
    ["help", "/help"],
  ]) {
    const lcps = [];
    for (let i = 0; i < 3; i++) {
      const page = await ctx.newPage();
      await page.addInitScript(() => {
        window.__lcp = 0;
        new PerformanceObserver((list) => {
          for (const e of list.getEntries()) window.__lcp = e.startTime;
        }).observe({ type: "largest-contentful-paint", buffered: true });
      });
      await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 });
      await page.waitForTimeout(800);
      lcps.push(await page.evaluate(() => window.__lcp));
      await page.close();
    }
    console.log(`${name} ${path}\tLCP中位=${round(median(lcps))}ms\t样本=3`);
  }
  await browser.close();
} catch (e) {
  console.log(`本地服务不可达，LCP 采样跳过：${e.message}`);
}
