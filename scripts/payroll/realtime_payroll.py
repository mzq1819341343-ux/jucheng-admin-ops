#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实时工资草案：加盟商后台旧机订单（orders37）+ 在职名单角色。

口径（产品拍板 A1 / B1 / C 过渡 JSON / D，与 docs/工资字段映射_新口径.md 一致）：
  - 名单每人必须有 role ∈ {上门, 门店}。缺 role、仅姓名列表、或根本没给名单
    → 拒绝出草案（退出码 4），禁止默认角色。
  - role=上门 且 取货方式=上门：分润 × 50% → door。
  - role=门店 且 取货方式=上门：不含券金额 → online（×1%），不进 door，
    不进门店阶梯基数，也不进该人的门店份额。
  - 来源类型 ≠ 门店 仍 ×1%（ONLINE_MODE=source_not_门店，不改）。
  - 门店阶梯仍按不含券定档：≥60000→4%，≥30000→3%，否则 0。
    不在名单上的订单仍计入全店定档（指令193），不计入个人份额。
  - 配件 = 实付 × 30%（ACCESSORIES_PCT）。--accessories-input 缺文件则该项为 0
    并在 assumptions 注明。禁止用旧机 191 列冒充配件。
  - 底薪取名单 base_salary，缺省 2000（基础 1000 + 全勤 500 + 拉新 500）。
    与 2000 不同的自定义底薪整笔记入 basic，全勤/拉新 = 0。
  - gross = base + door + online + store + accessories。

名单来源：
  - 默认 JSON：--active-roster。样例见同目录 active_roster.example.json。
    在职角色名单不要复制进本文件或文档。
  - 可选只读库：--roster-from=employees 或环境变量 ROSTER_SOURCE=employees。
    GET public.employees 中 employment_status=在职 的 person_name / work_role /
    base_salary。缺 work_role 仍拒绝出草案。本脚本不写 employees，不写草案表。

硬排除 HARD_EXCLUDED = 吕文飞、杨燕辉（退出码 3，不出草案）。
张浩冉、张静怡已移出硬排除；是否计薪只看名单里的 role。

规则常量与库内 salary_rules.commission_rules 对齐（accessories_pct=30 已在库侧落地）。
本脚本不写 migration、不改 salary_rules。EFFECTIVE_FROM=2026-10。
pipeline UPLOAD/APPLY 不在这里改，保持 false。

JSON 形状（不接受字符串姓名；示例用假名，不是在职名单）：
  {
    "active": [
      {"name": "示例甲", "role": "上门", "base_salary": 2000},
      {"person_name": "示例乙", "role": "门店"}
    ],
    "excluded": [{"person": "示例丙", "reason": "离职"}]
  }
  name 与 person_name 二选一。base_salary 可省略。

用法：
  python3 scripts/payroll/realtime_payroll.py \\
    --input <本机旧机订单.xlsx> --month 2026-10 \\
    --active-roster <本机名单.json> \\
    --accessories-input <本机配件导出.csv> \\
    --out-dir <本机目录>
  ROSTER_SOURCE=employees python3 scripts/payroll/realtime_payroll.py \\
    --input <本机旧机订单.xlsx> --month 2026-10 \\
    --roster-from=employees --out-dir <本机目录>

个人级输出只落 --out-dir，禁止入仓。stdout 只打印汇总，不打印个人清单。
退出码：0 成功；2 早于生效月；3 计薪名单含硬排除；4 名单/角色拒绝出草案；1 其它。
"""

import argparse
import csv
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ---- 与 salary_rules 对应（accessories_pct=30 已在库内，本文件只镜像常量） ----
EFFECTIVE_FROM = "2026-10"

BASE_SALARY = Decimal("2000")
BASE_BREAKDOWN = {"basic": Decimal("1000"), "attendance": Decimal("500"),
                  "new_customer": Decimal("500")}
ONLINE_PCT = Decimal("0.01")
# 线上判定仍锁 source_not_门店。门店角色上门单另计线上，写在 meta.caliber，不改这个常量。
ONLINE_MODE = "source_not_门店"
STORE_TIERS = [(Decimal("60000"), Decimal("0.04")),
               (Decimal("30000"), Decimal("0.03"))]
STORE_BASE_FIELD = "订单金额（不含券）"
ACCESSORIES_PCT = Decimal("0.30")  # 实付 × 30%。废止 accessories_tiers。
DOOR_PROFIT_PCT = Decimal("0.50")
ROLES = ("上门", "门店")

STORE_IDS = {"石河子万达店": "4594", "石河子友谊商厦店": "5511"}

# 老板定名单后硬排除只留这两人。张浩冉、张静怡不再拦截。
HARD_EXCLUDED = ("吕文飞", "杨燕辉")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_BEFORE_EFFECTIVE = 2
EXIT_HARD_EXCLUDED = 3
EXIT_ROSTER = 4

CENT = Decimal("0.01")

# 配件必须是独立导出。这些列出现即视为旧机 191 订单，禁止拿来当配件实付。
ORDER_IMPERSONATION_COLS = frozenset({
    "门店验货人", "订单金额（不含券）", "分润金额", "取货方式", "来源类型",
    "订单金额", "结算金额",
})
ACCESSORY_PERSON_COLS = ("归属人", "销售人")
ACCESSORY_AMOUNT_COL = "实付金额"
ACCESSORY_DATE_COLS = ("完成时间", "销售时间", "日期", "完成日期")

EMPLOYEES_SELECT = "person_name,work_role,base_salary,employment_status,user_id"


class DraftRejected(Exception):
    def __init__(self, message, code=EXIT_ROSTER):
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def money(d):
    return Decimal(d).quantize(CENT, rounding=ROUND_HALF_UP)


def load_auto_fetch():
    path = Path(__file__).resolve().parents[2] / "docs" / "fetch-pipeline" / "auto_fetch.py"
    spec = importlib.util.spec_from_file_location("auto_fetch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def to_decimal(v):
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def month_of(v):
    m = re.search(r"(\d{4})-(\d{2})", str(v))
    return "%s-%s" % (m.group(1), m.group(2)) if m else ""


def tier_rate(base):
    for threshold, rate in STORE_TIERS:
        if base >= threshold:
            return rate
    return Decimal("0")


def is_online_source(source_type):
    """来源类型去掉空白后不等于「门店」则算线上。

    ONLINE_MODE 锁死 source_not_门店。门店角色的上门单是否进线上，不在这里判断。
    """
    if ONLINE_MODE != "source_not_门店":
        raise RuntimeError(
            "ONLINE_MODE=%r 未锁定。现行唯一合法值是 source_not_门店"
            "（旧值 source_官网 已废止，不得作为可切换口径）" % (ONLINE_MODE,))
    return str(source_type).strip() != "门店"


def parse_base_salary(value, name):
    if isinstance(value, bool):
        raise DraftRejected("拒绝出草案：%s 的 base_salary 不是数字" % name)
    try:
        base = Decimal(str(value).replace(",", "").strip())
    except Exception:
        raise DraftRejected("拒绝出草案：%s 的 base_salary 不是数字" % name)
    if base < 0:
        raise DraftRejected("拒绝出草案：%s 的 base_salary 不能为负" % name)
    return money(base)


def classify_base(value, provided):
    """缺省或等于 2000：三分项。其它金额：自定义，整笔记入 basic。"""
    if not provided:
        return BASE_SALARY, False
    base = parse_base_salary(value, "")
    if base == BASE_SALARY:
        return BASE_SALARY, False
    return base, True


def parse_roster_document(doc):
    """解析过渡 JSON。缺 role 或仅姓名列表直接拒绝，不默认上门/门店。"""
    if not isinstance(doc, dict):
        raise DraftRejected("拒绝出草案：在职名单须为 JSON 对象")
    active = doc.get("active")
    if not isinstance(active, list) or not active:
        raise DraftRejected("拒绝出草案：在职名单 active 为空或缺失，禁止默认角色")
    people = {}
    for i, item in enumerate(active):
        if isinstance(item, str) or not isinstance(item, dict):
            raise DraftRejected(
                "拒绝出草案：active[%d] 不是带 role 的对象。"
                "不接受仅姓名列表，禁止默认角色" % i)
        name = str(item.get("name") or item.get("person_name") or "").strip()
        if not name:
            raise DraftRejected("拒绝出草案：active[%d] 缺少 name/person_name" % i)
        if name in HARD_EXCLUDED:
            raise DraftRejected(
                "计薪名单中出现硬排除人员 %s，已中止" % name, code=EXIT_HARD_EXCLUDED)
        if "role" not in item or str(item.get("role") or "").strip() == "":
            raise DraftRejected("拒绝出草案：%s 缺少 role，禁止默认角色" % name)
        role = str(item.get("role")).strip()
        if role not in ROLES:
            raise DraftRejected(
                "拒绝出草案：%s 的 role=%r 不在 {上门, 门店}" % (name, role))
        if name in people:
            raise DraftRejected("拒绝出草案：在职名单姓名重复 %s" % name)
        provided = "base_salary" in item and item.get("base_salary") is not None
        try:
            base, custom = classify_base(item.get("base_salary"), provided)
        except DraftRejected:
            raise DraftRejected("拒绝出草案：%s 的 base_salary 不是合法金额" % name)
        people[name] = {"name": name, "role": role, "base_salary": base, "custom_base": custom}
    reasons = {}
    excluded = doc.get("excluded") or []
    if not isinstance(excluded, list):
        raise DraftRejected("拒绝出草案：excluded 须为数组")
    for entry in excluded:
        if isinstance(entry, str):
            who = entry.strip()
            if who:
                reasons[who] = "不在在职名单"
        elif isinstance(entry, dict):
            who = str(entry.get("person") or entry.get("name") or entry.get("person_name") or "").strip()
            if who:
                reasons[who] = entry.get("reason") or "不在在职名单"
    return people, reasons


def parse_employee_rows(rows):
    """在职行 → 与 JSON 相同的名单结构。离职跳过。缺 work_role 拒绝。"""
    if not isinstance(rows, list):
        raise DraftRejected("拒绝出草案：employees 返回不是数组")
    active = []
    for row in rows:
        if not isinstance(row, dict):
            raise DraftRejected("拒绝出草案：employees 行不是对象")
        status = row.get("employment_status")
        if status is None or str(status).strip() == "":
            raise DraftRejected("拒绝出草案：employees 行缺少 employment_status")
        status = str(status).strip()
        if status == "离职":
            continue
        if status != "在职":
            raise DraftRejected("拒绝出草案：无法识别的 employment_status=%r" % status)
        name = str(row.get("person_name") or "").strip()
        if not name:
            raise DraftRejected("拒绝出草案：employees 在职行缺少 person_name")
        role = row.get("work_role")
        if role is None or str(role).strip() == "":
            raise DraftRejected("拒绝出草案：%s 缺少 work_role，禁止默认角色" % name)
        item = {"person_name": name, "role": str(role).strip()}
        if row.get("base_salary") is not None and str(row.get("base_salary")).strip() != "":
            item["base_salary"] = row.get("base_salary")
        active.append(item)
    if not active:
        raise DraftRejected("拒绝出草案：employees 没有 employment_status=在职 的人员")
    return parse_roster_document({"active": active, "excluded": []})


def resolve_roster_source(args, env):
    cli = (args.roster_from or "").strip()
    env_src = str(env.get("ROSTER_SOURCE") or "").strip()
    if cli and env_src and cli != env_src:
        raise DraftRejected(
            "拒绝出草案：ROSTER_SOURCE=%s 与 --roster-from=%s 冲突" % (env_src, cli))
    source = cli or env_src or "json"
    if source not in ("json", "employees"):
        raise DraftRejected("拒绝出草案：名单来源 %r 只接受 json 或 employees" % source)
    if source == "json" and args.active_roster:
        return "json"
    if source == "employees" and args.active_roster:
        raise DraftRejected(
            "拒绝出草案：--roster-from=employees 时不要同时传 --active-roster")
    if source == "employees":
        return "employees"
    raise DraftRejected(
        "拒绝出草案：未提供带 role 的在职名单（--active-roster）。禁止默认角色")


def load_roster(args, env, employees_fetcher):
    source = resolve_roster_source(args, env)
    if source == "json":
        path = Path(args.active_roster)
        if not path.is_file():
            raise DraftRejected("拒绝出草案：在职名单文件不存在：%s" % path)
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raise DraftRejected("拒绝出草案：在职名单不是合法 JSON")
        people, reasons = parse_roster_document(doc)
        return people, reasons, "json", str(path.resolve())
    # 只读 employees。JSON 仍是默认。不在这里写库，也不读 salary_detail_import / salary_monthly。
    reader = employees_fetcher or default_employees_fetcher
    rows = reader(env)
    people, reasons = parse_employee_rows(rows)
    return people, reasons, "employees", None


def build_employees_request(base_url, service_key):
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith("https://"):
        raise DraftRejected(
            "拒绝出草案：读取 employees 需要 https 的 SUPABASE_URL（或 NEXT_PUBLIC_SUPABASE_URL）",
            code=EXIT_ERROR)
    if not service_key or not str(service_key).strip():
        raise DraftRejected(
            "拒绝出草案：读取 employees 需要 SUPABASE_SERVICE_ROLE_KEY。未连库",
            code=EXIT_ERROR)
    query = urllib.parse.urlencode({
        "select": EMPLOYEES_SELECT,
        "employment_status": "eq.在职",
        "order": "person_name.asc",
        "limit": "1000",
    })
    url = "%s/rest/v1/employees?%s" % (base, query)
    path = urllib.parse.urlparse(url).path
    if path != "/rest/v1/employees":
        raise DraftRejected("拒绝出草案：employees 请求路径异常", code=EXIT_ERROR)
    req = urllib.request.Request(url, method="GET")
    req.add_header("apikey", service_key)
    req.add_header("Authorization", "Bearer " + service_key)
    req.add_header("Accept", "application/json")
    return req


def default_employees_fetcher(env):
    """GET public.employees。只读，失败即拒绝出草案，不写任何表。"""
    url = (env.get("SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL") or "")
    key = env.get("SUPABASE_SERVICE_ROLE_KEY") or ""
    req = build_employees_request(url, key)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise DraftRejected(
            "拒绝出草案：读取 employees 失败 HTTP %s" % exc.code, code=EXIT_ERROR)
    except urllib.error.URLError:
        raise DraftRejected("拒绝出草案：读取 employees 连接失败", code=EXIT_ERROR)
    if status != 200:
        raise DraftRejected(
            "拒绝出草案：读取 employees 失败 HTTP %s" % status, code=EXIT_ERROR)
    try:
        rows = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        raise DraftRejected("拒绝出草案：employees 响应不是 JSON", code=EXIT_ERROR)
    if isinstance(rows, list) and len(rows) >= 1000:
        raise DraftRejected("拒绝出草案：employees 在职人数达到读取上限，已中止", code=EXIT_ERROR)
    return rows


def _sheet_matrix(af, path):
    tables = af.load_tables(str(path))
    for _name, rows in tables:
        if rows:
            return rows
    raise DraftRejected("拒绝出草案：配件输入是空表", code=EXIT_ERROR)


def load_accessories(path, month, af):
    """独立配件导出 → {姓名: 当月实付}。缺文件不报错，返回空并注明缺源。"""
    none = {
        "pct": "30|source=none",
        "source": "none",
        "rows_in_month": 0,
        "unmatched_people": 0,
    }
    if not path:
        none["note"] = (
            "缺配件源未计：未提供 --accessories-input，accessories=0。"
            "禁止用旧机 191 列冒充配件")
        return {}, none
    file_path = Path(path)
    if not file_path.is_file():
        none["note"] = (
            "缺配件源未计：配件文件不存在（%s），accessories=0。"
            "禁止用旧机 191 列冒充配件" % file_path)
        return {}, none
    rows = _sheet_matrix(af, file_path)
    header = None
    data = None
    for i, raw in enumerate(rows[:10]):
        hdr = [str(c).strip() for c in raw]
        if any(hdr):
            header = hdr
            data = rows[i + 1:]
            break
    if not header:
        raise DraftRejected("拒绝出草案：配件输入没有表头", code=EXIT_ERROR)
    hset = set(header)
    impersonated = hset & ORDER_IMPERSONATION_COLS
    if impersonated:
        raise DraftRejected(
            "禁止用旧机 191 订单列冒充配件（发现 %s）。"
            "配件须为独立导出，列含归属人或销售人、实付金额" % "、".join(sorted(impersonated)),
            code=EXIT_ERROR)
    person_col = next((c for c in ACCESSORY_PERSON_COLS if c in hset), None)
    if person_col is None or ACCESSORY_AMOUNT_COL not in hset:
        raise DraftRejected(
            "拒绝出草案：配件输入缺少列。需要归属人或销售人，以及实付金额",
            code=EXIT_ERROR)
    date_col = next((c for c in ACCESSORY_DATE_COLS if c in hset), None)
    if date_col is None:
        raise DraftRejected(
            "拒绝出草案：配件输入缺少日期列（完成时间、销售时间或日期）",
            code=EXIT_ERROR)
    idx = {h: i for i, h in enumerate(header)}

    def cell(row, col):
        i = idx[col]
        return str(row[i]).strip() if i < len(row) else ""

    paid = defaultdict(Decimal)
    rows_in = 0
    for row in data:
        if not any(str(c).strip() for c in row):
            continue
        if month_of(cell(row, date_col)) != month:
            continue
        name = cell(row, person_col)
        amount = to_decimal(cell(row, ACCESSORY_AMOUNT_COL))
        if not name or amount is None:
            continue
        if amount < 0:
            raise DraftRejected(
                "拒绝出草案：配件实付金额为负（%s）" % name, code=EXIT_ERROR)
        paid[name] += amount
        rows_in += 1
    resolved = str(file_path.resolve())
    return paid, {
        "pct": "30|source=%s" % resolved,
        "source": resolved,
        "rows_in_month": rows_in,
        "note": "配件实付×30%%（%s），月内计入 %d 行" % (file_path.name, rows_in),
        "unmatched_people": 0,
    }


def base_view(person, roster, exceptions):
    if person in exceptions:
        base = money(exceptions[person])
        return base, {"basic": base, "attendance": Decimal("0"), "new_customer": Decimal("0")}, (
            "个人例外（非现行路径，--base-exceptions 注入；名单底薪被覆盖）")
    info = roster[person]
    base = info["base_salary"]
    if info["custom_base"]:
        return base, {"basic": base, "attendance": Decimal("0"), "new_customer": Decimal("0")}, (
            "名单自定义底薪（整笔记入 basic，全勤与拉新为 0）")
    return base, dict(BASE_BREAKDOWN), (
        "名单缺省2000（基础1000+全勤500+拉新500），不做1–9月个人例外")


def main(argv=None, env=None, employees_fetcher=None):
    ap = argparse.ArgumentParser(description="实时工资草案（订单 + 带角色的在职名单）")
    ap.add_argument("--input", required=True, help="加盟商后台旧机订单导出（xlsx/csv）")
    ap.add_argument("--month", default=None, help="计算月份 YYYY-MM；缺省取完成时间最大月份")
    ap.add_argument("--out-dir", required=True, help="个人级输出目录（仅本机，禁止入仓）")
    ap.add_argument("--source-label", default="orders37/191", help="数据来源标注（写入草案 meta）")
    ap.add_argument("--base-exceptions", default=None,
                    help="非现行路径：覆盖名单底薪。缺省不读。不得当作现行默认")
    ap.add_argument("--active-roster", default=None,
                    help="在职名单 JSON：active 为 {name|person_name, role, base_salary?}。"
                         "缺 role 拒绝出草案。不接受仅姓名列表")
    ap.add_argument("--roster-from", default=None, choices=["json", "employees"],
                    help="名单来源。默认 json（须 --active-roster）。"
                         "employees=只读 public.employees 在职行。环境变量 ROSTER_SOURCE 同义，冲突则拒绝")
    ap.add_argument("--accessories-input", default=None,
                    help="配件独立导出（csv/xlsx）：归属人或销售人、实付金额、日期。"
                         "缺文件则配件=0 并注明。禁止用旧机 191 列冒充")
    args = ap.parse_args(argv)
    if env is None:
        env = os.environ

    if args.month and args.month < EFFECTIVE_FROM:
        print("警告：月份 %s 早于生效月 EFFECTIVE_FROM=%s，现行规则自 %s 起算，本草案不适用"
              % (args.month, EFFECTIVE_FROM, EFFECTIVE_FROM), file=sys.stderr)
        return EXIT_BEFORE_EFFECTIVE

    af = load_auto_fetch()
    tables = af.load_tables(args.input)
    kind, _sheet, _hi, header, raw_rows = af.detect_format(tables)
    rows = [r for r in raw_rows if any(str(c).strip() for c in r)]
    idx = {h: i for i, h in enumerate(header)}

    def g(r, col):
        return str(r[idx[col]]).strip() if col in idx else ""

    def num(r, col):
        return to_decimal(r[idx[col]]) if col in idx else None

    # 191 无配件列。这里不读实付、不把订单金额当成配件。
    months = [month_of(g(r, "完成时间")) for r in rows]
    month = args.month or max((m for m in months if m), default="")
    if not month:
        print("错误：无法确定月份", file=sys.stderr)
        return EXIT_ERROR
    if month < EFFECTIVE_FROM:
        print("警告：月份 %s 早于生效月 EFFECTIVE_FROM=%s，现行规则自 %s 起算，本草案不适用"
              % (month, EFFECTIVE_FROM, EFFECTIVE_FROM), file=sys.stderr)
        return EXIT_BEFORE_EFFECTIVE
    in_month = [r for r, m in zip(rows, months) if m == month]
    out_month = len(rows) - len(in_month)

    try:
        roster, roster_reasons, roster_source, roster_file = load_roster(
            args, env, employees_fetcher)
        accessory_paid, accessory_meta = load_accessories(args.accessories_input, month, af)
    except DraftRejected as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return exc.code

    exceptions = {}
    if args.base_exceptions:
        print("警告：本跑注入了 --base-exceptions（非现行路径）。"
              "现行底薪以名单 base_salary 为准，缺省 2000。", file=sys.stderr)
        exceptions = {k: Decimal(str(v)) for k, v in
                      json.loads(Path(args.base_exceptions).read_text(encoding="utf-8")).items()}

    unmatched = sorted(name for name in accessory_paid if name not in roster)
    accessory_meta["unmatched_people"] = len(unmatched)
    if unmatched and accessory_meta["source"] != "none":
        accessory_meta["note"] += "；归属人不在在职名单的实付未计入 %d 人" % len(unmatched)

    store_base = defaultdict(Decimal)
    store_profit_base = defaultdict(Decimal)
    store_excluded_door = defaultdict(Decimal)
    store_count = defaultdict(int)
    excluded_tmp = defaultdict(lambda: {"orders": 0, "base_amt": Decimal("0"), "profit": Decimal("0")})
    agg = defaultdict(lambda: {
        "orders": 0, "base_amt_missing": 0,
        "profit": Decimal("0"), "profit_missing": 0,
        "door_orders": 0, "door_profit": Decimal("0"),
        "online_orders": 0, "online_amt": Decimal("0"),
        "store_base": Decimal("0"),
        "stores": defaultdict(lambda: {"orders": 0, "base_amt": Decimal("0")}),
    })
    for name in roster:
        agg[name]["role"] = roster[name]["role"]

    for r in in_month:
        person, store = g(r, "门店验货人"), g(r, "门店名称")
        amt = num(r, STORE_BASE_FIELD)
        profit = num(r, "分润金额")
        door_pickup = g(r, "取货方式") == "上门"
        online_src = is_online_source(g(r, "来源类型"))
        info = roster.get(person)
        role = info["role"] if info else None
        store_role_door = role == "门店" and door_pickup

        store_count[store] += 1
        if profit is not None:
            store_profit_base[store] += profit
        if store_role_door:
            if amt is not None:
                store_excluded_door[store] += amt
        elif amt is not None:
            store_base[store] += amt

        if info is None:
            bucket = excluded_tmp[person]
            bucket["orders"] += 1
            if amt is not None:
                bucket["base_amt"] += amt
            if profit is not None:
                bucket["profit"] += profit
            continue

        a = agg[person]
        a["orders"] += 1
        a["stores"][store]["orders"] += 1
        if amt is None:
            a["base_amt_missing"] += 1
        elif not store_role_door:
            a["store_base"] += amt
            a["stores"][store]["base_amt"] += amt
        if profit is None:
            a["profit_missing"] += 1
        else:
            a["profit"] += profit
        if door_pickup:
            a["door_orders"] += 1
            if role == "上门" and profit is not None:
                a["door_profit"] += profit
        enters_online = store_role_door or online_src
        if enters_online:
            a["online_orders"] += 1
            if amt is not None:
                a["online_amt"] += amt

    for store in store_count:
        store_base.setdefault(store, Decimal("0"))
    store_rate = {s: tier_rate(store_base[s]) for s in store_count}
    store_rate_ref = {s: tier_rate(store_profit_base[s]) for s in store_count}

    excluded_agg = {
        p: {"person": p,
            "orders": excluded_tmp[p]["orders"],
            "base_amt_excl_coupon": float(money(excluded_tmp[p]["base_amt"])),
            "profit_total": float(money(excluded_tmp[p]["profit"])),
            "reason": roster_reasons.get(p, "不在在职名单")}
        for p in sorted(excluded_tmp, key=lambda x: (-excluded_tmp[x]["orders"], x))
    }

    people = []
    for person in sorted(roster, key=lambda n: (-agg[n]["orders"], n)):
        a = agg[person]
        store_comm = sum(
            (a["stores"][s]["base_amt"] * store_rate.get(s, Decimal("0")) for s in a["stores"]),
            Decimal("0"))
        base, bd, base_source = base_view(person, roster, exceptions)
        door_comm = money(a["door_profit"] * DOOR_PROFIT_PCT)
        online_comm = money(a["online_amt"] * ONLINE_PCT)
        store_comm_m = money(store_comm)
        acc_paid = accessory_paid.get(person, Decimal("0"))
        acc_comm = money(acc_paid * ACCESSORIES_PCT)
        row = {
            "person": person,
            "role": roster[person]["role"],
            "stores": {s: {"store_id": STORE_IDS.get(s, ""),
                           "orders": a["stores"][s]["orders"],
                           "base_amt": float(money(a["stores"][s]["base_amt"])),
                           "tier_rate": float(store_rate.get(s, Decimal("0")))}
                       for s in a["stores"]},
            "orders": a["orders"],
            "base_salary": float(money(base)),
            "base_breakdown": {k: float(money(v)) for k, v in bd.items()},
            "base_salary_source": base_source,
            "door_orders": a["door_orders"],
            "door_profit": float(money(a["door_profit"])),
            "door_commission": float(door_comm),
            "online_orders": a["online_orders"],
            "online_amt": float(money(a["online_amt"])),
            "online_commission": float(online_comm),
            "store_base_amt": float(money(a["store_base"])),
            "store_commission": float(store_comm_m),
            "accessories_paid": float(money(acc_paid)),
            "accessories_commission": float(acc_comm),
            "accessories_note": accessory_meta["note"],
            "profit_total": float(money(a["profit"])),
            "profit_missing_rows": a["profit_missing"],
            "base_amt_missing_rows": a["base_amt_missing"],
        }
        row["gross"] = float(money(base + door_comm + online_comm + store_comm_m + acc_comm))
        people.append(row)

    total_orders = sum(p["orders"] for p in people)
    excluded_orders = sum(e["orders"] for e in excluded_agg.values())
    assert total_orders + excluded_orders == len(in_month), \
        "单量锚点不符：计薪 %d + 排除 %d != 月内行数 %d" % (total_orders, excluded_orders, len(in_month))
    leaked = [p["person"] for p in people if p["person"] in HARD_EXCLUDED]
    if leaked:
        print("错误：计薪名单中出现硬排除人员 %s，已中止" % "、".join(leaked), file=sys.stderr)
        return EXIT_HARD_EXCLUDED

    assumptions = [
        "线上提成【已拍板 A1】：来源类型≠门店（ONLINE_MODE=source_not_门店）的不含券×1%；"
        "另：role=门店且取货方式=上门的不含券也按线上×1%"
        "（online_mode=source_not_门店+store_role_door_as_online）。不与上门 50% 双计",
        "上门提成【已拍板 A1】：仅 role=上门 且取货方式=上门的分润×50%"
        "（door_profit_pct=50|role=上门_only）。role=门店的上门单不进 door",
        "门店提成【已拍板】：不含券按店定档（≥60000→4%，≥30000→3%，否则 0%）。"
        "role=门店的上门单不进阶梯基数、不进个人门店份额。"
        "不在名单上的订单仍计入定档，不进个人份额",
        "配件提成【已拍板 B1/D】：实付×30%（accessories_pct=30），废止 accessories_tiers。"
        + accessory_meta["note"],
        "底薪：名单 base_salary，缺省 2000（基础1000+全勤500+拉新500）。"
        "与缺省不同的自定义底薪整笔记入 basic，全勤/拉新=0。"
        "--base-exceptions 仍为非现行覆盖",
        "在职名单每人必须有 role∈{上门,门店}。缺 role、仅姓名列表或未提供名单则拒绝出草案，不默认角色。"
        "零单在职人员仍出底薪行",
        "硬排除仅吕文飞、杨燕辉。张浩冉、张静怡已移出硬排除，出现在计薪名单不再因此中止",
        "分润金额或实付金额为空的行按缺失不补值，不计入对应基数",
        "gross = base + door + online + store + accessories",
    ]
    if exceptions:
        assumptions.append(
            "本跑注入了 --base-exceptions（非现行路径）。现行底薪以名单为准；本文件仅供对照")
    if roster_source == "employees":
        assumptions.append(
            "名单来源 employees：employment_status=在职 的 work_role 与 base_salary。"
            "只读，未写库。缺 work_role 的在职行会拒绝出草案")
    else:
        assumptions.append("名单来源 JSON（过渡）。日后可改 --roster-from=employees，本文件不把角色写死")

    meta = {
        "draft": True,
        "month": month,
        "effective_from": EFFECTIVE_FROM,
        "source": args.source_label,
        "statement": "pending（2026-10 对账单未生成，月未结）",
        "input_file": str(Path(args.input).resolve()),
        "input_format": kind,
        "input_rows_total": len(rows),
        "rows_in_month": len(in_month),
        "rows_out_of_month": out_month,
        "note_partial_month": "输入为月中导出（完成时间截至导出时刻），非整月终数",
        "online_mode": ONLINE_MODE,
        "caliber": {
            "online_mode": "source_not_门店+store_role_door_as_online",
            "door_profit_pct": "50|role=上门_only",
            "accessories_pct": accessory_meta["pct"],
            "roster": roster_source,
        },
        "accessories": {
            "pct": 30,
            "source": accessory_meta["source"],
            "rows_in_month": accessory_meta["rows_in_month"],
            "unmatched_people": accessory_meta["unmatched_people"],
            "note": accessory_meta["note"],
        },
        "assumptions": assumptions,
        "store_tier_reference": {
            s: {"store_id": STORE_IDS.get(s, ""),
                "orders": store_count[s],
                "base_amt_excl_coupon": float(money(store_base[s])),
                "store_role_door_excluded_amt": float(money(store_excluded_door[s])),
                "tier_rate_by_base": float(store_rate[s]),
                "profit_base": float(money(store_profit_base[s])),
                "tier_rate_by_profit": float(store_rate_ref[s])}
            for s in store_count},
        "active_roster": {
            "source": roster_source,
            "file": roster_file,
            "active": [{"name": roster[n]["name"], "role": roster[n]["role"],
                        "base_salary": float(money(roster[n]["base_salary"]))}
                       for n in sorted(roster)],
            "excluded": list(excluded_agg.values()),
            "excluded_people": len(excluded_agg),
            "excluded_orders": excluded_orders,
        },
    }
    totals = {
        "people": len(people),
        "orders": total_orders,
        "base_salary_total": float(money(sum((Decimal(str(p["base_salary"])) for p in people), Decimal("0")))),
        "door_commission_total": float(money(sum((Decimal(str(p["door_commission"])) for p in people), Decimal("0")))),
        "online_commission_total": float(money(sum((Decimal(str(p["online_commission"])) for p in people), Decimal("0")))),
        "store_commission_total": float(money(sum((Decimal(str(p["store_commission"])) for p in people), Decimal("0")))),
        "accessories_paid_total": float(money(sum((Decimal(str(p["accessories_paid"])) for p in people), Decimal("0")))),
        "accessories_commission_total": float(money(sum((Decimal(str(p["accessories_commission"])) for p in people), Decimal("0")))),
        "gross_total": float(money(sum((Decimal(str(p["gross"])) for p in people), Decimal("0")))),
        "excluded_people": len(excluded_agg),
        "excluded_orders": excluded_orders,
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "totals": totals, "people": people}
    (out_dir / ("payroll_%s.json" % month)).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "excluded_roster.json").write_text(
        json.dumps({"month": month,
                    "rule": "只算带 role 的在职名单。硬排除仅吕文飞、杨燕辉",
                    "source": roster_source,
                    "active": meta["active_roster"]["active"],
                    "excluded": list(excluded_agg.values()),
                    "excluded_people": len(excluded_agg),
                    "excluded_orders": excluded_orders,
                    "paid_orders": total_orders,
                    "rows_in_month": len(in_month)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out_dir / ("payroll_%s.csv" % month), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["月份", "验货人", "角色", "单量", "底薪", "基础", "全勤", "拉新",
                    "上门单量", "上门分润(仅上门角色)", "上门提成(50%·仅上门角色)",
                    "线上单量", "线上金额(不含券)", "线上提成(1%)",
                    "门店提成基数(不含券)", "门店提成", "配件实付", "配件提成(实付×30%)",
                    "分润合计", "应发草案合计", "分润缺失行", "不含券缺失行"])
        for p in people:
            w.writerow([month, p["person"], p["role"], p["orders"], p["base_salary"],
                        p["base_breakdown"]["basic"], p["base_breakdown"]["attendance"],
                        p["base_breakdown"]["new_customer"], p["door_orders"], p["door_profit"],
                        p["door_commission"], p["online_orders"], p["online_amt"],
                        p["online_commission"], p["store_base_amt"], p["store_commission"],
                        p["accessories_paid"], p["accessories_commission"], p["profit_total"],
                        p["gross"], p["profit_missing_rows"], p["base_amt_missing_rows"]])

    print("输入 %d 行 → 月份 %s 内 %d 行（月外 %d 行）" % (len(rows), month, len(in_month), out_month))
    print("线上口径 ONLINE_MODE=%s；另：role=门店且取货方式=上门 的不含券也×1%%，不进门店阶梯"
          % ONLINE_MODE)
    print("上门口径：仅 role=上门 的取货上门分润×50%。底薪缺省 2000，名单可自定义")
    print("名单来源 %s；单量锚点 PASS：计薪单量 %d + 排除单量 %d == 月内行数 %d"
          % (roster_source, total_orders, excluded_orders, len(in_month)))
    for s in sorted(store_count):
        print("  %s(%s)：%d 单，阶梯基数(不含券) %s → 档位 %.0f%%"
              "（门店角色上门单剔出 %s；分润基数对照 %s → %.0f%%）"
              % (s, STORE_IDS.get(s, ""), store_count[s], money(store_base[s]),
                 float(store_rate[s] * 100), money(store_excluded_door[s]),
                 money(store_profit_base[s]), float(store_rate_ref[s] * 100)))
    print("人数 %d；应发草案合计 %s = 底薪 %s + 上门提成 %s + 线上提成 %s + 门店提成 %s + 配件提成 %s"
          % (totals["people"], totals["gross_total"], totals["base_salary_total"],
             totals["door_commission_total"], totals["online_commission_total"],
             totals["store_commission_total"], totals["accessories_commission_total"]))
    if accessory_meta["source"] == "none":
        print("配件：缺源，提成合计 0（见 assumptions，未用 191 列估算）")
    print("个人级输出（仅本机）：%s / .csv" % (out_dir / ("payroll_%s.json" % month)))
    print("在职名单：计薪人数 %d / 排除人数 %d / 排除单量 %d"
          % (totals["people"], len(excluded_agg), excluded_orders))
    print("排除清单（仅本机）：%s" % (out_dir / "excluded_roster.json"))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
