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

await browser.close();
process.exit(rep.finish());
