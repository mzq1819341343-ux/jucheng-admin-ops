#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""灌数脚本冒烟：默认 dry-run、双闸才写、只处理当月、只写 salary_realtime_draft。

不连库。个人级样例用虚构姓名，跑完即删。
"""

import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
APPLY_PATH = REPO / "scripts" / "apply_realtime_payroll_draft.py"
SQL_PATH = REPO / "supabase" / "migration_011_salary_realtime_draft.sql"
CONFIG_PATH = REPO / "docs" / "fetch-pipeline" / "pipeline_config.json"
BODY_SHA = "b4cf161eeabaeb727b7d19ca41c81499969fafab70d9902bd53e13a94cf3988a"
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def load_apply():
    spec = importlib.util.spec_from_file_location("apply_realtime_payroll_draft", APPLY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_payroll():
    path = REPO / "scripts" / "payroll" / "realtime_payroll.py"
    spec = importlib.util.spec_from_file_location("realtime_payroll", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ddl_body(text):
    lines = text.splitlines()
    start = 0
    while start < len(lines) and (not lines[start].strip() or lines[start].lstrip().startswith("--")):
        start += 1
    body = "\n".join(lines[start:]).strip("\n")
    return body


def payload(month="2026-10", people=None, effective_from="2026-10"):
    if people is None:
        people = [{
            "person": "示例甲",
            "role": "上门",
            "base_salary": "2000.00",
            "door_commission": "10.50",
            "online_commission": "1.00",
            "store_commission": "2.00",
            "accessories_commission": "0",
            "gross": "2013.50",
        }]
    return {
        "meta": {
            "month": month,
            "effective_from": effective_from,
            "online_mode": "source_not_门店",
            "statement": "pending",
            "draft": True,
        },
        "people": people,
    }


class Capture:
    def __init__(self):
        self.calls = []

    def __call__(self, table, rows):
        self.calls.append((table, rows))
        return len(rows)


def run(mod, data, apply_flag, env, writer, now=NOW):
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory(prefix="apply_draft_") as tmp:
        path = Path(tmp) / "payroll.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        argv = ["--input", str(path)]
        if apply_flag:
            argv.append("--apply")
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main(argv, env=env, writer=writer, now=now)
    return code, out.getvalue(), err.getvalue()


def main():
    mod = load_apply()
    pay = load_payroll()
    assert mod.TARGET_TABLE == "salary_realtime_draft"
    assert mod.env_gate_open({}) is False
    assert mod.env_gate_open({mod.ENV_GATE: "false"}) is False
    assert mod.env_gate_open({mod.ENV_GATE: "1"}) is False
    assert mod.env_gate_open({mod.ENV_GATE: "true"}) is True
    try:
        mod.guard_table("other_table")
    except RuntimeError as exc:
        assert "salary_realtime_draft" in str(exc)
    else:
        raise SystemExit("非目标表应被拒绝")

    sql = SQL_PATH.read_text(encoding="utf-8")
    body = ddl_body(sql)
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == BODY_SHA, "归档正文与线上已执行语句不一致"
    assert "salary_realtime_draft" in sql
    assert "grant select, insert, update, delete on public.salary_realtime_draft to authenticated;" in sql

    cfg_before = CONFIG_PATH.read_bytes()
    cfg = json.loads(cfg_before.decode("utf-8"))
    assert cfg["APPLY"] is False
    assert cfg["UPLOAD"] is False

    cap = Capture()
    code, out, _err = run(mod, payload(), False, {}, cap)
    assert code == 0 and "未写库" in out and "已 upsert" not in out
    assert cap.calls == []
    code, out, _err = run(mod, payload(), True, {}, cap)
    assert code == 0 and "dry-run" in out and cap.calls == []
    code, out, _err = run(mod, payload(), False, {mod.ENV_GATE: "true"}, cap)
    assert code == 0 and "dry-run" in out and cap.calls == []

    code, out, _err = run(mod, payload(), True, {mod.ENV_GATE: "true"}, cap)
    assert code == 0 and "已 upsert 1 行" in out
    assert len(cap.calls) == 1
    table, rows = cap.calls[0]
    assert table == "salary_realtime_draft"
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == set(mod.UPSERT_COLUMNS)
    assert "user_id" not in row
    assert row["month"] == "2026-10"
    assert row["person_name"] == "示例甲"
    assert row["base_amount"] == "2000.00"
    assert row["door_amount"] == "10.50"
    assert row["gross_amount"] == "2013.50"
    assert row["net_amount"] == "2013.50"
    assert row["deduction_amount"] == "0.00"
    assert row["source"] == "realtime_payroll"
    assert "source_not_门店" in row["caliber"]
    assert row["as_of"] == "2026-10-30T12:00:00+08:00"

    req = mod.build_upsert_request(
        "https://example.supabase.co", "test-secret", table, rows)
    parsed = urllib.parse.urlparse(req.full_url)
    assert parsed.path == "/rest/v1/salary_realtime_draft"
    assert "on_conflict=month,person_name" in (parsed.query or "")
    assert req.get_method() == "POST"
    assert req.get_header("Prefer") == "resolution=merge-duplicates,return=minimal"
    assert b"test-secret" not in req.data
    assert "示例甲".encode("utf-8") in req.data
    try:
        mod.build_upsert_request("http://example.supabase.co", "test-secret", table, rows)
    except RuntimeError:
        pass
    else:
        raise SystemExit("非 https 应被拒绝")

    code, _out, err = run(mod, payload(month="2026-11"), True, {mod.ENV_GATE: "true"}, cap)
    assert code == 2 and "当月" in err and len(cap.calls) == 1
    code, _out, err = run(mod, payload(month="2026-09"), True, {mod.ENV_GATE: "true"}, cap)
    assert code == 2 and "生效月" in err and len(cap.calls) == 1
    later = datetime(2026, 11, 1, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    code, _out, err = run(mod, payload(), True, {mod.ENV_GATE: "true"}, cap, now=later)
    assert code == 2 and len(cap.calls) == 1

    dup = payload(people=[
        {"person": "示例甲", "base_salary": "1", "gross": "1"},
        {"person": "示例甲", "base_salary": "2", "gross": "2"},
    ])
    code, _out, err = run(mod, dup, True, {mod.ENV_GATE: "true"}, cap)
    assert code == 1 and "重复" in err and len(cap.calls) == 1

    blocked = payload(people=[{
        "person": pay.HARD_EXCLUDED[0],
        "base_salary": "2000",
        "gross": "2000",
    }])
    code, _out, err = run(mod, blocked, True, {mod.ENV_GATE: "true"}, cap)
    assert code == 3 and len(cap.calls) == 1

    assert CONFIG_PATH.read_bytes() == cfg_before

    tagged = payload()
    tagged["meta"]["caliber"] = {
        "online_mode": "source_not_门店+store_role_door_as_online",
        "door_profit_pct": "50|role=上门_all_profit",
        "door_role_store_pct": "4|trade=门店订单|amt=不含券_else_订单金额",
        "accessories_pct": "30|source=none",
        "roster": "json",
    }
    tagged["meta"]["active_roster"] = {"source": "json", "active": []}
    tagged["people"][0]["accessories_commission"] = "3.00"
    tagged["people"][0]["gross"] = "2016.50"
    cap2 = Capture()
    code, out, _err = run(mod, tagged, True, {mod.ENV_GATE: "true"}, cap2)
    assert code == 0 and len(cap2.calls) == 1
    tagged_row = cap2.calls[0][1][0]
    assert tagged_row["accessories_amount"] == "3.00"
    assert tagged_row["gross_amount"] == "2016.50"
    assert "role" not in tagged_row
    assert "online_caliber=source_not_门店+store_role_door_as_online" in tagged_row["caliber"]
    assert "door=50|role=上门_all_profit" in tagged_row["caliber"]
    assert "door_store=4|trade=门店订单|amt=不含券_else_订单金额" in tagged_row["caliber"]
    assert "accessories=30|source=none" in tagged_row["caliber"]
    assert "roster=json" in tagged_row["caliber"]
    assert cap2.calls[0][0] == "salary_realtime_draft"

    # 双闸开齐但没有库凭据：失败退出，且不使用调用方环境里的密钥
    code, out, err = run(mod, payload(), True, {mod.ENV_GATE: "true"}, None)
    assert code == 1 and "已 upsert" not in out and "未写库" in err
    print("smoke_apply_realtime_draft PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
