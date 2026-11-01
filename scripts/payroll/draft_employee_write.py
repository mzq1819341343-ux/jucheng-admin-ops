#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""员工保存后的当月草案占位，以及底薪变更后的当月同步。

两条写路径都只碰 public.salary_realtime_draft。默认不写库。
真正写库要同时成立：
  1. 环境变量 ALLOW_EMPLOYEE_DRAFT_WRITE=true（独立闸，不读灌数闸 ALLOW_APPLY_REALTIME_DRAFT）
  2. apply=True / 命令行 --apply
只开 ALLOW_APPLY_REALTIME_DRAFT 不放行本路径。灌数脚本的双闸保持原样。
pipeline_config.json 的 UPLOAD / APPLY 保持 false。本模块只读这两键，不改文件，
也不靠它们放行。

十月禁灌：目标月 2026-10 直接拒绝，ALLOW_EMPLOYEE_DRAFT_WRITE 与 --apply 开齐也不写。
生产禁盲开：没有第三道「生产放行」开关。密钥只从环境读取，不进 argv、不进仓库。

占位（在职保存成功后）：
  base_amount = 本次员工表 base_salary。上门与门店都跟这个数，不因角色改成 0。
  door / online / store / accessories = 0。
  subsidy_amount / bonus_amount = 0（只在新建时）。
  source = employee_placeholder（migration_011 已有 source 列）。
  caliber 标明 pending_payroll，表示待计薪。
  冲突键 (month, person_name)，Prefer resolution=ignore-duplicates（ON CONFLICT DO NOTHING）。
  已有草案行不读取后覆盖，也不发 merge。

底薪同步（底薪改完后）：
  只更新 base_amount，并按 apply 的 recompute_payable 重算 gross_amount / net_amount。
  不把 subsidy_amount / bonus_amount / 业绩列放进 PATCH。
  当月没有行：先走占位插入，再按读回的行写底薪（读回若已有手填补贴，仍保留）。

本模块不算薪、不抓数、不写 migration、不写 employees。
web 在 Vercel 用服务端 Route 按同契约直写 draft（service role），不必在线上跑本 CLI。
CLI 留给本机 / ops。行为字段与占位、同步规则不变。
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO_ROOT / "docs" / "fetch-pipeline" / "pipeline_config.json"
APPLY_PATH = REPO_ROOT / "scripts" / "apply_realtime_payroll_draft.py"
PAYROLL_PATH = REPO_ROOT / "scripts" / "payroll" / "realtime_payroll.py"

ENV_GATE = "ALLOW_EMPLOYEE_DRAFT_WRITE"
OCTOBER_NO_SEED = "2026-10"
SOURCE_PLACEHOLDER = "employee_placeholder"
CALIBER_BASE = "employee_placeholder|pending_payroll|base_follows_table"
ROLES = ("上门", "门店")
EMPLOYMENT_OK = "在职"
EMPLOYMENT_LEFT = "离职"

DRAFT_SELECT = (
    "person_name,user_id,base_amount,door_amount,online_amount,store_amount,"
    "accessories_amount,subsidy_amount,bonus_amount,deduction_amount,"
    "gross_amount,net_amount,source,caliber"
)
EMPLOYEES_SELECT = "person_name,work_role,base_salary,employment_status,user_id"
PATCH_KEYS = ("base_amount", "gross_amount", "net_amount", "as_of", "updated_at")
FORBIDDEN_PATCH_KEYS = (
    "subsidy_amount", "bonus_amount", "door_amount", "online_amount",
    "store_amount", "accessories_amount", "deduction_amount",
    "source", "caliber", "month", "person_name", "user_id", "id",
)
INSERT_AMOUNT_ZEROS = (
    "door_amount", "online_amount", "store_amount", "accessories_amount",
    "subsidy_amount", "bonus_amount",
)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")

_CACHE = {}


class DraftWriteRejected(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def apply_mod():
    if "apply" not in _CACHE:
        spec = importlib.util.spec_from_file_location(
            "jucheng_apply_realtime_payroll_draft", APPLY_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CACHE["apply"] = mod
    return _CACHE["apply"]


def payroll_mod():
    if "pay" not in _CACHE:
        spec = importlib.util.spec_from_file_location(
            "jucheng_realtime_payroll_caliber", PAYROLL_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CACHE["pay"] = mod
    return _CACHE["pay"]


def money(value):
    return apply_mod().money(value)


def money_text(value):
    return format(money(value), "f")


def guard_write_table(table):
    try:
        return apply_mod().guard_table(table)
    except RuntimeError as exc:
        raise DraftWriteRejected(str(exc), code=1)


def env_gate_open(env):
    """占位与底薪同步的独立闸。不读 ALLOW_APPLY_REALTIME_DRAFT。"""
    if env is None:
        env = os.environ
    return str(env.get(ENV_GATE, "")).strip().lower() == "true"


def _https_base(base_url):
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith("https://"):
        raise DraftWriteRejected("SUPABASE_URL 须为 https", code=1)
    return base


def _service_key(service_key):
    key = (service_key or "").strip()
    if not key:
        raise DraftWriteRejected("缺少 SUPABASE_SERVICE_ROLE_KEY", code=1)
    return key


def _check_url(url, allowed_paths):
    path = urllib.parse.urlparse(url).path
    if path not in allowed_paths:
        raise DraftWriteRejected("请求路径越界，已中止未写库", code=1)
    return url


def filter_eq(column, value):
    return "%s=eq.%s" % (column, urllib.parse.quote(str(value), safe=""))


def _auth_headers(req, service_key):
    req.add_header("apikey", service_key)
    req.add_header("Authorization", "Bearer " + service_key)
    req.add_header("Accept", "application/json")
    return req


def credentials(env):
    env = env or {}
    url = (env.get("SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    if not url or not key:
        raise DraftWriteRejected(
            "%s=true 与 --apply 已同时成立，但缺少 SUPABASE_URL / "
            "SUPABASE_SERVICE_ROLE_KEY，未写库" % ENV_GATE, code=1)
    return url, key


def resolve_month(explicit, now, tz_name):
    try:
        month, explicit_flag = apply_mod().resolve_target_month(explicit, now, tz_name)
    except RuntimeError as exc:
        raise DraftWriteRejected(str(exc), code=1)
    effective = payroll_mod().EFFECTIVE_FROM
    if month < effective:
        raise DraftWriteRejected(
            "月份 %s 早于生效月 %s，不写占位、不同步底薪" % (month, effective), code=2)
    if month == OCTOBER_NO_SEED:
        raise DraftWriteRejected(
            "十月禁灌：%s 不写 salary_realtime_draft 占位、不同步底薪。生产禁盲开。"
            % month, code=2)
    return month, explicit_flag


def as_of_iso(now, tz_name):
    tz = ZoneInfo(tz_name or "Asia/Shanghai")
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    return now.replace(microsecond=0).isoformat()


def parse_base_salary(value):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise DraftWriteRejected("base_salary 不是合法金额", code=1)
    try:
        raw = Decimal(str(value).replace(",", "").strip())
    except Exception:
        raise DraftWriteRejected("base_salary 不是合法金额", code=1)
    if raw < 0:
        raise DraftWriteRejected("base_salary 不能为负", code=1)
    return money(raw)


def parse_user_id(value):
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    if not UUID_RE.match(text):
        raise DraftWriteRejected("user_id 不是 UUID", code=1)
    return text


def normalize_employee(raw):
    """只认调用方给来的员工字段。底薪不经过算薪脚本，上门也不改成 0。"""
    if not isinstance(raw, dict):
        raise DraftWriteRejected("员工参数须为对象", code=1)
    name = str(raw.get("person_name") or raw.get("name") or "").strip()
    user_id = parse_user_id(raw.get("user_id"))
    if not name and not user_id:
        raise DraftWriteRejected("需要 person_name 或 user_id", code=1)
    role = raw.get("work_role", raw.get("role"))
    if role is None or str(role).strip() == "":
        role = None
    else:
        role = str(role).strip()
        if role not in ROLES:
            raise DraftWriteRejected("work_role 只能是上门或门店", code=1)
    status_raw = raw.get("employment_status")
    status_provided = status_raw is not None and str(status_raw).strip() != ""
    if not status_provided:
        status = EMPLOYMENT_OK
    else:
        status = str(status_raw).strip()
        if status not in (EMPLOYMENT_OK, EMPLOYMENT_LEFT):
            raise DraftWriteRejected("无法识别的 employment_status", code=1)
    base_provided = "base_salary" in raw and raw.get("base_salary") is not None and str(
        raw.get("base_salary")).strip() != ""
    base = parse_base_salary(raw.get("base_salary")) if base_provided else None
    if name and name in payroll_mod().HARD_EXCLUDED:
        raise DraftWriteRejected("硬排除人员 %s，已中止未写库" % name, code=3)
    return {
        "person_name": name,
        "user_id": user_id,
        "work_role": role,
        "employment_status": status,
        "status_provided": status_provided,
        "base_salary": base,
        "base_provided": base_provided,
    }


def overlay_employee(fetched, cli):
    """CLI 上显式给过的字段覆盖表读数。没给的才用 employees 行。"""
    merged = {}
    if fetched:
        merged.update(fetched)
    for key, value in (cli or {}).items():
        if key == "base_salary" and not cli.get("base_provided"):
            continue
        if key == "employment_status" and not cli.get("status_provided"):
            continue
        if value is None or (isinstance(value, str) and value.strip() == ""):
            continue
        if key in ("base_provided", "status_provided"):
            continue
        merged[key] = value
    if cli and cli.get("base_provided"):
        merged["base_salary"] = cli["base_salary"]
    return normalize_employee(merged)


def build_caliber(work_role):
    if work_role:
        return CALIBER_BASE + "|work_role=%s" % work_role
    return CALIBER_BASE


def payable_texts(base, door, online, store, accessories, subsidy, bonus, deduction, as_of):
    """与 apply_realtime_payroll_draft.recompute_payable 同一函数，避免两套应发。"""
    apply = apply_mod()
    row = {key: "0.00" for key in apply.UPSERT_COLUMNS}
    row.update({
        "month": "2026-11",
        "person_name": "_",
        "as_of": as_of,
        "updated_at": as_of,
        "caliber": CALIBER_BASE,
        "source": SOURCE_PLACEHOLDER,
        "base_amount": money_text(base),
        "door_amount": money_text(door),
        "online_amount": money_text(online),
        "store_amount": money_text(store),
        "accessories_amount": money_text(accessories),
        "subsidy_amount": money_text(subsidy),
        "bonus_amount": money_text(bonus),
        "deduction_amount": money_text(deduction),
    })
    apply.recompute_payable(row)
    return row["gross_amount"], row["net_amount"]


def build_placeholder_row(employee, month, as_of):
    if employee.get("base_salary") is None:
        raise DraftWriteRejected("缺少 base_salary", code=1)
    if not employee.get("person_name"):
        raise DraftWriteRejected("缺少 person_name", code=1)
    base = employee["base_salary"]
    gross, net = payable_texts(base, 0, 0, 0, 0, 0, 0, 0, as_of)
    row = {
        "month": month,
        "person_name": employee["person_name"],
        "as_of": as_of,
        "base_amount": money_text(base),
        "door_amount": "0.00",
        "online_amount": "0.00",
        "store_amount": "0.00",
        "accessories_amount": "0.00",
        "subsidy_amount": "0.00",
        "bonus_amount": "0.00",
        "gross_amount": gross,
        "deduction_amount": "0.00",
        "net_amount": net,
        "caliber": build_caliber(employee.get("work_role")),
        "source": SOURCE_PLACEHOLDER,
        "updated_at": as_of,
    }
    if employee.get("user_id"):
        row["user_id"] = employee["user_id"]
    if row["source"] != SOURCE_PLACEHOLDER:
        raise DraftWriteRejected("占位 source 异常", code=1)
    for key in INSERT_AMOUNT_ZEROS:
        if row[key] != "0.00":
            raise DraftWriteRejected("占位业绩或补贴列不是 0", code=1)
    return row


def read_amount(record, keys, label):
    if not isinstance(record, dict):
        raise DraftWriteRejected("已有草案行不是对象，已中止未写库", code=1)
    for key in keys:
        if key not in record:
            continue
        raw = record[key]
        if raw is None or str(raw).strip() == "":
            return Decimal("0.00")
        try:
            return money(raw)
        except Exception:
            raise DraftWriteRejected(
                "已有草案的 %s 不是金额，已中止未写库" % label, code=1)
    raise DraftWriteRejected("已有草案行缺少 %s，已中止未写库" % label, code=1)


def build_base_patch(existing, new_base, as_of):
    """只改底薪与应发。补贴、奖金、业绩、扣款留在原行，不进 PATCH。"""
    if not isinstance(existing, dict):
        raise DraftWriteRejected("已有草案行不是对象，已中止未写库", code=1)
    name = str(existing.get("person_name") or existing.get("person") or "").strip()
    door = read_amount(existing, ("door_amount", "door_commission"), "door_amount")
    online = read_amount(existing, ("online_amount", "online_commission"), "online_amount")
    store = read_amount(existing, ("store_amount", "store_commission"), "store_amount")
    accessories = read_amount(
        existing, ("accessories_amount", "accessories_commission"), "accessories_amount")
    subsidy = read_amount(existing, ("subsidy_amount", "subsidy"), "subsidy_amount")
    bonus = read_amount(existing, ("bonus_amount", "bonus"), "bonus_amount")
    deduction = read_amount(existing, ("deduction_amount", "deduction"), "deduction_amount")
    if subsidy < 0 or bonus < 0:
        raise DraftWriteRejected("已有补贴或奖金为负，已中止未写库", code=1)
    gross, net = payable_texts(
        new_base, door, online, store, accessories, subsidy, bonus, deduction, as_of)
    patch = {
        "base_amount": money_text(new_base),
        "gross_amount": gross,
        "net_amount": net,
        "as_of": as_of,
        "updated_at": as_of,
    }
    assert_patch_only_base(patch)
    preserved = {
        "person_name": name,
        "door_amount": money_text(door),
        "online_amount": money_text(online),
        "store_amount": money_text(store),
        "accessories_amount": money_text(accessories),
        "subsidy_amount": money_text(subsidy),
        "bonus_amount": money_text(bonus),
        "deduction_amount": money_text(deduction),
        "source": existing.get("source"),
    }
    return patch, preserved


def assert_patch_only_base(patch):
    if set(patch) != set(PATCH_KEYS):
        raise DraftWriteRejected("底薪同步字段越界，已中止未写库", code=1)
    if set(patch) & set(FORBIDDEN_PATCH_KEYS):
        raise DraftWriteRejected("底薪同步不得覆盖手填或业绩列，已中止未写库", code=1)


def _one_row(rows, what):
    if not isinstance(rows, list):
        raise DraftWriteRejected("%s返回不是数组，已中止未写库" % what, code=1)
    if len(rows) > 1:
        raise DraftWriteRejected("%s命中多行，已中止未写库" % what, code=1)
    if len(rows) == 1 and not isinstance(rows[0], dict):
        raise DraftWriteRejected("%s行不是对象，已中止未写库" % what, code=1)
    return rows[0] if rows else None


def build_draft_get_request(base_url, service_key, month, person_name):
    guard_write_table(apply_mod().TARGET_TABLE)
    base = _https_base(base_url)
    key = _service_key(service_key)
    if not person_name:
        raise DraftWriteRejected("读取草案缺少 person_name，已中止未写库", code=1)
    query = "&".join([
        "select=" + urllib.parse.quote(DRAFT_SELECT, safe=","),
        filter_eq("month", month),
        filter_eq("person_name", person_name),
        "limit=2",
    ])
    url = "%s/rest/v1/%s?%s" % (base, apply_mod().TARGET_TABLE, query)
    _check_url(url, ("/rest/v1/" + apply_mod().TARGET_TABLE,))
    req = urllib.request.Request(url, method="GET")
    return _auth_headers(req, key)


def build_employee_get_request(base_url, service_key, spec):
    base = _https_base(base_url)
    key = _service_key(service_key)
    if spec.get("person_name"):
        ident = filter_eq("person_name", spec["person_name"])
    elif spec.get("user_id"):
        ident = filter_eq("user_id", spec["user_id"])
    else:
        raise DraftWriteRejected("读取 employees 缺少身份", code=1)
    query = "&".join([
        "select=" + urllib.parse.quote(EMPLOYEES_SELECT, safe=","),
        ident,
        "limit=2",
    ])
    url = "%s/rest/v1/employees?%s" % (base, query)
    _check_url(url, ("/rest/v1/employees",))
    req = urllib.request.Request(url, method="GET")
    return _auth_headers(req, key)


def build_placeholder_insert_request(base_url, service_key, row):
    table = guard_write_table(apply_mod().TARGET_TABLE)
    base = _https_base(base_url)
    key = _service_key(service_key)
    url = "%s/rest/v1/%s?on_conflict=month,person_name" % (base, table)
    _check_url(url, ("/rest/v1/" + table,))
    body = json.dumps([row], ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    _auth_headers(req, key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "resolution=ignore-duplicates,return=representation")
    if b"merge-duplicates" in (req.data or b""):
        raise DraftWriteRejected("占位请求不得 merge", code=1)
    return req


def build_base_patch_request(base_url, service_key, month, person_name, patch):
    assert_patch_only_base(patch)
    table = guard_write_table(apply_mod().TARGET_TABLE)
    base = _https_base(base_url)
    key = _service_key(service_key)
    if not person_name:
        raise DraftWriteRejected("同步底薪缺少 person_name，已中止未写库", code=1)
    query = "&".join([filter_eq("month", month), filter_eq("person_name", person_name)])
    url = "%s/rest/v1/%s?%s" % (base, table, query)
    _check_url(url, ("/rest/v1/" + table,))
    body = json.dumps(patch, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="PATCH")
    _auth_headers(req, key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    return req


def _open_json(req, fail_prefix):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise DraftWriteRejected("%s HTTP %s，已中止未写库" % (fail_prefix, exc.code), code=1)
    except urllib.error.URLError as exc:
        raise DraftWriteRejected("%s连接失败，已中止未写库" % fail_prefix, code=1)
    if status != 200:
        raise DraftWriteRejected("%s HTTP %s，已中止未写库" % (fail_prefix, status), code=1)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise DraftWriteRejected("%s响应不是 JSON，已中止未写库" % fail_prefix, code=1)
    return payload


def _send_write(req, fail_prefix):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            resp.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise DraftWriteRejected("%s HTTP %s" % (fail_prefix, exc.code), code=1)
    except urllib.error.URLError as exc:
        raise DraftWriteRejected("%s连接失败" % fail_prefix, code=1)
    if status not in (200, 201, 204):
        raise DraftWriteRejected("%s HTTP %s" % (fail_prefix, status), code=1)
    return status


def fetch_draft_row(base_url, service_key, month, person_name):
    payload = _open_json(
        build_draft_get_request(base_url, service_key, month, person_name),
        "读取当月草案")
    return _one_row(payload, "当月草案")


def fetch_employee_row(base_url, service_key, spec):
    payload = _open_json(
        build_employee_get_request(base_url, service_key, spec),
        "读取 employees")
    row = _one_row(payload, "employees ")
    if row is None:
        raise DraftWriteRejected("employees 没有该员工，已中止未写库", code=1)
    return row


def _load_config(config_path):
    path = Path(config_path or DEFAULT_CONFIG)
    try:
        cfg = apply_mod().load_pipeline_config(path)
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        raise DraftWriteRejected("读取 pipeline_config 失败：%s" % exc, code=1)
    return cfg


def _finish_employee(employee, will_write, env, employee_reader):
    """dry-run 不读 employees。写库且缺姓名或缺底薪时才读。"""
    cli = normalize_employee(employee or {})
    need_fetch = will_write and (not cli["person_name"] or cli["base_salary"] is None)
    if not will_write:
        if not cli["person_name"] or cli["base_salary"] is None:
            raise DraftWriteRejected(
                "dry-run 不读库，需要 --person-name 与 --base-salary", code=1)
        return cli
    if not need_fetch:
        return cli
    spec = {}
    if cli["person_name"]:
        spec["person_name"] = cli["person_name"]
    else:
        spec["user_id"] = cli["user_id"]
    if employee_reader is None:
        url, key = credentials(env)

        def employee_reader(query, _url=url, _key=key):
            return [fetch_employee_row(_url, _key, query)]
    fetched_rows = employee_reader(spec)
    fetched = _one_row(fetched_rows, "employees ")
    if fetched is None:
        raise DraftWriteRejected("employees 没有该员工，已中止未写库", code=1)
    if str(fetched.get("employment_status") or "").strip() == "":
        raise DraftWriteRejected("employees 行缺少 employment_status，已中止未写库", code=1)
    fetched_uid = parse_user_id(fetched.get("user_id"))
    merged = overlay_employee(fetched, cli)
    if cli.get("user_id") and fetched_uid and cli["user_id"] != fetched_uid:
        raise DraftWriteRejected("user_id 与 employees 不一致，已中止未写库", code=1)
    return merged


def _insert_writer(writer, env):
    if writer is not None:
        return writer
    url, key = credentials(env)

    def insert(table, rows, _url=url, _key=key):
        table = guard_write_table(table)
        if len(rows) != 1:
            raise DraftWriteRejected("占位每次只写一行", code=1)
        return _send_write(
            build_placeholder_insert_request(_url, _key, rows[0]), "占位写库失败")

    return insert


def _patch_writer(patcher, env, month, person_name):
    if patcher is not None:
        return patcher
    url, key = credentials(env)

    def patch(table, body, _url=url, _key=key, _month=month, _name=person_name):
        table = guard_write_table(table)
        return _send_write(
            build_base_patch_request(_url, _key, _month, _name, body), "底薪同步写库失败")

    return patch


def _base_result(mode, month, tz_name, employee, action, wrote, gate_env, apply_flag,
                 config_upload, config_apply):
    return {
        "ok": True,
        "mode": mode,
        "month": month,
        "tz_name": tz_name,
        "person_name": employee.get("person_name"),
        "work_role": employee.get("work_role"),
        "action": action,
        "wrote": wrote,
        "gate_env": bool(gate_env),
        "apply_flag": bool(apply_flag),
        "config_upload": bool(config_upload),
        "config_apply": bool(config_apply),
        "source": None,
        "base_amount": None,
        "gross_amount": None,
        "net_amount": None,
        "subsidy_amount": None,
        "bonus_amount": None,
        "insert_row": None,
        "patch": None,
        "preserved": None,
    }


def _amounts_from_row(result, row):
    if not row:
        return result
    result["base_amount"] = row.get("base_amount")
    result["gross_amount"] = row.get("gross_amount")
    result["net_amount"] = row.get("net_amount")
    result["subsidy_amount"] = row.get("subsidy_amount")
    result["bonus_amount"] = row.get("bonus_amount")
    result["source"] = row.get("source")
    return result


def ensure_employee_placeholder(employee, month=None, *, now=None, env=None, apply=False,
                                tz_name=None, existing_reader=None, writer=None,
                                employee_reader=None, config_path=None):
    """在职保存成功后调用。已有同人同月草案则 DO NOTHING。"""
    return _run(
        "placeholder", employee, month, now=now, env=env, apply=apply, tz_name=tz_name,
        existing_reader=existing_reader, writer=writer, patcher=None,
        employee_reader=employee_reader, config_path=config_path)


def sync_employee_base_salary(employee, month=None, *, now=None, env=None, apply=False,
                              tz_name=None, existing_reader=None, writer=None, patcher=None,
                              employee_reader=None, config_path=None):
    """底薪改完后调用。保留补贴、奖金和业绩列；没有草案行则先占位再写底薪。"""
    return _run(
        "sync", employee, month, now=now, env=env, apply=apply, tz_name=tz_name,
        existing_reader=existing_reader, writer=writer, patcher=patcher,
        employee_reader=employee_reader, config_path=config_path)


def _call_reader(existing_reader, env, month, person_name):
    """existing_reader(month, person_name) -> list。未注入时 GET 一行。"""
    if existing_reader is None:
        url, key = credentials(env)
        row = fetch_draft_row(url, key, month, person_name)
        return [] if row is None else [row]
    rows = existing_reader(month, person_name)
    return rows


def _run(mode, employee, month, *, now, env, apply, tz_name, existing_reader, writer,
         patcher, employee_reader, config_path):
    if mode not in ("placeholder", "sync"):
        raise DraftWriteRejected("未知写入模式", code=1)
    env = os.environ if env is None else env
    cfg = _load_config(config_path)
    tz = tz_name or cfg.get("TIMEZONE") or "Asia/Shanghai"
    if now is None:
        now = datetime.now(ZoneInfo(tz))
    month_text, _explicit = resolve_month(month, now, tz)
    gate_env = env_gate_open(env)
    will_write = bool(apply) and gate_env
    person = _finish_employee(employee, will_write, env, employee_reader)
    if mode == "placeholder" and person["employment_status"] != EMPLOYMENT_OK:
        raise DraftWriteRejected("非在职不占位，已中止未写库", code=4)
    stamp = as_of_iso(now, tz)
    preview = build_placeholder_row(person, month_text, stamp)
    result = _base_result(
        mode, month_text, tz, person,
        "dry-run" if not will_write else "pending",
        False, gate_env, apply, cfg.get("UPLOAD", False), cfg.get("APPLY", False))
    result["requested_base_amount"] = money_text(person["base_salary"])
    _amounts_from_row(result, preview)
    result["insert_row"] = preview if not will_write or mode == "placeholder" else None
    if not will_write:
        result["action"] = "dry-run"
        return result

    rows = _call_reader(existing_reader, env, month_text, person["person_name"])
    current = _one_row(rows, "当月草案")
    if current is not None:
        got = str(current.get("person_name") or current.get("person") or "").strip()
        if got and got != person["person_name"]:
            raise DraftWriteRejected("草案行姓名与目标不一致，已中止未写库", code=1)

    if mode == "placeholder":
        if current is not None:
            result["action"] = "skip"
            result["wrote"] = False
            result["insert_row"] = None
            result["patch"] = None
            _amounts_from_row(result, {
                "base_amount": money_text(read_amount(
                    current, ("base_amount", "base_salary"), "base_amount")),
                "gross_amount": current.get("gross_amount"),
                "net_amount": current.get("net_amount"),
                "subsidy_amount": money_text(read_amount(
                    current, ("subsidy_amount", "subsidy"), "subsidy_amount")),
                "bonus_amount": money_text(read_amount(
                    current, ("bonus_amount", "bonus"), "bonus_amount")),
                "source": current.get("source"),
            })
            result["preserved"] = {
                "subsidy_amount": result["subsidy_amount"],
                "bonus_amount": result["bonus_amount"],
                "base_amount": result["base_amount"],
            }
            return result
        inserter = _insert_writer(writer, env)
        inserter(guard_write_table(apply_mod().TARGET_TABLE), [preview])
        result["action"] = "insert"
        result["wrote"] = True
        result["insert_row"] = preview
        result["source"] = SOURCE_PLACEHOLDER
        return result

    if current is None:
        if person["employment_status"] != EMPLOYMENT_OK:
            raise DraftWriteRejected("非在职且当月无草案，不占位，已中止未写库", code=4)
        inserter = _insert_writer(writer, env)
        inserter(guard_write_table(apply_mod().TARGET_TABLE), [preview])
        reread = _call_reader(existing_reader, env, month_text, person["person_name"])
        current = _one_row(reread, "当月草案")
        if current is None:
            current = preview
        inserted = True
    else:
        inserted = False

    patch, preserved = build_base_patch(current, person["base_salary"], stamp)
    patcher_fn = _patch_writer(patcher, env, month_text, person["person_name"])
    patcher_fn(guard_write_table(apply_mod().TARGET_TABLE), patch)
    result["action"] = "placeholder_then_patch" if inserted else "patch"
    result["wrote"] = True
    result["insert_row"] = preview if inserted else None
    result["patch"] = patch
    result["preserved"] = preserved
    result["base_amount"] = patch["base_amount"]
    result["gross_amount"] = patch["gross_amount"]
    result["net_amount"] = patch["net_amount"]
    result["subsidy_amount"] = preserved["subsidy_amount"]
    result["bonus_amount"] = preserved["bonus_amount"]
    result["source"] = preserved.get("source") or (
        SOURCE_PLACEHOLDER if inserted else None)
    return result


def render(result):
    """stdout 契约。不打印密钥。"""
    gate = ENV_GATE
    phase = "apply" if result["gate_env"] and result["apply_flag"] else "dry-run"
    lines = [
        "月份 %s（时区 %s）" % (result["month"], result["tz_name"]),
        "十月禁灌：拒绝 %s 写入。生产禁盲开：默认不写库，写库须 %s=true 与 --apply。"
        "只开 ALLOW_APPLY_REALTIME_DRAFT 不放行。"
        % (OCTOBER_NO_SEED, gate),
        "pipeline_config UPLOAD=%s APPLY=%s（本脚本不修改这两键，也不靠它们放行）"
        % (str(result["config_upload"]).lower(), str(result["config_apply"]).lower()),
        "写库闸：%s=%s --apply=%s → %s（不读 ALLOW_APPLY_REALTIME_DRAFT）" % (
            gate, str(result["gate_env"]).lower(), str(result["apply_flag"]).lower(), phase),
        "person_name=%s work_role=%s action=%s source=%s base_amount=%s gross_amount=%s net_amount=%s"
        % (result.get("person_name"), result.get("work_role"), result.get("action"),
           result.get("source"), result.get("base_amount"), result.get("gross_amount"),
           result.get("net_amount")),
    ]
    action = result.get("action")
    if action == "dry-run" and result.get("mode") == "placeholder":
        row = result.get("insert_row") or {}
        lines.append(
            "占位预览 door_amount=%s online_amount=%s store_amount=%s accessories_amount=%s "
            "subsidy_amount=%s bonus_amount=%s deduction_amount=%s"
            % (row.get("door_amount"), row.get("online_amount"), row.get("store_amount"),
               row.get("accessories_amount"), row.get("subsidy_amount"),
               row.get("bonus_amount"), row.get("deduction_amount")))
        lines.append("上门与门店都按本次 base_salary 写入，不因角色改成 0。")
        lines.append("写库时 ON CONFLICT (month, person_name) DO NOTHING，已有草案行不覆盖。")
        lines.append("未写库。")
    elif action == "dry-run":
        lines.append(
            "dry-run 不读库。预览按当月无行：先占位（业绩列与补贴奖金为 0）再写底薪。"
            "写库时若已有行，保留 subsidy_amount、bonus_amount 与业绩列，只替换 base_amount 并重算应发。")
        lines.append("未写库。")
    elif action == "skip":
        lines.append(
            "已有当月草案，ON CONFLICT DO NOTHING，未覆盖 base_amount、subsidy_amount、"
            "bonus_amount 与业绩列。")
        lines.append("未发写请求（DO NOTHING）。")
    elif action == "insert":
        lines.append(
            "已占位 1 行 → %s（Prefer resolution=ignore-duplicates；source=%s）。"
            % (apply_mod().TARGET_TABLE, SOURCE_PLACEHOLDER))
    elif action == "patch":
        kept = result.get("preserved") or {}
        lines.append(
            "已同步底薪。保留 subsidy_amount=%s bonus_amount=%s door_amount=%s "
            "online_amount=%s store_amount=%s accessories_amount=%s deduction_amount=%s，"
            "这些列不在 PATCH 里。"
            % (kept.get("subsidy_amount"), kept.get("bonus_amount"), kept.get("door_amount"),
               kept.get("online_amount"), kept.get("store_amount"),
               kept.get("accessories_amount"), kept.get("deduction_amount")))
    elif action == "placeholder_then_patch":
        kept = result.get("preserved") or {}
        lines.append("先占位再写底薪。")
        lines.append(
            "已同步底薪。保留 subsidy_amount=%s bonus_amount=%s，不覆盖业绩列。"
            % (kept.get("subsidy_amount"), kept.get("bonus_amount")))
    return "\n".join(lines)


def build_parser(mode):
    if mode == "placeholder":
        desc = "在职保存后占位当月 salary_realtime_draft（默认 dry-run，冲突 DO NOTHING）"
    else:
        desc = "底薪变更后同步当月草案 base_amount 并重算应发（默认 dry-run，保留补贴奖金）"
    ap = argparse.ArgumentParser(description=desc)
    ap.add_argument("--person-name", default=None, help="员工姓名。与 --user-id 至少给一个")
    ap.add_argument("--user-id", default=None, help="employees.user_id / profiles.id。写库且缺姓名时用来读员工表")
    ap.add_argument("--base-salary", default=None,
                    help="表底薪。上门也跟这个数。dry-run 必填；写库可省略改从 employees 读")
    ap.add_argument("--work-role", default=None, help="上门 或 门店。只写入 caliber，不改底薪")
    ap.add_argument("--employment-status", default=None, help="在职 或 离职。省略且不读表时按在职")
    ap.add_argument("--month", default=None, help="YYYY-MM。省略则用 pipeline 时区的日历当月。2026-10 拒绝")
    ap.add_argument("--apply", action="store_true",
                    help="与 ALLOW_EMPLOYEE_DRAFT_WRITE=true 同时成立才写库；"
                         "只开 ALLOW_APPLY_REALTIME_DRAFT 不放行")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="pipeline_config.json（只读，不修改 UPLOAD/APPLY）")
    return ap


def employee_from_namespace(args):
    raw = {}
    if args.person_name:
        raw["person_name"] = args.person_name
    if args.user_id:
        raw["user_id"] = args.user_id
    if args.base_salary is not None:
        raw["base_salary"] = args.base_salary
    if args.work_role:
        raw["work_role"] = args.work_role
    if args.employment_status:
        raw["employment_status"] = args.employment_status
    return raw


def main(argv=None, mode="placeholder", env=None, now=None, existing_reader=None,
         writer=None, patcher=None, employee_reader=None):
    args = build_parser(mode).parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    raw = employee_from_namespace(args)
    try:
        if mode == "placeholder":
            result = ensure_employee_placeholder(
                raw, month=args.month, now=now, env=env, apply=bool(args.apply),
                existing_reader=existing_reader, writer=writer,
                employee_reader=employee_reader, config_path=args.config)
        elif mode == "sync":
            result = sync_employee_base_salary(
                raw, month=args.month, now=now, env=env, apply=bool(args.apply),
                existing_reader=existing_reader, writer=writer, patcher=patcher,
                employee_reader=employee_reader, config_path=args.config)
        else:
            raise DraftWriteRejected("未知写入模式", code=1)
    except DraftWriteRejected as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    print(render(result))
    return 0
