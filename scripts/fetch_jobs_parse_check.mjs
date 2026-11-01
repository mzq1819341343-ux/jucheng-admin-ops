/**
 * 与 jucheng-admin-web lib/fetch-jobs.ts 的解析规则对齐。
 * 只覆盖 parseFetchJobCreate / parseFetchJobLatest / fetchLegLabel。
 * 前端那三个函数改接受形状时，这里一起改。
 *
 * 用法：node scripts/fetch_jobs_parse_check.mjs cases.json
 * cases.json：{ "cases": [{ "id", "op": "create"|"latest", "status", "body", "json" }] }
 * 标准输出：{ [id]: 解析结果 }
 */
import fs from "node:fs";

const FETCH_JOBS_NOT_READY = "任务接口未就绪";
const EMPTY_LEG = { ok: null, rows: null, detail: null };

function asRecord(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value;
}

function asJobId(value) {
  if (typeof value !== "string") return null;
  const id = value.trim();
  return id === "" ? null : id;
}

function fetchJobFailureMessage(status, action) {
  if (status === 0 || status === 404 || status === 405 || status === 501 || status === 502 || status === 503) {
    return FETCH_JOBS_NOT_READY;
  }
  if (status === 401) {
    return action === "dispatch" ? "未登录，无法下发抓取任务" : "未登录，无法读取抓取状态";
  }
  if (status === 403) {
    return action === "dispatch" ? "当前角色不能下发抓取任务" : "当前角色不能查看抓取状态";
  }
  if (status === 400) return "月份或范围不正确";
  return action === "dispatch" ? "任务下发失败" : "抓取状态读取失败";
}

function parseFetchJobCreate(status, body, json) {
  if (!json || status < 200 || status >= 300) {
    return { ok: false, message: fetchJobFailureMessage(json ? status : 0, "dispatch") };
  }
  const row = asRecord(body);
  const jobId = asJobId(row?.job_id) ?? asJobId(row?.jobId);
  if (!jobId) return { ok: false, message: "下发没有返回任务号" };
  return { ok: true, jobId };
}

function asRows(value) {
  const n = typeof value === "number" ? value : typeof value === "string" && value.trim() !== "" ? Number(value) : Number.NaN;
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n);
}

function parseLeg(value) {
  const row = asRecord(value);
  if (!row) return { ...EMPTY_LEG };
  let ok = null;
  let detail = null;
  if (typeof row.ok === "boolean") ok = row.ok;
  else if (row.status === "ok" || row.status === "success") ok = true;
  else if (row.status === "failed" || row.status === "error") ok = false;
  else if (row.status === "running" || row.status === "pending") detail = "进行中";
  const rows = asRows(row.rows ?? row.row_count ?? row.count);
  if (detail === null) {
    if (typeof row.error === "string" && row.error.trim() !== "") detail = row.error.trim();
    else if (typeof row.message === "string" && row.message.trim() !== "") detail = row.message.trim();
  }
  return { ok, rows, detail };
}

function parseAt(row) {
  for (const key of ["finished_at", "updated_at", "at", "created_at", "last_at"]) {
    const value = row[key];
    if (typeof value === "string" && value.trim() !== "") return value;
  }
  return null;
}

function legHasSignal(leg) {
  return leg.ok !== null || leg.rows !== null || (leg.detail !== null && leg.detail !== "");
}

function parseFetchJobLatest(status, body, json) {
  if (status === 204) {
    return { ok: true, latest: { jobId: null, at: null, used: { ...EMPTY_LEG }, accessories: { ...EMPTY_LEG }, empty: true } };
  }
  if (!json || status < 200 || status >= 300) {
    return { ok: false, message: fetchJobFailureMessage(json ? status : 0, "status") };
  }
  const root = asRecord(body);
  const wrapped = root && "latest" in root ? root.latest : body;
  if (wrapped == null) {
    return { ok: true, latest: { jobId: null, at: null, used: { ...EMPTY_LEG }, accessories: { ...EMPTY_LEG }, empty: true } };
  }
  const row = asRecord(wrapped);
  if (!row) return { ok: false, message: fetchJobFailureMessage(0, "status") };
  const used = parseLeg(row.used ?? row.old_machine ?? row.oldMachine);
  const accessories = parseLeg(row.accessories ?? row.accessory);
  const jobId = asJobId(row.job_id) ?? asJobId(row.jobId);
  const at = parseAt(row);
  const empty = jobId === null && at === null && !legHasSignal(used) && !legHasSignal(accessories);
  return { ok: true, latest: { jobId, at, used, accessories, empty } };
}

function fetchLegLabel(leg) {
  const outcome =
    leg.detail === "进行中" ? "进行中" : leg.ok === true ? "成功" : leg.ok === false ? "失败" : "未返回";
  const rows = leg.rows === null ? "行数未返回" : `${leg.rows} 行`;
  const extra = leg.detail && leg.detail !== "进行中" ? `（${leg.detail}）` : "";
  return `${outcome} · ${rows}${extra}`;
}

function runCase(item) {
  if (item.op === "create") {
    return parseFetchJobCreate(item.status, item.body, item.json);
  }
  const parsed = parseFetchJobLatest(item.status, item.body, item.json);
  if (!parsed.ok) return parsed;
  return {
    ...parsed,
    usedLabel: fetchLegLabel(parsed.latest.used),
    accessoriesLabel: fetchLegLabel(parsed.latest.accessories),
  };
}

const input = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const out = {};
for (const item of input.cases) out[item.id] = runCase(item);
process.stdout.write(JSON.stringify(out));
