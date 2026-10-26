/**
 * 线上未登录侧断言（指令57 A8 固化）：只对生产 https://jcxj.cc 做未登录实测，
 * 不登录、不带真实会话。
 *
 * 判定：
 * 1) /login 返回 200 且页面中「演示」出现 0 次（生产零演示入口）；
 * 2) 未登录访问六条登录后路径全部 307 → /login；
 * 3) 伪造 demo Cookie（demo_role=owner）访问仍 307（生产演示不可进）；
 * 4) /help-shots 八张图全部 200 且 content-type 为 image/png。
 *
 * 用法：node scripts/regression/audit-online.mjs（REG_ONLINE 可覆盖目标）
 */
import { Reporter, ONLINE } from "./lib.mjs";

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

process.exit(rep.finish());
