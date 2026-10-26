/**
 * 员工（staff）视角敏感信息 absent 审计（指令57 A8 固化，指令58 A3/A6 扩围）。
 *
 * 断言三轨：
 * - 文本轨：page.innerText（渲染后可见文本）不得包含敏感金额/口径串；
 * - 标签轨：敏感词不得作为「数据标签」渲染——即不得有叶子元素其完整文本
 *   等于该标签（区分数据标签与手册散文）；
 * - 禁用串轨（指令58 A3 督工裁定）：店员可见的业务页一律不得出现
 *   「无权限」「仅老板可见」「仅老板 / 合伙人可见」「敏感列已按权限隐藏」
 *   「已按权限隐藏」——无权限内容整行/整节不渲染，不挂占位标签或说明行。
 *   适用范围：店员可达的业务页（总览/仪表盘/报表/对比/工资/报销/成员/状态）；
 *   /help 为手册文档（权限矩阵与说明文字是文档内容本身），不在此轨范围。
 *
 * 另含：成员页店员整段不渲染（无「成员列表与业务授权」标题、无邮箱、无授权控件）；
 * 状态页工资对账行与抓取日志整节不渲染；
 * 成本/净利构成直进被拦回（URL 带 denied=，且目标页标志串不渲染）。
 * 指令60 A2：/status 扩为三角色渲染轨（对账卡三角色在位、工资对账行仅
 * 老板正例、抓取日志店员不渲染/合伙人与老板可见）。
 *
 * 用法：node scripts/regression/audit-staff.mjs（本地演示服务，staff 会话）
 */
import { launchBrowser, demoContext, Reporter, innerText, exactLabels, BASE } from "./lib.mjs";

const rep = new Reporter("audit-staff");
const browser = await launchBrowser();
let ctx = await demoContext(browser, { role: "staff", viewport: { width: 1440, height: 1000 } });

async function openPage(path) {
  const page = await ctx.newPage();
  const resp = await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 }).catch(() => null);
  await page.waitForTimeout(400);
  return { page, resp };
}

function absentText(name, text, list) {
  for (const s of list) rep.check(`${name} 文本 absent「${s}」`, !text.includes(s));
}
function absentRegex(name, text, list) {
  for (const re of list) rep.check(`${name} 文本 absent ${re}`, !re.test(text));
}
function absentLabels(name, labels, list) {
  for (const s of list) rep.check(`${name} 标签 absent「${s}」`, !labels.includes(s));
}

const NET_AMOUNTS = [/[-−]6,313/, /6,312\.51/, /[-−]11\.9%/];

// 指令58 A3 禁用串轨：占位标签/说明行一律不得对店员出现。
const FORBIDDEN = ["无权限", "仅老板可见", "仅老板 / 合伙人可见", "敏感列已按权限隐藏", "已按权限隐藏"];
function absentForbidden(name, text) {
  for (const s of FORBIDDEN) rep.check(`${name} 禁用串 absent「${s}」`, !text.includes(s));
}

// —— 总览 ——
{
  const { page, resp } = await openPage("/");
  rep.check("总览 HTTP 200", !!resp && resp.status() === 200);
  const t = await innerText(page);
  absentText("总览", t, ["扣成本后净利", "公司口径净利", "净利暂定", "工资占分润比", "利润率（分润）", "利润率（营收）", "年回报率", "工资成本"]);
  absentRegex("总览", t, NET_AMOUNTS);
  absentForbidden("总览", t);
  await page.close();
}

// —— 爱回收仪表盘 ——
{
  const { page } = await openPage("/business/aihuishou");
  const t = await innerText(page);
  absentText("仪表盘", t, ["净利暂定", "工资占分润比", "公司口径净利", "工资成本", "利润率（分润）"]);
  absentRegex("仪表盘", t, NET_AMOUNTS);
  absentForbidden("仪表盘", t);
  await page.close();
}

// —— 经营报表 ——
{
  const { page } = await openPage("/business/aihuishou/reports");
  const t = await innerText(page);
  const labels = await exactLabels(page);
  absentLabels("报表", labels, ["工资成本", "工资占分润比", "人均分润", "净利暂定（元）", "净利暂定·扣成本后（元）", "2026-09 净利暂定（元）", "扣成本前净利暂定"]);
  absentText("报表", t, ["单店净利暂定 =", "净利暂定未扣房租水电", "净利暂定仅最新月为完整口径", "目标达成复盘", "导出复盘 CSV", "导出复盘 Excel"]);
  absentRegex("报表", t, [/[-−]809\.45/, /[-−]5,503\.06/, /3,290\.59/, /1,580\.27/, /6,312\.51/]);
  absentForbidden("报表", t);
  await page.close();
}

// —— 两店对比 ——
{
  const { page } = await openPage("/business/aihuishou/compare");
  const t = await innerText(page);
  const labels = await exactLabels(page);
  absentLabels("对比", labels, ["门店成本", "净利暂定", "扣成本后净利暂定", "利润率（分润）", "利润率（营收）"]);
  absentRegex("对比", t, [/11,183\.37/, /[-−]5,503\.06/, /[-−]809\.45/, /[-−]35\.1%/]);
  absentForbidden("对比", t);
  await page.close();
}

// —— 工资（店员=我的薪酬） ——
{
  const { page } = await openPage("/salary");
  const t = await innerText(page);
  absentText("工资", t, ["工资实表明细", "双版本预览", "工资体系配置", "门店工资汇总", "244,757", "李冬浩", "王毅杰", "夏龙俊", "刘市伟"]);
  rep.check("工资 店员只见「我的薪酬」", t.includes("我的薪酬"));
  absentForbidden("工资", t);
  await page.close();
}

// —— 报销（店员=只看自己） ——
{
  const { page } = await openPage("/expenses");
  const t = await innerText(page);
  rep.check("报销 店员范围说明在位", t.includes("只显示你创建的报销单"));
  absentText("报销", t, ["全部明细", "全量汇总"]);
  absentForbidden("报销", t);
  await page.close();
}

// —— 成员页（店员=成员列表整段不渲染，指令58 A3 裁定） ——
{
  const { page } = await openPage("/members");
  const t = await innerText(page);
  const labels = await exactLabels(page);
  rep.check("成员页 店员无「成员列表与业务授权」标题", !labels.includes("成员列表与业务授权"));
  absentText("成员页", t, ["成员列表与业务授权仅老板", "成员管理为只读", "待邀请名单"]);
  absentRegex("成员页", t, [/[\w.+-]+@[\w-]+\.[\w.]+/]);
  const boxes = await page.locator('input[type="checkbox"]').count();
  rep.check("成员页 店员无授权控件", boxes === 0, `checkbox=${boxes}`);
  rep.check("成员页 店员仍见角色说明", t.includes("角色说明（三级）"));
  absentForbidden("成员页", t);
  await page.close();
}

// —— 状态页（店员=工资对账行整行不渲染、抓取日志整节不渲染，指令58 A3 裁定） ——
// 指令60 A2 扩围：/status 三角色渲染轨——「10 月对账」卡为状态展示卡（非敏感
// 数据），三角色都在位；工资对账行仅老板渲染、抓取日志节店员不渲染（合伙人
// 可见日志、不可见对账行），三轨对照固化，防后续改动悄悄改渲染范围。
{
  const { page, resp } = await openPage("/status");
  rep.check("状态页 HTTP 200", !!resp && resp.status() === 200);
  const t = await innerText(page);
  absentText("状态页", t, ["工资明细对账（实表 vs 月度汇总）", "最近抓取 / 导入日志", "请老板点"]);
  rep.check("状态页 店员 10 月对账卡在位", t.includes("10 月对账"));
  rep.check("状态页 店员 对账卡状态标在位", t.includes("need_user_login"));
  absentForbidden("状态页", t);
  await page.close();
}

// —— 直进拦截：门店成本 / 净利构成 ——
for (const [name, path, marker] of [
  ["成本直进", "/business/aihuishou/costs", "电费实报"],
  ["净利构成直进", "/business/aihuishou/breakdown", "净利构成"],
]) {
  const page = await ctx.newPage();
  await page.goto(BASE + path, { waitUntil: "networkidle", timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(400);
  const url = page.url();
  const t = await innerText(page);
  rep.check(`${name} 被拦回（denied）`, url.includes("denied="), url);
  rep.check(`${name} 目标页标志 absent「${marker}」`, !t.includes(marker));
  await page.close();
}

// ============================================================
// 合伙人轨（指令58 A6）：可见经营汇总，不可见明细与个人工资。
// ============================================================
await ctx.close();
ctx = await demoContext(browser, { role: "partner", viewport: { width: 1440, height: 1000 } });

function presentText(name, text, list) {
  for (const s of list) rep.check(`${name} 合伙人可见「${s}」`, text.includes(s));
}

// —— 总览（合伙人）：敏感汇总可见 ——
{
  const { page, resp } = await openPage("/");
  rep.check("合伙人 总览 HTTP 200", !!resp && resp.status() === 200);
  const t = await innerText(page);
  presentText("合伙人总览", t, ["扣成本后净利", "利润率（分润）"]);
  await page.close();
}

// —— 报表（合伙人）：敏感列与目标复盘可见 ——
{
  const { page } = await openPage("/business/aihuishou/reports");
  const t = await innerText(page);
  presentText("合伙人报表", t, ["工资成本", "目标达成复盘"]);
  await page.close();
}

// —— 工资（合伙人）：只看门店汇总，无个人明细/配置/实表 ——
{
  const { page } = await openPage("/salary");
  const t = await innerText(page);
  presentText("合伙人工资", t, ["门店月度工资汇总"]);
  absentText("合伙人工资", t, ["工资实表明细", "工资体系配置", "员工月度工资", "我的薪酬", "李冬浩", "王毅杰", "夏龙俊", "刘市伟", "244,757"]);
  await page.close();
}

// —— 报销（合伙人）：只有按月汇总，无明细行/类目/表单 ——
{
  const { page } = await openPage("/expenses");
  const t = await innerText(page);
  presentText("合伙人报销", t, ["按月汇总"]);
  absentText("合伙人报销", t, ["差旅", "采购", "物流", "新建报销"]);
  await page.close();
}

// —— 成员页（合伙人）：角色说明 + 中性只读说明，无邮箱/控件 ——
{
  const { page } = await openPage("/members");
  const t = await innerText(page);
  presentText("合伙人成员页", t, ["角色说明（三级）", "本页不展示成员明细"]);
  absentText("合伙人成员页", t, ["待邀请名单"]);
  absentRegex("合伙人成员页", t, [/[\w.+-]+@[\w-]+\.[\w.]+/]);
  const boxes = await page.locator('input[type="checkbox"]').count();
  rep.check("合伙人成员页 无授权控件", boxes === 0, `checkbox=${boxes}`);
  await page.close();
}

// —— 状态页（合伙人）：抓取日志可见；工资对账行按 A3 裁定不渲染 ——
{
  const { page } = await openPage("/status");
  const t = await innerText(page);
  presentText("合伙人状态页", t, ["最近抓取 / 导入日志", "10 月对账"]);
  absentText("合伙人状态页", t, ["工资明细对账（实表 vs 月度汇总）"]);
  await page.close();
}

// ============================================================
// 老板轨（指令60 A2）：/status 三角色渲染固化的老板侧——对账卡在位、
// 工资对账行仅老板渲染（本轨为正例）、抓取日志节可见。
// ============================================================
await ctx.close();
ctx = await demoContext(browser, { role: "owner", viewport: { width: 1440, height: 1000 } });

{
  const { page, resp } = await openPage("/status");
  rep.check("老板 状态页 HTTP 200", !!resp && resp.status() === 200);
  const t = await innerText(page);
  for (const s of ["10 月对账", "工资明细对账（实表 vs 月度汇总）", "最近抓取 / 导入日志"]) {
    rep.check(`老板状态页 老板可见「${s}」`, t.includes(s));
  }
  await page.close();
}

await browser.close();
process.exit(rep.finish());
