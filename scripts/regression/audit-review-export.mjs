/**
 * 目标复盘导出一致性审计（指令61 A4，指令60 A4 一次性比对正规化）：
 * 老板演示会话（含 demo_targets 注入三分支）下打开报表页，抓「目标达成
 * 复盘」页面表格逐行，再真实点击两个导出按钮拿下载文件，CSV / Excel 与
 * 页面行同源逐格比对——证明页面与导出吃的是同一份 buildTargetReviewRows
 * 行数据、同一格式化口径（components/TargetReviewExport.tsx rowCells）。
 *
 * 格式差异（设计如此，比对时正规化）：页面金额带千分位与「 元」后缀、
 * 缺值显「—」；导出为纯数字（金额 2 位小数 / 单量整数）、缺值留空。
 * 正规化后数值按分取整比对，月份/口径/状态/达成率逐字比对。
 *
 * 前置同 run-all：本地演示服务（REG_BASE，NEXT_PUBLIC_ALLOW_DEMO=1 构建）。
 * 用法：node scripts/regression/audit-review-export.mjs
 */
import { readFileSync } from "node:fs";
import { BASE, Reporter, demoContext, launchBrowser } from "./lib.mjs";

const rep = new Reporter("audit-review-export");
const REPORTS = BASE + "/business/aihuishou/reports";

// 与 audit-anchors 三分支同一注入：已达成 / 未达成 / 待核算各一行，其余未设定。
const targets = [
  { month: "2026-09", metric: "profit", target_value: 50000 },
  { month: "2026-09", metric: "orders", target_value: 500 },
  { month: "2026-08", metric: "net_profit", target_value: 1000 },
];

const num = (s) => {
  const t = String(s).replace(/[,，\s]/g, "").replace(/元$/, "");
  if (t === "" || t === "—") return null;
  const v = Number(t);
  return Number.isFinite(v) ? Math.round(v * 100) : NaN;
};
const txt = (s) => (s === "—" ? "" : String(s).trim());

/** 页面行 → 正规化六列；导出行本身即正规化形态，仅数值列过 num。 */
function normRow(cells, fromPage) {
  return cells.map((c, i) => {
    if (i === 2 || i === 3) return num(c);
    if (i === 4) return txt(c);
    return String(c).trim();
  });
}
const eqRow = (a, b) => a.length === b.length && a.every((v, i) => v === b[i] || (Number.isNaN(v) && Number.isNaN(b[i])));

const browser = await launchBrowser();
try {
  const ctx = await demoContext(browser, { role: "owner", viewport: { width: 1440, height: 1000 } });
  await ctx.addCookies([
    { name: "demo_targets", value: Buffer.from(JSON.stringify(targets), "utf8").toString("base64url"), url: BASE },
  ]);
  const page = await ctx.newPage();
  const resp = await page.goto(REPORTS, { waitUntil: "networkidle", timeout: 30000 }).catch(() => null);
  rep.check("复盘导出审计 HTTP 200", !!resp && resp.status() === 200);
  await page.waitForTimeout(400);

  // —— 页面侧：定位复盘表（标题后第一张 report 表）并抓行 ——
  const pageData = await page.evaluate(() => {
    const h2s = [...document.querySelectorAll("h2.section-title")];
    const h2 = h2s.find((h) => h.textContent.includes("目标达成复盘"));
    if (!h2) return null;
    let el = h2.nextElementSibling;
    while (el && !el.querySelector("table.report")) el = el.nextElementSibling;
    const table = el?.querySelector("table.report");
    if (!table) return null;
    const head = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    const rows = [...table.querySelectorAll("tbody tr")].map((tr) =>
      [...tr.querySelectorAll("td")].map((td) => td.textContent.trim())
    );
    return { head, rows };
  });
  rep.check("复盘节与表格在位", !!pageData);
  const btnCsv = await page.getByRole("button", { name: "导出复盘 CSV" }).count();
  const btnXls = await page.getByRole("button", { name: "导出复盘 Excel" }).count();
  rep.check("导出按钮在位（CSV + Excel）", btnCsv === 1 && btnXls === 1, `csv=${btnCsv} xls=${btnXls}`);
  const pageRows = (pageData?.rows ?? []).map((r) => normRow(r, true));
  rep.check("页面复盘行数 = 27（9 月 × 3 口径）", pageRows.length === 27, `实得 ${pageRows.length}`);
  rep.check(
    "页面表头六列",
    JSON.stringify(pageData?.head) === JSON.stringify(["月份", "口径", "目标", "实际", "达成率", "状态"]),
    JSON.stringify(pageData?.head)
  );
  const statuses = new Set(pageRows.map((r) => r[5]));
  for (const s of ["已达成", "未达成", "待核算", "未设定"]) {
    rep.check(`页面状态分支覆盖「${s}」`, statuses.has(s));
  }

  // —— CSV：真实点击下载，BOM/表头/逐行比对 ——
  const [csvDl] = await Promise.all([
    page.waitForEvent("download", { timeout: 15000 }),
    page.getByRole("button", { name: "导出复盘 CSV" }).click(),
  ]);
  const csvBuf = readFileSync(await csvDl.path());
  rep.check("CSV 带 UTF-8 BOM", csvBuf[0] === 0xef && csvBuf[1] === 0xbb && csvBuf[2] === 0xbf);
  const csvLines = csvBuf.toString("utf8").replace(/^﻿/, "").split("\n").filter((l) => l.length > 0);
  rep.check("CSV 表头六列", csvLines[0] === "月份,口径,目标,实际,达成率,状态", csvLines[0]);
  const csvRows = csvLines.slice(1).map((l) => normRow(l.split(","), false));
  rep.check("CSV 行数与页面一致", csvRows.length === pageRows.length, `csv=${csvRows.length} page=${pageRows.length}`);
  pageRows.forEach((pr, i) => {
    rep.check(`CSV 第 ${i + 1} 行与页面逐格一致（${pr[0]} ${pr[1]}）`, eqRow(csvRows[i] ?? [], pr),
      `csv=${JSON.stringify(csvRows[i])} page=${JSON.stringify(pr)}`);
  });

  // —— Excel（.xls HTML 表）：真实点击下载，解析 tr/td 逐行比对 ——
  const [xlsDl] = await Promise.all([
    page.waitForEvent("download", { timeout: 15000 }),
    page.getByRole("button", { name: "导出复盘 Excel" }).click(),
  ]);
  const xlsText = readFileSync(await xlsDl.path(), "utf8");
  rep.check("Excel 带 UTF-8 BOM", xlsText.charCodeAt(0) === 0xfeff);
  const dec = (s) =>
    s.replace(/<[^>]+>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&amp;/g, "&").trim();
  const trs = [...xlsText.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].map((m) => m[1]);
  const xlsHead = trs.length ? [...trs[0].matchAll(/<th>([\s\S]*?)<\/th>/g)].map((m) => dec(m[1])) : [];
  rep.check("Excel 表头六列", JSON.stringify(xlsHead) === JSON.stringify(["月份", "口径", "目标", "实际", "达成率", "状态"]),
    JSON.stringify(xlsHead));
  const xlsRows = trs.slice(1).map((tr) => normRow([...tr.matchAll(/<td>([\s\S]*?)<\/td>/g)].map((m) => dec(m[1])), false));
  rep.check("Excel 行数与页面一致", xlsRows.length === pageRows.length, `xls=${xlsRows.length} page=${pageRows.length}`);
  pageRows.forEach((pr, i) => {
    rep.check(`Excel 第 ${i + 1} 行与页面逐格一致（${pr[0]} ${pr[1]}）`, eqRow(xlsRows[i] ?? [], pr),
      `xls=${JSON.stringify(xlsRows[i])} page=${JSON.stringify(pr)}`);
  });
  rep.check("CSV 与 Excel 行数据互相一致", JSON.stringify(csvRows) === JSON.stringify(xlsRows));

  await ctx.close();
} finally {
  await browser.close();
}
process.exit(rep.finish());
