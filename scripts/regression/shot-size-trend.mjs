/**
 * 守线偏差趋势自动留痕（指令66 A2，65-A5 手工表脚本化）：
 * 只读脚本——从 git 历史逐轮收尾点取 help-shots 八图实物字节，生成偏差
 * 趋势表，与 docs/运维_性能基线.md 中 65-A5 已记值逐值比对；偏差距容差
 * 沿 ≤1.5pt（逼近区）时在输出末尾显式提示。
 *
 * 纪律：只提示、不改基线、不改图；本脚本不纳入 run-all 断言（退出码
 * 恒为 0，比对不一致以「不一致」行显式列出，由当轮验收人工裁处）。
 * 采纳基线单源 = audit-online.mjs 的 SHOT_SIZE_BASE（正则解析，不复制
 * 常量）；容差沿与 audit-online 4b 一致：[−20%, +5%]。
 *
 * 用法：node scripts/regression/shot-size-trend.mjs
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const FILES = [
  "overview.png",
  "expenses.png",
  "salary-rule-preview.png",
  "costs-utilities.png",
  "dashboard-drill.png",
  "compare.png",
  "mobile-overview.png",
  "mobile-expenses.png",
];
// 逐轮收尾点（commit subject 应含「指令NN收尾」，脚本自检并提示）
const ROUND_POINTS = [
  { round: 61, commit: "d525887" },
  { round: 62, commit: "5592255" },
  { round: 63, commit: "df6d5bb" },
  { round: 64, commit: "99e792f" },
  { round: 65, commit: "17c4919" },
];
const TOL_LOW = -20; // %
const TOL_HIGH = 5; // %
const NEAR_PT = 1.5; // 逼近区：距沿 ≤1.5pt

const fmt = (n) => n.toLocaleString("en-US");
const devOf = (size, base) => ((size - base) / base) * 100;
const fmtDev = (d) => `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(2)}%`;

// —— 采纳基线：解析 audit-online.mjs 的 SHOT_SIZE_BASE（单源）——
const onlineSrc = readFileSync(join(root, "scripts/regression/audit-online.mjs"), "utf8");
const baseBlock = onlineSrc.match(/const SHOT_SIZE_BASE = \{([\s\S]*?)\};/);
if (!baseBlock) {
  console.error("无法从 audit-online.mjs 解析 SHOT_SIZE_BASE，脚本中止。");
  process.exit(0);
}
const BASE = {};
for (const m of baseBlock[1].matchAll(/"([\w.-]+)":\s*(\d+)/g)) BASE[m[1]] = Number(m[2]);

// —— git 历史逐轮取字节 ——
function gitSize(commit, file) {
  const out = execFileSync("git", ["cat-file", "-s", `${commit}:public/help-shots/${file}`], {
    cwd: root,
    encoding: "utf8",
  });
  return Number(out.trim());
}
function gitSubject(commit) {
  return execFileSync("git", ["log", "-1", "--format=%s", commit], { cwd: root, encoding: "utf8" }).trim();
}
const sizes = {}; // sizes[file][round] = bytes
const subjectWarn = [];
for (const { round, commit } of ROUND_POINTS) {
  const subj = gitSubject(commit);
  if (!subj.includes(`指令${round}收尾`)) subjectWarn.push(`收尾点 ${commit} subject 未含「指令${round}收尾」：${subj}`);
  for (const f of FILES) {
    (sizes[f] ||= {})[round] = gitSize(commit, f);
  }
}

// —— 基线文档 65-A5 已记值：解析「守线偏差趋势」节表格 ——
const doc = readFileSync(join(root, "docs/运维_性能基线.md"), "utf8");
const secStart = doc.indexOf("## 守线偏差趋势");
const sec = secStart >= 0 ? doc.slice(secStart) : "";
const tableLines = sec.split("\n").filter((l) => l.startsWith("|"));
const header = tableLines[0].split("|").map((c) => c.trim());
const roundCols = {}; // colIdx -> round
header.forEach((h, i) => {
  const m = h.match(/^(\d+) 收尾/);
  if (m) roundCols[i] = Number(m[1]);
});
const devColIdx = header.findIndex((h) => h.includes("偏差"));
const docRows = {}; // docRows[file] = { base, rounds: {round: bytes}, dev }
for (const line of tableLines.slice(2)) {
  const cells = line.split("|").map((c) => c.replace(/\*/g, "").trim());
  const file = cells[1];
  if (!FILES.includes(file)) continue;
  const num = (s) => Number(s.replace(/,/g, ""));
  const row = { base: num(cells[2]), rounds: {}, dev: null };
  for (const [idx, round] of Object.entries(roundCols)) row.rounds[round] = num(cells[Number(idx)]);
  const devCell = devColIdx >= 0 ? cells[devColIdx] ?? "" : "";
  const dm = devCell.match(/([+−-])([\d.]+)%/);
  if (dm) row.dev = (dm[1] === "+" ? 1 : -1) * Number(dm[2]);
  docRows[file] = row;
}

// —— 生成趋势表 ——
const rounds = ROUND_POINTS.map((p) => p.round);
const latest = rounds[rounds.length - 1];
console.log(`# 守线偏差趋势（脚本生成，指令66 A2）\n`);
console.log(
  `| 图 | 采纳基线 | ${rounds.map((r) => `${r} 收尾`).join(" | ")} | 现行偏差（${latest} 收尾） | 判定 |`
);
console.log(`|---|---|${rounds.map(() => "---").join("|")}|---|---|`);
const nearHints = [];
for (const f of FILES) {
  const base = BASE[f];
  const dev = devOf(sizes[f][latest], base);
  let judge = "容差内";
  if (dev > TOL_HIGH || dev < TOL_LOW) judge = "已越沿";
  const distHigh = TOL_HIGH - dev;
  const distLow = dev - TOL_LOW;
  if (judge === "容差内" && (distHigh <= NEAR_PT || distLow <= NEAR_PT)) {
    judge = "逼近边缘";
    nearHints.push(
      `${f}：现行偏差 ${fmtDev(dev)}，距${distHigh <= distLow ? ` +5% 上沿 ${distHigh.toFixed(2)}pt` : ` −20% 下沿 ${distLow.toFixed(2)}pt`}（≤1.5pt 逼近区）`
    );
  }
  console.log(
    `| ${f} | ${fmt(base)} | ${rounds.map((r) => fmt(sizes[f][r])).join(" | ")} | ${fmtDev(dev)} | ${judge} |`
  );
}

// —— 与基线文档已记值逐值比对 ——
console.log(`\n## 与 docs/运维_性能基线.md 65-A5 已记值比对`);
let mismatch = 0;
const cmp = (label, a, b) => {
  const ok = a === b;
  if (!ok) mismatch++;
  console.log(`- ${ok ? "一致" : "不一致"}：${label}（文档=${a} / 脚本=${b}）`);
};
for (const f of FILES) {
  const d = docRows[f];
  if (!d) {
    mismatch++;
    console.log(`- 不一致：${f} 在基线文档趋势表中缺行`);
    continue;
  }
  cmp(`${f} 采纳基线`, d.base, BASE[f]);
  for (const r of rounds) {
    if (d.rounds[r] !== undefined) cmp(`${f} ${r} 收尾字节`, d.rounds[r], sizes[f][r]);
  }
  // 文档「现行偏差」记于表内最末收尾列（64 收尾）：以同轮脚本值比对
  const docRounds = Object.keys(d.rounds).map(Number);
  const docLast = Math.max(...docRounds);
  if (d.dev !== null) {
    const scriptDev = Math.round(devOf(sizes[f][docLast], BASE[f]) * 100) / 100;
    const ok = Math.abs(scriptDev - d.dev) < 0.005;
    if (!ok) mismatch++;
    console.log(`- ${ok ? "一致" : "不一致"}：${f} 现行偏差（文档=${fmtDev(d.dev)} / 脚本@${docLast}收尾=${fmtDev(scriptDev)}）`);
  }
}
console.log(mismatch === 0 ? `\n比对结论：全部逐值一致（0 处不一致）。` : `\n比对结论：${mismatch} 处不一致，见上，须人工裁处（脚本不改基线）。`);

// —— 逼近区显式提示 ——
if (subjectWarn.length) {
  console.log(`\n## 收尾点自检提示`);
  for (const w of subjectWarn) console.log(`- ⚠ ${w}`);
}
console.log(`\n## 逼近区提示（只提示，不改基线）`);
if (nearHints.length) for (const h of nearHints) console.log(`- ⚠ ${h}`);
else console.log(`- 无：无图进入距沿 ≤1.5pt 的逼近区。`);
