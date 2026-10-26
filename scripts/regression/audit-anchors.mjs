/**
 * 演示数据漂移哨兵（指令58 A5）：老板演示会话逐页断言锁定锚点，演示兜底
 * 数据或核算口径一旦漂移立刻 FAIL。锚点全部来自已锁定口径（指令55 A 自检）：
 * - 1–9 月合计 3,958 单 / 分润 416,661.32；
 * - 2026-09 分润 53,220.60、公司口径净利 −6,312.51（总览英雄取整 −6,313）；
 * - 工资实发（月度汇总口径）244,757.00；2026-09 门店成本 11,183.37；
 * - 双利润率：分润口径 −11.9%，营收口径恒「—」（无数据源，不冒充）。
 * 负号在不同组件有 ASCII「-」与「−」两种渲染，断言用 [-−] 兼容。
 * 工资页演示模式不渲染实表明细（指令47），该页只断结构锚（配置区/双版本预览），
 * 工资数字锚点在总览年回报脚注、仪表盘与报表页断言。
 *
 * 用法：node scripts/regression/audit-anchors.mjs（本地演示服务，老板会话）
 */
import { launchBrowser, demoContext, Reporter, innerText, BASE } from "./lib.mjs";

const rep = new Reporter("audit-anchors");
const browser = await launchBrowser();
const ctx = await demoContext(browser, { role: "owner", viewport: { width: 1440, height: 1000 } });

async function openPage(path) {
  const page = await ctx.newPage();
  const resp = await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 }).catch(() => null);
  await page.waitForTimeout(600); // 等总览英雄 CountUp 落定
  return { page, resp };
}
function has(name, text, s) {
  rep.check(`${name} 锚点「${s}」`, text.includes(s));
}
function hasRe(name, text, re) {
  rep.check(`${name} 锚点 ${re}`, re.test(text));
}

// —— 总览 ——
{
  const { page, resp } = await openPage("/");
  rep.check("总览 HTTP 200", !!resp && resp.status() === 200);
  const t = await innerText(page);
  hasRe("总览", t, /[-−]6,313/); // 英雄净利取整（精确值 −6,312.51 在仪表盘/报表断言）
  hasRe("总览", t, /[-−]11\.9%/); // 利润率（分润）
  has("总览", t, "244,757.00"); // 年回报分母脚注：工资实发
  has("总览", t, "93,567.00"); // 年回报分母脚注：门店成本（1–9 月）
  const revDash = await page.evaluate(() => {
    const els = [...document.querySelectorAll("body *")].filter(
      (e) => e.children.length === 0 && (e.textContent || "").trim() === "利润率（营收）"
    );
    return els.some((e) => {
      const card = e.closest("div");
      return !!card && (card.parentElement?.textContent || "").includes("—");
    });
  });
  rep.check("总览 利润率（营收）值为「—」", revDash);
  await page.close();
}

// —— 爱回收仪表盘 ——
{
  const { page } = await openPage("/business/aihuishou");
  const t = await innerText(page);
  has("仪表盘", t, "3,958");
  has("仪表盘", t, "53,220.60");
  hasRe("仪表盘", t, /[-−]6,312\.51/);
  has("仪表盘", t, "11,183.37");
  hasRe("仪表盘", t, /[-−]11\.9%/);
  has("仪表盘", t, "244,757.00");
  await page.close();
}

// —— 经营报表 ——
{
  const { page } = await openPage("/business/aihuishou/reports");
  const t = await innerText(page);
  has("报表", t, "3,958");
  has("报表", t, "416,661.32");
  has("报表", t, "53,220.60");
  hasRe("报表", t, /[-−]6,312\.51/);
  has("报表", t, "244,757.00");
  has("报表", t, "11,183.37");
  await page.close();
}

// —— 两店对比（2026-09 分店锚点） ——
{
  const { page } = await openPage("/business/aihuishou/compare");
  const t = await innerText(page);
  hasRe("对比", t, /[-−]5,503\.06/); // 友谊 9 月扣成本后净利暂定
  hasRe("对比", t, /[-−]809\.45/); // 万达 9 月扣成本后净利暂定
  hasRe("对比", t, /[-−]35\.1%/); // 友谊 9 月利润率（分润）
  has("对比", t, "7,083.33"); // 友谊 9 月门店成本
  has("对比", t, "4,100.04"); // 万达 9 月门店成本
  await page.close();
}

// —— 工资（结构锚；数字锚点见总览/仪表盘/报表） ——
{
  const { page } = await openPage("/salary");
  const t = await innerText(page);
  has("工资", t, "工资体系配置");
  has("工资", t, "双版本预览");
  await page.close();
}

await browser.close();
process.exit(rep.finish());
