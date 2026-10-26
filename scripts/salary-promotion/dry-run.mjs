/**
 * 工资晋升 dry-run（指令59 A1）：只读演算，零写入。
 *
 * 对应 migration_006 末尾注释的「晋升路径」：
 *   salary_detail_import 按 employee_name 精确匹配 profiles.display_name
 *   → 同人同月两段（store/door）汇总 → 将写入 salary_monthly 的行
 *     （scope 待老板晋升时指定，本脚本只标 TODO；published 恒 false）。
 *
 * 零写入保证：
 * - 本脚本不引入任何数据库客户端、不发网络请求，只读本地 JSON 文件；
 * - 全文没有写库调用（insert / update / delete / upsert 均无），
 *   产出只有控制台报告与可选的 Markdown 报告文件（--out）。
 *
 * 输入：{ profiles: [{id, display_name}], salary_detail_import: [...] }
 *   默认读同目录 sample-data.json（虚构样本）；真实运行用 --input 指定老板
 *   从 Supabase SQL Editor 导出的同结构 JSON（导出方法见 README.md）。
 *
 * 用法：
 *   node scripts/salary-promotion/dry-run.mjs [--input data.json] [--out report.md]
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const argVal = (flag) => {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : null;
};
const inputPath = argVal("--input") || join(here, "sample-data.json");
const outPath = argVal("--out");

const data = JSON.parse(readFileSync(inputPath, "utf8"));
const profiles = data.profiles ?? [];
const details = data.salary_detail_import ?? data.details ?? [];

const round2 = (n) => Math.round((n + Number.EPSILON) * 100) / 100;
const fmt = (n) => (n === null || n === undefined ? "—" : round2(Number(n)).toFixed(2));

// —— 1) 姓名匹配：display_name 精确相等；同名多账号 = 重名风险，不自动匹配 ——
const byName = new Map();
for (const p of profiles) {
  const key = String(p.display_name ?? "").trim();
  if (!key) continue;
  (byName.get(key) ?? byName.set(key, []).get(key)).push(p);
}

const names = [...new Set(details.map((d) => String(d.employee_name).trim()))];
const matched = new Map(); // name -> profile
const unmatched = [];
const ambiguous = [];
for (const name of names) {
  const cands = byName.get(name) ?? [];
  if (cands.length === 1) matched.set(name, cands[0]);
  else if (cands.length === 0) unmatched.push(name);
  else ambiguous.push({ name, candidates: cands.map((c) => c.id) });
}

// —— 2) 已回填 matched_user_id 的行优先认领（与 migration_006 注释第 1 步一致）——
const profileById = new Map(profiles.map((p) => [p.id, p]));
for (const d of details) {
  if (d.matched_user_id && profileById.has(d.matched_user_id)) {
    matched.set(String(d.employee_name).trim(), profileById.get(d.matched_user_id));
  }
}

// —— 3) 按人按月汇总两段，生成「将写入 salary_monthly」的演算行 ——
const groups = new Map(); // `${userId}|${month}` -> row
for (const d of details) {
  const name = String(d.employee_name).trim();
  const profile = matched.get(name);
  if (!profile) continue;
  const key = `${profile.id}|${d.month}`;
  const row = groups.get(key) ?? {
    user_id: profile.id,
    employee_name: name,
    month: d.month,
    scope: "TODO（待老板指定）",
    base_amount: 0,
    commission_amount: 0,
    deduction_amount: 0,
    net_amount: 0,
    published: false,
    sections: [],
  };
  row.base_amount += Number(d.base_amount ?? 0);
  row.commission_amount += Number(d.commission_amount ?? 0);
  row.deduction_amount += Number(d.deduction_amount ?? 0);
  row.net_amount += Number(d.net_amount ?? 0);
  row.sections.push(d.section);
  groups.set(key, row);
}
const promoteRows = [...groups.values()].sort((a, b) =>
  a.month.localeCompare(b.month) || a.employee_name.localeCompare(b.employee_name)
);

// —— 4) 校验：每行分项之和与实发的关系只提示不拦截（源表口径含调整项符号） ——
const warnings = [];
for (const r of promoteRows) {
  const sum = round2(r.base_amount + r.commission_amount + r.deduction_amount);
  if (Math.abs(sum - round2(r.net_amount)) > 0.005) {
    warnings.push(
      `${r.employee_name} ${r.month}：分项合计 ${fmt(sum)} ≠ 实发 ${fmt(r.net_amount)}（源表调整项口径，晋升前请老板核对）`
    );
  }
}

// —— 报告 ——
const lines = [];
lines.push(`# 工资晋升 dry-run 报告（只读演算，未写入任何数据）`);
lines.push("");
lines.push(`- 输入文件：${inputPath}`);
lines.push(`- 明细行：${details.length} 行 / ${names.length} 人；profiles：${profiles.length} 个账号`);
lines.push(`- 匹配成功：${matched.size} 人 → 将晋升 salary_monthly ${promoteRows.length} 行（published=false，scope 待老板指定）`);
lines.push(`- 未匹配（无同名账号）：${unmatched.length} 人${unmatched.length ? "：" + unmatched.join("、") : ""}`);
lines.push(`- 重名待人工确认（同名多账号，不自动匹配）：${ambiguous.length} 人${ambiguous.length ? "：" + ambiguous.map((a) => a.name).join("、") : ""}`);
lines.push("");
lines.push(`## 将晋升的行（演算）`);
lines.push("");
lines.push(`| 员工 | 月份 | user_id | scope | 底薪 | 提成 | 扣款/调整 | 实发 | published | 段 |`);
lines.push(`|---|---|---|---|---|---|---|---|---|---|`);
for (const r of promoteRows) {
  lines.push(
    `| ${r.employee_name} | ${r.month} | ${r.user_id} | ${r.scope} | ${fmt(r.base_amount)} | ${fmt(r.commission_amount)} | ${fmt(r.deduction_amount)} | ${fmt(r.net_amount)} | false | ${r.sections.join("+")} |`
  );
}
if (unmatched.length) {
  lines.push("");
  lines.push(`## 未匹配名单（先建账号再晋升）`);
  for (const n of unmatched) lines.push(`- ${n}`);
}
if (ambiguous.length) {
  lines.push("");
  lines.push(`## 重名风险（须老板人工指定账号）`);
  for (const a of ambiguous) lines.push(`- ${a.name}：同名账号 ${a.candidates.length} 个（${a.candidates.join(" / ")}）`);
}
if (warnings.length) {
  lines.push("");
  lines.push(`## 口径提示`);
  for (const w of warnings) lines.push(`- ${w}`);
}
lines.push("");
lines.push(`> 本报告为 dry-run 演算：真实晋升由老板核对本报告后，按 migration_006 注释 SQL 执行（先 published=false，核对无误再发布）。`);

const report = lines.join("\n");
console.log(report);
if (outPath) {
  writeFileSync(outPath, report + "\n", "utf8");
  console.log(`\n报告已写入 ${outPath}`);
}
