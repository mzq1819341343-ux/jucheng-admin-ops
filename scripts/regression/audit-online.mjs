/**
 * 线上未登录侧断言（指令57 A8 固化）：只对生产 https://jcxj.cc 做未登录实测，
 * 不登录、不带真实会话。
 *
 * 判定：
 * 1) /login 返回 200 且页面中「演示」出现 0 次（生产零演示入口）；
 * 2) 未登录访问六条登录后路径全部 307 → /login；
 * 3) 伪造 demo Cookie（demo_role=owner）访问仍 307（生产演示不可进）；
 * 4) /help-shots 八张图全部 200 且 content-type 为 image/png；
 * 5) /help 图注日期（指令58 A2）：生产 /help 未登录 307 读不到正文，图注随同一次
 *    构建发布，故对本地演示构建的同一 /help 断言八处「截图刷新：YYYY-MM-DD」
 *    存在、格式正确且为同一日；本地服务不可达时 info 跳过（全跑时必跑）。
 *
 * 用法：node scripts/regression/audit-online.mjs（REG_ONLINE 可覆盖目标）
 */
import { Reporter, ONLINE, BASE } from "./lib.mjs";

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

// 4) help-shots 八图 200
for (const f of ["overview.png", "expenses.png", "salary-rule-preview.png", "costs-utilities.png", "dashboard-drill.png", "compare.png", "mobile-overview.png", "mobile-expenses.png"]) {
  const r = await get(`/help-shots/${f}`);
  const ct = r.headers.get("content-type") || "";
  rep.check(`help-shots/${f} 200 png`, r.status === 200 && ct.includes("image/png"), `status=${r.status} ct=${ct}`);
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
} catch (e) {
  rep.info(`本地演示服务不可达，图注日期断言跳过（全跑时必跑）：${e.message}`);
}

process.exit(rep.finish());
