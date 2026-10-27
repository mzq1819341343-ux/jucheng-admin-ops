/**
 * 布局审计（指令57 A8 固化，指令58 A6 双视口，指令59 A3 扩为三角色，
 * 指令67 A3 增日间主题轨）。
 *
 * 角色 × 视口：老板 / 合伙人 / 店员三演示会话 × 390×844 + 1440×1000。
 * 页集按各角色可达页裁剪：老板、合伙人六页（总览/报销/工资/成本/对比/help）；
 * 店员五页（成本页店员直进被拦回，不在布局页集内，拦截由 audit-staff 断言）。
 *
 * 主题双轨：夜间轨 = 三角色全量（现状，默认主题）；日间轨 = 老板会话先行
 * （同页集 × 双视口），主题经 localStorage 预置 `jucheng-theme=light` 在
 * 页面脚本前注入，另逐页断言 data-theme=light 实际生效；布局判定与夜间
 * 轨同标准复用。
 *
 * 判定标准（两视口同标准）：
 * 1) 页面级横向溢出为 0：documentElement.scrollWidth <= 视口宽；
 * 2) 文本撞线为 0：叶子文本元素 scrollWidth > clientWidth + 2 且 overflowX=visible
 *    且非 nowrap 的元素数为 0；
 * 3) 超宽元素必须归属 .table-wrap 容器（容器内横向滚动为设计内）：
 *    最外层越界元素若不在 .table-wrap 内 → FAIL 并列出 tag/class/文本定位。
 * 触控热区 <30px 的可点元素只作 info 输出（品牌字标等既有例外，不判 FAIL）。
 *
 * 末段另含动线固化断言 15 项（指令72 A5 固化指令71 A4 电费动线 8 项 /
 * A5 目标动线 7 项的临时实测脚本断言面）：老板会话 1440 下实测电费区
 * 默认值/未录点选/提示翻转与目标表单月份选项/预填/收起；演示态写入
 * 以独立 context 隔离。本套件自此含布局 + 动线两类断言。
 *
 * 用法：node scripts/regression/audit-390.mjs（需本地演示服务已启动，见运维清单）
 */
import { launchBrowser, demoContext, Reporter, BASE } from "./lib.mjs";

const ALL_PAGES = [
  ["总览", "/"],
  ["报销", "/expenses"],
  ["工资", "/salary"],
  ["成本", "/business/aihuishou/costs"],
  ["对比", "/business/aihuishou/compare"],
  ["help", "/help"],
];

const ROLE_PAGES = [
  ["老板", "owner", ALL_PAGES],
  ["合伙人", "partner", ALL_PAGES],
  ["店员", "staff", ALL_PAGES.filter(([name]) => name !== "成本")],
];

const VIEWPORTS = [
  { label: "390", viewport: { width: 390, height: 844 }, mobile: true },
  { label: "1440", viewport: { width: 1440, height: 1000 }, mobile: false },
];

// 主题双轨：夜间 = 三角色全量；日间 = 老板会话先行（指令67 A3）
const TRACKS = [
  { themeLabel: "夜间", theme: undefined, roles: ROLE_PAGES },
  { themeLabel: "日间", theme: "light", roles: [ROLE_PAGES[0]] },
];

const rep = new Reporter("audit-390");
const browser = await launchBrowser();

for (const track of TRACKS) {
for (const vp of VIEWPORTS) {
  for (const [roleLabel, role, pages] of track.roles) {
    const ctx = await demoContext(browser, {
      role,
      viewport: vp.viewport,
      mobile: vp.mobile,
      theme: track.theme,
    });

    for (const [name, path] of pages) {
      const tag = `${vp.label} ${track.themeLabel} ${roleLabel} ${name}`;
      const page = await ctx.newPage();
      const resp = await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 }).catch(() => null);
      await page.waitForTimeout(500);
      rep.check(`${tag} ${path} HTTP 200`, !!resp && resp.status() === 200, resp ? `status=${resp.status()}` : "无响应");
      const r = await page.evaluate(() => {
        const iw = window.innerWidth;
        const docSW = document.documentElement.scrollWidth;
        const wide = [];
        const els = document.querySelectorAll("body *");
        for (const el of els) {
          const b = el.getBoundingClientRect();
          if (b.width > 0 && (b.right > iw + 1 || b.left < -1)) {
            let p = el.parentElement, parentWide = false;
            while (p) {
              const pr = p.getBoundingClientRect();
              if (pr.right > iw + 1 || pr.left < -1) { parentWide = true; break; }
              p = p.parentElement;
            }
            if (!parentWide) {
              wide.push({
                tag: el.tagName.toLowerCase(),
                cls: String(el.className && el.className.baseVal === undefined ? el.className : "").slice(0, 60),
                text: (el.textContent || "").trim().slice(0, 40),
                inTableWrap: !!el.closest(".table-wrap"),
              });
            }
          }
          if (wide.length >= 20) break;
        }
        const squeezed = [];
        for (const el of els) {
          if (el.children.length === 0 && el.scrollWidth > el.clientWidth + 2) {
            const cs = getComputedStyle(el);
            if (cs.overflowX === "visible" && cs.whiteSpace !== "nowrap") {
              squeezed.push({ tag: el.tagName.toLowerCase(), text: (el.textContent || "").trim().slice(0, 30), sw: el.scrollWidth, cw: el.clientWidth });
            }
          }
          if (squeezed.length >= 10) break;
        }
        const smallTargets = [];
        for (const el of document.querySelectorAll("a, button, summary, select, input")) {
          const b = el.getBoundingClientRect();
          if (b.width > 0 && b.height > 0 && b.height < 30) {
            smallTargets.push(`${el.tagName.toLowerCase()}(${Math.round(b.height)}px):${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 20)}`);
          }
          if (smallTargets.length >= 6) break;
        }
        return { iw, docSW, wide, squeezed, smallTargets, themeAttr: document.documentElement.getAttribute("data-theme") };
      });
      if (track.theme) {
        rep.check(`${tag} 日间主题生效（data-theme=light）`, r.themeAttr === "light", `data-theme=${r.themeAttr}`);
      }
      rep.check(`${tag} 页面级横向溢出=0`, r.docSW <= r.iw, `docSW=${r.docSW} iw=${r.iw}`);
      rep.check(`${tag} 文本撞线=0`, r.squeezed.length === 0, JSON.stringify(r.squeezed.slice(0, 3)));
      const outside = r.wide.filter((w) => !w.inTableWrap);
      rep.check(
        `${tag} 超宽元素全归属 .table-wrap`,
        outside.length === 0,
        outside.map((w) => `${w.tag}.${w.cls}「${w.text}」`).join(" ; ")
      );
      if (r.wide.length) rep.info(`${tag} 容器内超宽元素 ${r.wide.length} 个（设计内）`);
      if (r.smallTargets.length) rep.info(`${tag} <30px 热区（仅记录）: ${r.smallTargets.join(" ; ")}`);
      await page.close();
    }
    await ctx.close();
  }
}
}

// ============================================================
// 动线固化断言（指令72 A5：固化指令71 A4/A5 两个临时实测脚本的断言面，
// 并入本套件不新造套件；老板演示会话 1440，各自动线独立 context 隔离
// 演示态写入。真实模式观感点验不在此列，归三角色真机材料包。）
// A4 电费动线 8 项：默认值 / 未录点选 / 提示翻转 / 已录退出列表 / 友谊跨度。
// A5 目标动线 7 项：月份选项（本月/下月按运行日动态推导）/ 保存呈现 /
// 待核算 / 预填现值 / 收起。
// ============================================================
{
  const ctx = await demoContext(browser, { role: "owner", viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const tag = "1440 动线固化";
  await page.goto(BASE + "/business/aihuishou/costs", { waitUntil: "networkidle", timeout: 30000 });
  const monthInput = page.locator("#uc-month");
  await monthInput.waitFor({ timeout: 15000 });
  rep.check(`${tag} 电费默认门店=万达`, (await page.locator("#uc-scope").inputValue()) === "wanda");
  rep.check(`${tag} 电费无行时月份默认空（不臆造）`, (await monthInput.inputValue()) === "");
  const chips = page.locator("button", { hasText: /^2026-\d{2}$/ });
  const chipCount = await chips.count();
  rep.check(`${tag} 电费未录月份点选钮出现`, chipCount >= 1, `count=${chipCount}`);
  const firstChip = (await chips.first().innerText()).trim();
  await chips.first().click();
  await page.waitForTimeout(300);
  rep.check(`${tag} 电费点选即填入月份`, (await monthInput.inputValue()) === firstChip, firstChip);
  const hint = await page.locator(".access-msg").allInnerTexts();
  rep.check(`${tag} 电费填入后提示=尚未录入（未取得未计入）`, hint.some((t) => t.includes(`${firstChip} 电费尚未录入——未取得，未计入`)));
  await page.locator("#uc-amount").fill("321.5");
  await page.locator("button[type=submit]", { hasText: "录入电费" }).click();
  await page.waitForTimeout(500);
  const hint2 = await page.locator(".access-msg").allInnerTexts();
  rep.check(`${tag} 电费录入后提示翻转为已有电费`, hint2.some((t) => t.includes("已有电费 321.50 元")));
  const chipsAfter = await page.locator("button", { hasText: /^2026-\d{2}$/ }).allInnerTexts();
  rep.check(`${tag} 电费已录月份退出未录列表`, !chipsAfter.map((t) => t.trim()).includes(firstChip));
  await page.locator("#uc-scope").selectOption("friendship");
  await page.waitForTimeout(300);
  const fChips = await page.locator("button", { hasText: /^2026-\d{2}$/ }).allInnerTexts();
  rep.check(`${tag} 电费友谊未录月份自 2026-02 起`, fChips.length >= 1 && fChips[0].trim() === "2026-02", `first=${fChips[0]?.trim()}`);
  await page.close();
  await ctx.close();
}
{
  const ctx = await demoContext(browser, { role: "owner", viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const tag = "1440 动线固化";
  const sh = new Date(Date.now() + 8 * 3600 * 1000); // Asia/Shanghai
  const ym = (d) => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
  const curMonth = ym(sh);
  const nextMonth = ym(new Date(Date.UTC(sh.getUTCFullYear(), sh.getUTCMonth() + 1, 1)));
  await page.goto(BASE + "/", { waitUntil: "networkidle", timeout: 30000 });
  const openBtn = page.locator("button", { hasText: /设定本月目标|调整目标/ }).first();
  await openBtn.waitFor({ timeout: 15000 });
  await openBtn.click();
  await page.locator("#target-month").waitFor({ timeout: 10000 });
  const optVals = (await page.locator("#target-month option").allInnerTexts()).map((t) => t.trim());
  rep.check(`${tag} 目标月份选项含本月`, optVals.includes(curMonth), optVals.join(","));
  rep.check(`${tag} 目标月份选项含下月`, optVals.includes(nextMonth));
  rep.check(`${tag} 目标月份选项含数据月 2026-09`, optVals.includes("2026-09"));
  await page.locator("#target-month").selectOption(curMonth);
  await page.locator("#target-metric").selectOption("profit");
  await page.locator("#target-value").fill("60000");
  await page.locator("button[type=submit]", { hasText: "保存目标" }).click();
  await page.waitForTimeout(600);
  const bodyText = await page.locator("main").innerText();
  const esc = curMonth.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  rep.check(`${tag} 目标保存后出现本月分润行`, new RegExp(`${esc} · 当月分润`).test(bodyText));
  rep.check(`${tag} 目标无数据月实际=待核算`, new RegExp(`${esc} · 当月分润[\\s\\S]{0,120}待核算`).test(bodyText));
  await page.locator("#target-month").selectOption("2026-09");
  await page.locator("#target-month").selectOption(curMonth);
  await page.locator("#target-metric").selectOption("profit");
  rep.check(`${tag} 目标调整时预填现值`, (await page.locator("#target-value").inputValue()) === "60000");
  await page.locator("button", { hasText: "收起" }).click();
  await page.waitForTimeout(300);
  rep.check(`${tag} 目标收起后表单关闭`, (await page.locator("#target-month").count()) === 0);
  await page.close();
  await ctx.close();
}

await browser.close();
process.exit(rep.finish());
