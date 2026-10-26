/**
 * 390×844 手机视口布局审计（指令57 A8 固化，原 /tmp ad-hoc audit390 正规化）。
 *
 * 判定标准（六页：总览/报销/工资/成本/对比/help，老板演示会话）：
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

const PAGES = [
  ["总览", "/"],
  ["报销", "/expenses"],
  ["工资", "/salary"],
  ["成本", "/business/aihuishou/costs"],
  ["对比", "/business/aihuishou/compare"],
  ["help", "/help"],
];

const rep = new Reporter("audit-390");
const browser = await launchBrowser();
const ctx = await demoContext(browser, {
  role: "owner",
  viewport: { width: 390, height: 844 },
  mobile: true,
});

for (const [name, path] of PAGES) {
  const page = await ctx.newPage();
  const resp = await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 }).catch(() => null);
  await page.waitForTimeout(500);
  rep.check(`${name} ${path} HTTP 200`, !!resp && resp.status() === 200, resp ? `status=${resp.status()}` : "无响应");
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
    return { iw, docSW, wide, squeezed, smallTargets };
  });
  rep.check(`${name} 页面级横向溢出=0`, r.docSW <= r.iw, `docSW=${r.docSW} iw=${r.iw}`);
  rep.check(`${name} 文本撞线=0`, r.squeezed.length === 0, JSON.stringify(r.squeezed.slice(0, 3)));
  const outside = r.wide.filter((w) => !w.inTableWrap);
  rep.check(
    `${name} 超宽元素全归属 .table-wrap`,
    outside.length === 0,
    outside.map((w) => `${w.tag}.${w.cls}「${w.text}」`).join(" ; ")
  );
  if (r.wide.length) rep.info(`${name} 容器内超宽元素 ${r.wide.length} 个（设计内）`);
  if (r.smallTargets.length) rep.info(`${name} <30px 热区（仅记录）: ${r.smallTargets.join(" ; ")}`);
  await page.close();
}

await browser.close();
process.exit(rep.finish());
