#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 realtime_payroll 产出 upsert 进 public.salary_realtime_draft。

默认 dry-run，不连库。真正写库要两道闸同时打开，缺一不可：
  1. 环境变量 ALLOW_APPLY_REALTIME_DRAFT=true
  2. 命令行带 --apply
pipeline_config.json 的 APPLY 保持 false。本脚本只读该键、不改文件，
写库也不靠它放行（与 auto_fetch 的 UPLOAD/APPLY 暂缓不是同一道开关）。

只写表 salary_realtime_draft。只处理 Asia/Shanghai 当月（可用 pipeline
配置 TIMEZONE）。冲突键 (month, person_name)。不删除未出现在本次产出里的行。

用法：
  python3 scripts/apply_realtime_payroll_draft.py \\
    --input ~/Desktop/05_Muse督工/payroll_193/payroll_2026-10.json
  ALLOW_APPLY_REALTIME_DRAFT=true python3 scripts/apply_realtime_payroll_draft.py \\
    --input <payroll JSON> --apply

写库还需 SUPABASE_URL（或 NEXT_PUBLIC_SUPABASE_URL）与
SUPABASE_SERVICE_ROLE_KEY。密钥只从环境读取，不进 argv、不进仓库。
stdout 只打印汇总锚点，不打印个人清单。

退出码：0 成功（含 dry-run）；2 不是当月或早于生效月；3 计薪名单含硬排除人员；
1 其它错误。
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
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from zoneinfo import ZoneInfo

TARGET_TABLE = "salary_realtime_draft"
ENV_GATE = "ALLOW_APPLY_REALTIME_DRAFT"
CENT = Decimal("0.01")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
UPSERT_COLUMNS = (
    "month", "person_name", "as_of",
    "base_amount", "door_amount", "online_amount", "store_amount",
    "accessories_amount", "gross_amount", "deduction_amount", "net_amount",
    "caliber", "source", "updated_at",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "docs" / "fetch-pipeline" / "pipeline_config.json"


def guard_table(table):
    if table != TARGET_TABLE:
        raise RuntimeError("只允许写表 %s" % TARGET_TABLE)
    return table


def env_gate_open(env):
    return str(env.get(ENV_GATE, "")).strip().lower() == "true"


def load_pipeline_config(path):
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not isinstance(cfg, dict):
        raise RuntimeError("pipeline_config.json 不是对象")
    return cfg


def load_payroll_module():
    path = REPO_ROOT / "scripts" / "payroll" / "realtime_payroll.py"
    spec = importlib.util.spec_from_file_location("realtime_payroll", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def current_month(now, tz_name):
    tz = ZoneInfo(tz_name or "Asia/Shanghai")
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    return now.strftime("%Y-%m")


def money(value):
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def money_text(value):
    return format(money(value), "f")


def build_caliber(meta):
    roster = "active" if meta.get("active_roster") else "all"
    return "realtime_payroll|effective_from=%s|online_mode=%s|roster=%s|statement=%s" % (
        meta.get("effective_from") or "",
        meta.get("online_mode") or "",
        roster,
        meta.get("statement") or "",
    )


def rows_from_payload(payload, month, as_of_iso):
    if not isinstance(payload, dict):
        raise RuntimeError("payroll JSON 须为对象")
    meta = payload.get("meta") or {}
    if not isinstance(meta, dict):
        raise RuntimeError("payroll JSON 的 meta 须为对象")
    file_month = str(meta.get("month") or "")
    if not MONTH_RE.match(file_month):
        raise RuntimeError("payroll JSON 缺少合法 meta.month（YYYY-MM）")
    if file_month != month:
        raise RuntimeError("只处理当月 %s，产出月份是 %s" % (month, file_month))
    people = payload.get("people")
    if not isinstance(people, list):
        raise RuntimeError("payroll JSON 缺少 people 数组")
    caliber = build_caliber(meta)
    seen = set()
    rows = []
    for person in people:
        if not isinstance(person, dict):
            raise RuntimeError("people 中有非对象行")
        name = str(person.get("person_name") or person.get("person") or "").strip()
        if not name:
            raise RuntimeError("存在空姓名，已中止")
        if name in seen:
            raise RuntimeError("同一产出里姓名重复，已中止")
        seen.add(name)
        base = money(person.get("base_salary", person.get("base_amount")))
        door = money(person.get("door_commission", person.get("door_amount")))
        online = money(person.get("online_commission", person.get("online_amount")))
        store = money(person.get("store_commission", person.get("store_amount")))
        accessories = money(person.get("accessories_commission", person.get("accessories_amount")))
        if person.get("gross") is None and person.get("gross_amount") is None:
            gross = money(base + door + online + store + accessories)
        else:
            gross = money(person.get("gross", person.get("gross_amount")))
        if person.get("deduction_amount") is None and person.get("deduction") is None:
            deduction = Decimal("0.00")
        else:
            deduction = money(person.get("deduction_amount", person.get("deduction")))
        if person.get("net_amount") is None and person.get("net") is None:
            net = money(gross - deduction)
        else:
            net = money(person.get("net_amount", person.get("net")))
        row = {
            "month": month,
            "person_name": name,
            "as_of": as_of_iso,
            "base_amount": format(base, "f"),
            "door_amount": format(door, "f"),
            "online_amount": format(online, "f"),
            "store_amount": format(store, "f"),
            "accessories_amount": format(accessories, "f"),
            "gross_amount": format(gross, "f"),
            "deduction_amount": format(deduction, "f"),
            "net_amount": format(net, "f"),
            "caliber": caliber,
            "source": "realtime_payroll",
            "updated_at": as_of_iso,
        }
        extra = set(row) - set(UPSERT_COLUMNS)
        if extra:
            raise RuntimeError("灌数字段越界")
        rows.append(row)
    return rows


def totals_of(rows):
    keys = ("base_amount", "door_amount", "online_amount", "store_amount",
            "accessories_amount", "gross_amount")
    out = {k: Decimal("0.00") for k in keys}
    for row in rows:
        for key in keys:
            out[key] += Decimal(row[key])
    return {k: format(money(v), "f") for k, v in out.items()}


def build_upsert_request(base_url, service_key, table, rows):
    table = guard_table(table)
    base = (base_url or "").strip().rstrip("/")
    if not base.startswith("https://"):
        raise RuntimeError("SUPABASE_URL 须为 https")
    if not service_key or not str(service_key).strip():
        raise RuntimeError("缺少 SUPABASE_SERVICE_ROLE_KEY")
    if not rows:
        raise RuntimeError("没有行可写")
    url = "%s/rest/v1/%s?on_conflict=month,person_name" % (base, table)
    path = urllib.parse.urlparse(url).path
    if path != "/rest/v1/" + TARGET_TABLE:
        raise RuntimeError("只允许写表 %s" % TARGET_TABLE)
    body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("apikey", service_key)
    req.add_header("Authorization", "Bearer " + service_key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "resolution=merge-duplicates,return=minimal")
    return req


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def postgrest_upsert(base_url, service_key, table, rows):
    req = build_upsert_request(base_url, service_key, table, rows)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            resp.read()
    except urllib.error.HTTPError as exc:
        exc.read()
        raise RuntimeError("写库失败 HTTP %s" % exc.code) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("写库连接失败") from exc
    if status not in (200, 201, 204):
        raise RuntimeError("写库失败 HTTP %s" % status)
    return len(rows)


def _as_of_iso(now, tz_name):
    tz = ZoneInfo(tz_name or "Asia/Shanghai")
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    return now.replace(microsecond=0).isoformat()


def main(argv=None, env=None, writer=None, now=None):
    ap = argparse.ArgumentParser(description="实时工资草案灌入 salary_realtime_draft（默认 dry-run）")
    ap.add_argument("--input", required=True, help="realtime_payroll 产出的 payroll_<month>.json")
    ap.add_argument("--apply", action="store_true",
                    help="与 ALLOW_APPLY_REALTIME_DRAFT=true 同时成立才写库；否则 dry-run")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help="pipeline_config.json（只读，不修改 APPLY）")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    env = os.environ if env is None else env
    now = datetime.now(ZoneInfo("Asia/Shanghai")) if now is None else now

    cfg_path = Path(args.config)
    cfg = load_pipeline_config(cfg_path)
    config_apply = bool(cfg.get("APPLY", False))
    tz_name = cfg.get("TIMEZONE") or "Asia/Shanghai"
    month = current_month(now, tz_name)
    gate_env = env_gate_open(env)
    will_write = bool(args.apply) and gate_env

    text = Path(args.input).read_text(encoding="utf-8")
    payload = json.loads(text, parse_float=Decimal)
    pay = load_payroll_module()
    file_month = str((payload.get("meta") or {}).get("month") or "")
    if file_month and file_month < pay.EFFECTIVE_FROM:
        print("月份 %s 早于生效月 %s，不灌数" % (file_month, pay.EFFECTIVE_FROM), file=sys.stderr)
        return 2
    try:
        rows = rows_from_payload(payload, month, _as_of_iso(now, tz_name))
    except RuntimeError as exc:
        message = str(exc)
        if message.startswith("只处理当月"):
            print(message, file=sys.stderr)
            return 2
        print(message, file=sys.stderr)
        return 1
    leaked = [row["person_name"] for row in rows if row["person_name"] in pay.HARD_EXCLUDED]
    if leaked:
        print("计薪名单含硬排除人员，已中止", file=sys.stderr)
        return 3

    totals = totals_of(rows)
    print("月份 %s（当月，时区 %s）" % (month, tz_name))
    print("pipeline_config APPLY=%s（本脚本不修改该键；写库不靠它放行）"
          % str(config_apply).lower())
    print("双闸：%s=%s --apply=%s → %s" % (
        ENV_GATE, str(gate_env).lower(), str(bool(args.apply)).lower(),
        "apply" if will_write else "dry-run"))
    print("人数 %s；应发合计 %s = 底薪 %s + 上门 %s + 线上 %s + 门店 %s + 配件 %s" % (
        len(rows), totals["gross_amount"], totals["base_amount"], totals["door_amount"],
        totals["online_amount"], totals["store_amount"], totals["accessories_amount"]))

    if not will_write:
        print("未写库。写库须同时 %s=true 与 --apply，且只 upsert %s，键 (month, person_name)。"
              % (ENV_GATE, TARGET_TABLE))
        return 0

    if writer is None:
        url = (env.get("SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
        key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not url or not key:
            print("双闸已开，但缺少 SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY，未写库",
                  file=sys.stderr)
            return 1

        def writer(table, batch, _url=url, _key=key):
            return postgrest_upsert(_url, _key, table, batch)

    if not rows:
        print("当月产出 0 行，未发写请求。")
        return 0
    try:
        written = writer(guard_table(TARGET_TABLE), rows)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("已 upsert %s 行 → %s（键 month, person_name）" % (written, TARGET_TABLE))
    return 0


if __name__ == "__main__":
    sys.exit(main())
