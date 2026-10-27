/**
 * 工资晋升 dry-run 预期校验（指令63 A6）：只读留痕防口径漂移。
 *
 * 一键跑全部三份样本（sample-data / sample-boundary / sample-empty），
 * 把 dry-run.mjs 的控制台报告与同目录 expected.json 的期望关键行逐行
 * 比对（整行精确相等），逐样本输出 PASS/FAIL，任一 FAIL 退出码 1。
 * 本脚本与 dry-run 一样零写入：不连库、不发网络请求、不写任何文件。
 *
 * 用法：node scripts/salary-promotion/verify-expectations.mjs
 *   或 npm run salary:verify（仓库根目录）
 */
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const expected = JSON.parse(readFileSync(join(here, "expected.json"), "utf8"));

let allOk = true;
for (const [file, lines] of Object.entries(expected.samples)) {
  const input = join(here, file);
  let out;
  try {
    out = execFileSync(process.execPath, [join(here, "dry-run.mjs"), "--input", input], {
      encoding: "utf8",
    });
  } catch (e) {
    console.log(`FAIL ${file}：dry-run 执行失败：${e.message}`);
    allOk = false;
    continue;
  }
  const actualLines = new Set(out.split("\n").map((l) => l.replace(/\r$/, "")));
  const missing = lines.filter((l) => !actualLines.has(l));
  if (missing.length === 0) {
    console.log(`PASS ${file}：期望关键行 ${lines.length} 行全对位`);
  } else {
    allOk = false;
    console.log(`FAIL ${file}：${missing.length}/${lines.length} 行期望未出现（口径或样本已变，须人工确认后同步 expected.json）：`);
    for (const m of missing) console.log(`  缺：${m}`);
  }
}
console.log(allOk ? "[salary-verify] 全部 PASS" : "[salary-verify] 存在 FAIL");
process.exit(allOk ? 0 : 1);
