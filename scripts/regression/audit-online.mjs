/**
 * 线上未登录侧断言（指令57 A8 固化）：只对生产 https://jcxj.cc 做未登录实测，
 * 不登录、不带真实会话。
 *
 * 判定：
 * 1) /login 返回 200 且页面中「演示」出现 0 次（生产零演示入口）；
 * 2) 未登录访问六条登录后路径全部 307 → /login；
 * 3) 伪造 demo Cookie（demo_role=owner）访问仍 307（生产演示不可进）；
 * 4) /help-shots 八张图全部 200 且 content-type 为 image/png，并逐图体积
 *    守线（指令62 A2：≤ 61-A5 采纳基线 ×1.3）；指令63 A2 增基线防漂移
 *    自检：上限须恒 = ceil(采纳基线 ×1.3)，且仓内实物体积与采纳基线
 *    偏差须在容差内（实物已显著偏离 = 「基线过期」，须重定基线）；
 * 5) /help 图注日期（指令58 A2）：生产 /help 未登录 307 读不到正文，图注随同一次
 *    构建发布，故对本地演示构建的同一 /help 断言八处「截图刷新：YYYY-MM-DD」
 *    存在、格式正确且为同一日；指令64 A2 增「与运行当日（Asia/Shanghai）
 *    一致」兜底断言（shots 已自动对齐当日，防图注过期混过）；本地服务
 *    不可达时 info 跳过（全跑时必跑）；
 * 7) 三处同数（指令64 A6）：/help 引用图数 = SHOT_SIZE_BASE 条目数 =
 *    public/help-shots 实物文件数，防加图/删图漏同步任一侧。
 *
 * 用法：node scripts/regression/audit-online.mjs（REG_ONLINE 可覆盖目标）
 */
import { Reporter, ONLINE, BASE } from "./lib.mjs";
import { statSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const rep = new Reporter("audit-online");

async function get(path, headers = {}) {
  return fetch(ONLINE + path, { redirect: "manual", headers });
}

// 1) 登录页零演示
{
  const r = await get("/login");
  const body = await r.text();
  rep.check("/login HTTP 200", r.status === 200, `status=${r.status}`);
  const n = body.split("演示").length - 1;
  rep.check("/login 「演示」出现 0 次", n === 0, `count=${n}`);
}

// 2) 六路径未登录 307
for (const p of ["/", "/expenses", "/business/aihuishou", "/salary", "/help", "/status"]) {
  const r = await get(p);
  const loc = r.headers.get("location") || "";
  rep.check(`未登录 ${p} → 307 /login`, r.status === 307 && loc.includes("/login"), `status=${r.status} loc=${loc}`);
}

// 3) 伪造 demo Cookie 仍 307
for (const p of ["/", "/salary"]) {
  const r = await get(p, { cookie: "demo_role=owner; demo_biz=all" });
  rep.check(`伪造 demo Cookie ${p} → 307`, r.status === 307, `status=${r.status}`);
}

// 4) help-shots 八图 200 + 体积守线（指令62 A2）
// 守线基线 = 指令61 A5 采纳量化后体积（2026-10-27 实测 public/help-shots，
// 与生产逐字节一致），上限 = ceil(基线 × 1.3)；防 shots 刷新忘量化导致
// 体积回涨无人察觉。基线变更只在「重新量化采纳」时随图一起改并注明轮次。
// 采纳基线独立成表（指令63 A2），与上限表互为自检对象，改一处即被下节抓出。
const SHOT_SIZE_BASE = {
  "overview.png": 97684, // 61-A5
  "expenses.png": 95302, // 61-A5
  "salary-rule-preview.png": 17630, // 61-A5
  "costs-utilities.png": 37695, // 71-A7：随 71-A4 电费区界面变化（未录月份点选行+说明增补）重拍采纳
  "dashboard-drill.png": 31218, // 171 收尾：打码去真数后重新量化采纳（2026-10-29）
  "compare.png": 87218, // 61-A5
  "mobile-overview.png": 149551, // 60-A3
  "mobile-expenses.png": 154082, // 60-A3
};
const SHOT_SIZE_CAPS = {
  "overview.png": 126990,
  "expenses.png": 123893,
  "salary-rule-preview.png": 22919,
  "costs-utilities.png": 49004,
  "dashboard-drill.png": 40584, // = ceil(31218×1.3)
  "compare.png": 113384,
  "mobile-overview.png": 194417,
  "mobile-expenses.png": 200307,
};
for (const [f, cap] of Object.entries(SHOT_SIZE_CAPS)) {
  const r = await get(`/help-shots/${f}`);
  const ct = r.headers.get("content-type") || "";
  rep.check(`help-shots/${f} 200 png`, r.status === 200 && ct.includes("image/png"), `status=${r.status} ct=${ct}`);
  const bytes = r.status === 200 ? (await r.arrayBuffer()).byteLength : 0;
  rep.check(`help-shots/${f} 体积守线 ≤${cap}B`, bytes > 0 && bytes <= cap, `bytes=${bytes} cap=${cap}`);
}

// 4b) 守线基线防漂移自检（指令63 A2）：
// (i) 上限恒须 = ceil(采纳基线 × 1.3)——上限或基线任一被手改即 FAIL；
// (ii) 仓内实物体积（public/help-shots，本地可读时）与采纳基线偏差须在
//      容差 [+5%, −20%] 内（容差吸收日期驱动的内容翻转，如超期徽标增减）；
//      实物显著偏离基线 = 「基线过期」FAIL，提示按新采纳体积重定基线。
//      本地文件不可读（如脱离仓库运行）时该项 info 跳过，(i) 恒跑。
{
  const shotsDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "public", "help-shots");
  for (const [f, base] of Object.entries(SHOT_SIZE_BASE)) {
    const cap = SHOT_SIZE_CAPS[f];
    const expect = Math.ceil(base * 1.3);
    rep.check(
      `守线自检 ${f}：上限 = ceil(基线×1.3)`,
      cap === expect,
      `基线过期或常量被改：cap=${cap} 基线=${base} 应为上限=${expect}`
    );
    let actual = null;
    try {
      actual = statSync(join(shotsDir, f)).size;
    } catch {
      actual = null;
    }
    if (actual === null) {
      rep.info(`守线自检 ${f}：仓内实物不可读，实物对位跳过`);
      continue;
    }
    const inTol = actual <= Math.ceil(base * 1.05) && actual >= Math.floor(base * 0.8);
    rep.check(
      `守线自检 ${f}：实物 ${actual}B 与采纳基线偏差在容差内`,
      inTol,
      `基线过期：实物=${actual}B 基线=${base}B（容差 +5%/−20%），请按新采纳体积重定 SHOT_SIZE_BASE 与 SHOT_SIZE_CAPS`
    );
  }
}

// 5) /help 图注日期存在且格式正确（指令58 A2；对本地演示构建的同一 /help 断言）
try {
  const r = await fetch(BASE + "/help", { headers: { cookie: "demo_role=owner; demo_biz=all" } });
  if (r.status !== 200) throw new Error(`status=${r.status}`);
  const html = await r.text();
  // 剥离 <script>（RSC 载荷会把正文再嵌一份），只数渲染正文里的图注。
  const bodyHtml = html.replace(/<script[\s\S]*?<\/script>/g, "");
  const dates = [...bodyHtml.matchAll(/截图刷新：(\d{4})-(\d{2})-(\d{2})/g)].map((m) => m[0].replace("截图刷新：", ""));
  rep.check("/help 图注「截图刷新」共 8 处", dates.length === 8, `count=${dates.length}`);
  rep.check(
    "/help 图注日期格式 YYYY-MM-DD 且八处同一日",
    dates.length === 8 && dates.every((d) => d === dates[0]),
    dates.join(",")
  );
  // 指令64 A2：图注须与运行当日（Asia/Shanghai）一致——同日断言防不住
  // 「八处同为过期日期」整批混过，当日兜底把图注新鲜度钉在运行日。
  const todayCST = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai" }).format(new Date());
  rep.check(
    "/help 图注日期与运行当日（Asia/Shanghai）一致",
    dates.length === 8 && dates.every((d) => d === todayCST),
    `运行当日=${todayCST} 图注=${dates[0] ?? "无"}`
  );
} catch (e) {
  rep.info(`本地演示服务不可达，图注日期断言跳过（全跑时必跑）：${e.message}`);
}

// 6) 生产恒忽略演示注入（指令59 A2）：带 demo_targets 注入 Cookie 仍 307
{
  const inj = Buffer.from(
    JSON.stringify([{ month: "2026-09", metric: "profit", target_value: 1 }]),
    "utf8"
  ).toString("base64url");
  for (const p of ["/", "/business/aihuishou/reports"]) {
    const r = await get(p, { cookie: `demo_role=owner; demo_biz=all; demo_targets=${inj}` });
    rep.check(`伪造 demo_targets 注入 ${p} → 307`, r.status === 307, `status=${r.status}`);
  }
  // 指令66 A5 缺口补强：(i) /login 带伪造 demo Cookie 仍零演示入口
  // （§1 原仅测无 Cookie 态）；(ii) demo_targets 单独出现（无 demo_role）
  // 不得单独成事——targets 分支以 user.demo 为前提，角色闸门在前。
  {
    const r = await get("/login", { cookie: "demo_role=owner; demo_biz=all" });
    const body = await r.text();
    rep.check("伪造 demo Cookie /login HTTP 200", r.status === 200, `status=${r.status}`);
    const n = body.split("演示").length - 1;
    rep.check("伪造 demo Cookie /login 「演示」出现 0 次", n === 0, `count=${n}`);
  }
  {
    const r = await get("/business/aihuishou/reports", { cookie: `demo_targets=${inj}` });
    rep.check("仅 demo_targets（无 demo_role）/reports → 307", r.status === 307, `status=${r.status}`);
  }
}

// 7) 三处同数（指令64 A6）：/help 引用图数 = SHOT_SIZE_BASE 条目数 = 实物文件数。
// 加图/删图须三处同步（页面引用、守线基线、实物文件），任一侧漏改即 FAIL。
{
  const shotsDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "public", "help-shots");
  const baseCount = Object.keys(SHOT_SIZE_BASE).length;
  let files = null;
  try {
    files = readdirSync(shotsDir).filter((f) => f.endsWith(".png"));
  } catch {
    files = null;
  }
  if (files === null) {
    rep.info("三处同数：public/help-shots 不可读，实物侧跳过（脱离仓库运行时）");
  } else {
    rep.check(
      "三处同数：SHOT_SIZE_BASE 条目数 = help-shots 实物文件数",
      baseCount === files.length,
      `基线=${baseCount} 实物=${files.length}（${files.join(",")}）`
    );
  }
  try {
    const r = await fetch(BASE + "/help", { headers: { cookie: "demo_role=owner; demo_biz=all" } });
    if (r.status !== 200) throw new Error(`status=${r.status}`);
    const html = await r.text();
    const bodyHtml = html.replace(/<script[\s\S]*?<\/script>/g, "");
    const refs = new Set([...bodyHtml.matchAll(/help-shots\/([\w.-]+\.png)/g)].map((m) => m[1]));
    rep.check(
      "三处同数：/help 引用图数 = SHOT_SIZE_BASE 条目数",
      refs.size === baseCount,
      `引用=${refs.size} 基线=${baseCount}（${[...refs].join(",")}）`
    );
  } catch (e) {
    rep.info(`本地演示服务不可达，三处同数页面侧跳过（全跑时必跑）：${e.message}`);
  }
}

process.exit(rep.finish());
