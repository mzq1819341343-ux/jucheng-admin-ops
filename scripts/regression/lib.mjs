/**
 * 回归脚本公共库（指令57 A8 固化）。
 *
 * - 浏览器：playwright-core + 本机 chromium headless shell。
 *   可执行文件解析顺序：环境变量 CHROMIUM_PATH → ~/.cache/ms-playwright 下的
 *   chrome-headless-shell / chrome → 交给 playwright 默认解析（会给出明确报错）。
 * - 前端检出：REG_WEB_ROOT（必填，绝对路径或相对当前工作目录的路径），指向
 *   jucheng-admin-web 本地检出。app/、public/、lib/ 只从这里读/写。
 *   未设置或空白 → 退出码 2，并打印中文说明。
 * - 本地被测服务：REG_BASE（默认 http://localhost:3100），须为
 *   NEXT_PUBLIC_ALLOW_DEMO=1 的生产构建（见 docs/运维_回归清单.md）。
 *   HTTP 审计只用 REG_BASE / REG_ONLINE，不把它们当成磁盘路径。
 * - 线上被测：REG_ONLINE（默认 https://jcxj.cc），只做未登录侧断言。
 */
import { chromium } from "playwright-core";
import { existsSync, readdirSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

function resolveWebRoot() {
  const raw = process.env.REG_WEB_ROOT;
  if (raw == null || String(raw).trim() === "") {
    console.error(
      "错误：未设置 REG_WEB_ROOT（或为空）。请设为 jucheng-admin-web 本地检出目录：绝对路径，或相对当前工作目录的路径。app/、public/、lib/ 只从该目录读取。示例：REG_WEB_ROOT=../jucheng-admin-web"
    );
    process.exit(2);
  }
  const webRoot = resolve(String(raw).trim());
  let st;
  try {
    st = statSync(webRoot);
  } catch {
    console.error(
      `错误：REG_WEB_ROOT 指向的目录不存在：${webRoot}（原值 ${String(raw).trim()}）。请改为本机 jucheng-admin-web 检出路径。`
    );
    process.exit(2);
  }
  if (!st.isDirectory()) {
    console.error(`错误：REG_WEB_ROOT 不是目录：${webRoot}`);
    process.exit(2);
  }
  return webRoot;
}

/** jucheng-admin-web 检出根目录（已解析为绝对路径）。 */
export const WEB_ROOT = resolveWebRoot();

/** 拼前端仓内路径，例如 webPath("public", "help-shots")。 */
export function webPath(...parts) {
  return join(WEB_ROOT, ...parts);
}

export const BASE = process.env.REG_BASE || "http://localhost:3100";
export const ONLINE = process.env.REG_ONLINE || "https://jcxj.cc";

export function resolveChromium() {
  if (process.env.CHROMIUM_PATH && existsSync(process.env.CHROMIUM_PATH)) {
    return process.env.CHROMIUM_PATH;
  }
  const root = join(homedir(), ".cache", "ms-playwright");
  if (existsSync(root)) {
    const dirs = readdirSync(root).sort().reverse();
    for (const d of dirs) {
      for (const rel of [
        "chrome-headless-shell-linux64/chrome-headless-shell",
        "chrome-linux/chrome",
        "chrome-linux64/chrome",
      ]) {
        const p = join(root, d, rel);
        if (existsSync(p)) return p;
      }
    }
  }
  return undefined;
}

export async function launchBrowser() {
  const executablePath = resolveChromium();
  return chromium.launch({
    ...(executablePath ? { executablePath } : {}),
    args: ["--no-sandbox"],
  });
}

/** 演示会话 context：demo_role / demo_biz Cookie（仅本地演示构建有效，生产恒 307）。
 * theme 可选：经 addInitScript 在页面脚本运行前预置 localStorage
 * `jucheng-theme`（应用的预绘制恢复脚本据此设 data-theme），供主题双轨审计。 */
export async function demoContext(browser, { role = "owner", biz = "all", viewport, mobile = false, theme } = {}) {
  const ctx = await browser.newContext({
    viewport,
    ...(mobile ? { deviceScaleFactor: 2, isMobile: true, hasTouch: true } : {}),
  });
  await ctx.addCookies([
    { name: "demo_role", value: role, url: BASE },
    { name: "demo_biz", value: biz, url: BASE },
  ]);
  if (theme) {
    await ctx.addInitScript((t) => {
      try {
        localStorage.setItem("jucheng-theme", t);
      } catch (e) {}
    }, theme);
  }
  return ctx;
}

/** 逐项断言收集器：printCheck 记录，finish 打印汇总并返回退出码（0=全过）。 */
export class Reporter {
  constructor(title) {
    this.title = title;
    this.pass = 0;
    this.fail = 0;
    this.failures = [];
  }
  check(desc, ok, detail = "") {
    if (ok) {
      this.pass += 1;
      console.log(`  PASS ${desc}`);
    } else {
      this.fail += 1;
      this.failures.push(desc);
      console.log(`  FAIL ${desc}${detail ? ` —— ${detail}` : ""}`);
    }
  }
  info(msg) {
    console.log(`  info ${msg}`);
  }
  finish() {
    console.log(`[${this.title}] pass=${this.pass} fail=${this.fail}`);
    if (this.failures.length) console.log(`  failures: ${this.failures.join(" | ")}`);
    return this.fail === 0 ? 0 : 1;
  }
}

export async function innerText(page) {
  return page.evaluate(() => document.body.innerText);
}

/** 元素「精确标签」集合：某字符串是否为某个叶子元素的完整文本（区分数据标签与说明句散文）。 */
export async function exactLabels(page) {
  return page.evaluate(() => {
    const set = new Set();
    for (const el of document.querySelectorAll("body *")) {
      if (el.children.length === 0) {
        const t = (el.textContent || "").trim();
        if (t && t.length <= 40) set.add(t);
      }
    }
    return [...set];
  });
}
