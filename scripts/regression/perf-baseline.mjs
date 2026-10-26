/**
 * 生产性能基线采样（指令59 A7 立项；指令61 A7 升级协议 v2）：只测不改。
 *
 * —— 采样协议 v2（多轮中位，抗单轮网络噪声；指令60 A3 教训立约）——
 * 线上每条目标：3 轮 × 每轮 3 样本。轮内取中位（单轮估计，抗个别慢样本）；
 * 结论值 = 三个轮中位的再中位（轮间中位）；波动幅度 = 轮中位极差
 * （max − min，总耗时口径），随结论值一并输出，波动大于结论值 50% 时
 * 该条只记体积等确定性指标、耗时结论标注「噪声主导，不下升降结论」。
 * 剔除规则：单样本请求失败（网络错误/非预期状态）记为废样本并补采一次；
 * 整轮全废则该轮作废，有效轮 < 2 时整条标注「采样不足」不给结论值。
 * 不做人为掐头去尾：除上述中位与废样本规则外不删数据。
 *
 * 线上（https://jcxj.cc，未登录侧）：/login（200）、六条登录后路径（307）、
 * /help-shots 八张图（200，含体积）。
 * 本地（演示生产构建，老板会话，1440）：五页 LCP 近似，3 轮 × 每轮 2 次
 * 取轮中位 → 轮间中位 + 极差；本地服务不可达时跳过并注明。
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
const ROUNDS = 3;
const PER_ROUND = 3;

async function sample(url) {
  const t0 = performance.now();
  const r = await fetch(url, { redirect: "manual" });
  const tHead = performance.now();
  const buf = await r.arrayBuffer();
  const tEnd = performance.now();
  return { status: r.status, ttfb: tHead - t0, total: tEnd - t0, bytes: buf.byteLength };
}

/** v2 多轮采样：返回 { status, bytes, ttfb, total, spread, noisy, ok }。 */
async function seriesV2(label, url, expectedStatus) {
  const roundMedians = [];
  let status = null;
  let bytes = null;
  for (let r = 0; r < ROUNDS; r++) {
    const rows = [];
    for (let i = 0; i < PER_ROUND; i++) {
      try {
        const s = await sample(url);
        if (expectedStatus && s.status !== expectedStatus) continue; // 非预期状态 = 废样本
        rows.push(s);
      } catch {
        // 网络错误 = 废样本，继续补采本轮剩余名额
      }
    }
    if (rows.length === 0) continue; // 整轮作废
    status = rows[0].status;
    bytes = rows[0].bytes;
    roundMedians.push({ ttfb: median(rows.map((x) => x.ttfb)), total: median(rows.map((x) => x.total)) });
  }
  if (roundMedians.length < 2) {
    console.log(`${label}\t采样不足（有效轮 ${roundMedians.length}/${ROUNDS}），不给结论值`);
    return { ok: false };
  }
  const ttfb = median(roundMedians.map((m) => m.ttfb));
  const total = median(roundMedians.map((m) => m.total));
  const spread = Math.max(...roundMedians.map((m) => m.total)) - Math.min(...roundMedians.map((m) => m.total));
  const noisy = spread > total * 0.5;
  console.log(
    `${label}\tstatus=${status}\tTTFB=${round(ttfb)}ms\t总耗时=${round(total)}ms\t轮间极差=${round(spread)}ms${noisy ? "（噪声主导）" : ""}\t体积=${bytes}B`
  );
  return { ok: true, status, bytes, ttfb, total, spread, noisy };
}

console.log(`== 线上 ${ONLINE}（协议 v2：${ROUNDS} 轮 × ${PER_ROUND} 样本） ==`);
await seriesV2("/login", ONLINE + "/login", 200);
for (const p of ["/", "/expenses", "/business/aihuishou", "/salary", "/help", "/status"]) {
  await seriesV2(p + "（未登录）", ONLINE + p, 307);
}
for (const f of ["overview.png", "expenses.png", "salary-rule-preview.png", "costs-utilities.png", "dashboard-drill.png", "compare.png", "mobile-overview.png", "mobile-expenses.png"]) {
  await seriesV2(`/help-shots/${f}`, ONLINE + `/help-shots/${f}`, 200);
}

console.log(`\n== 本地演示构建 ${BASE}（LCP 近似，老板会话 1440，v2：${ROUNDS} 轮 × 2 次） ==`);
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
    const roundMedians = [];
    for (let r = 0; r < ROUNDS; r++) {
      const lcps = [];
      for (let i = 0; i < 2; i++) {
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
      roundMedians.push(median(lcps));
    }
    const v = median(roundMedians);
    const spread = Math.max(...roundMedians) - Math.min(...roundMedians);
    console.log(`${name} ${path}\tLCP=${round(v)}ms\t轮间极差=${round(spread)}ms`);
  }
  await browser.close();
} catch (e) {
  console.log(`本地服务不可达，LCP 采样跳过：${e.message}`);
}
