#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""占位 DO NOTHING、底薪同步保留补贴奖金、缺行先占位。不连库。"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
MOD_PATH = REPO / "scripts" / "payroll" / "draft_employee_write.py"
ENSURE = REPO / "scripts" / "payroll" / "ensure_employee_placeholder.py"
SYNC = REPO / "scripts" / "payroll" / "sync_base_salary.py"
CONFIG = REPO / "docs" / "fetch-pipeline" / "pipeline_config.json"
SQL11 = REPO / "supabase" / "migration_011_salary_realtime_draft.sql"
SQL16 = REPO / "supabase" / "migration_016_salary_realtime_draft_subsidy_bonus.sql"
NOW = datetime(2026, 11, 2, 9, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
USER_ID = "00000000-0000-4000-8000-0000000000a1"


def load():
    spec = importlib.util.spec_from_file_location("draft_employee_write_under_test", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_main(mod, mode, argv, env, reader=None, writer=None, patcher=None,
             employee_reader=None, now=NOW):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(
            argv, mode=mode, env=env, now=now,
            existing_reader=reader, writer=writer, patcher=patcher,
            employee_reader=employee_reader)
    return code, out.getvalue(), err.getvalue()


def person(base="2000", role="上门", status=None, name="示例甲", user_id=None):
    raw = {"person_name": name, "base_salary": base, "work_role": role}
    if status is not None:
        raw["employment_status"] = status
    if user_id is not None:
        raw["user_id"] = user_id
    return raw


def draft_row(**kwargs):
    row = {
        "person_name": "示例甲",
        "base_amount": "2000.00",
        "door_amount": "10.00",
        "online_amount": "1.00",
        "store_amount": "2.00",
        "accessories_amount": "3.00",
        "subsidy_amount": "12.50",
        "bonus_amount": "8.00",
        "deduction_amount": "4.00",
        "gross_amount": "2036.50",
        "net_amount": "2032.50",
        "source": "realtime_payroll",
        "caliber": "realtime_payroll",
    }
    row.update(kwargs)
    return row


class Log:
    def __init__(self):
        self.events = []
        self.row = None

    def reader(self, month, name):
        self.events.append(("read", month, name))
        if self.row is None:
            return []
        return [dict(self.row)]

    def insert(self, table, rows):
        self.events.append(("insert", table, rows))
        self.row = dict(rows[0])
        return 1

    def patch(self, table, body):
        self.events.append(("patch", table, body))
        if self.row is None:
            self.row = {}
        self.row.update(body)
        return 1


def main():
    mod = load()
    pay = mod.payroll_mod()
    apply = mod.apply_mod()
    assert mod.SOURCE_PLACEHOLDER == "employee_placeholder"
    assert mod.OCTOBER_NO_SEED == "2026-10"
    assert pay.EFFECTIVE_FROM == "2026-10"
    assert pay.HARD_EXCLUDED == ("吕文飞", "杨燕辉")
    assert apply.ENV_GATE == "ALLOW_APPLY_REALTIME_DRAFT"
    assert mod.env_gate_open({}) is False
    assert mod.env_gate_open({apply.ENV_GATE: "true"}) is True
    assert mod.env_gate_open({apply.ENV_GATE: "1"}) is False

    sql11 = SQL11.read_text(encoding="utf-8")
    sql16 = SQL16.read_text(encoding="utf-8")
    assert "source text not null default 'realtime_payroll'" in sql11
    assert "unique (month, person_name)" in sql11
    assert "subsidy_amount" in sql16 and "bonus_amount" in sql16
    cfg_before = CONFIG.read_bytes()
    cfg = json.loads(cfg_before.decode("utf-8"))
    assert cfg["APPLY"] is False and cfg["UPLOAD"] is False

    try:
        mod.guard_write_table("salary_monthly")
    except mod.DraftWriteRejected as exc:
        assert exc.code == 1 and "salary_realtime_draft" in str(exc)
    else:
        raise SystemExit("非目标表应被拒绝")

    env_closed = {}
    env_open = {apply.ENV_GATE: "true"}
    log = Log()
    code, out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "2000", "--work-role", "上门"],
        env_closed, reader=log.reader, writer=log.insert, now=NOW)
    assert code == 0, err
    assert log.events == []
    assert "未写库" in out and "dry-run" in out
    assert "十月禁灌" in out and "生产禁盲开" in out
    assert "source=employee_placeholder" in out
    assert "base_amount=2000.00" in out
    assert "door_amount=0.00" in out
    assert "不因角色改成 0" in out
    assert "DO NOTHING" in out
    assert "UPLOAD=false" in out and "APPLY=false" in out
    assert "已占位" not in out

    code, out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "2000", "--work-role", "上门",
         "--month", "2026-11", "--apply"],
        env_closed, reader=log.reader, writer=log.insert)
    assert code == 0 and "dry-run" in out and log.events == [], err
    code, out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "2000", "--work-role", "上门",
         "--month", "2026-11"],
        env_open, reader=log.reader, writer=log.insert)
    assert code == 0 and "dry-run" in out and log.events == [], err

    # 上门跟表：1800 原样占位，业绩/补贴/奖金为 0。
    inserted = Log()
    code, out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "1800", "--work-role", "上门",
         "--user-id", USER_ID, "--month", "2026-11", "--apply"],
        env_open, reader=inserted.reader, writer=inserted.insert)
    assert code == 0, err
    assert [event[0] for event in inserted.events] == ["read", "insert"]
    table, rows = inserted.events[1][1], inserted.events[1][2]
    assert table == "salary_realtime_draft"
    assert len(rows) == 1
    row = rows[0]
    assert row["base_amount"] == "1800.00"
    assert row["door_amount"] == row["online_amount"] == row["store_amount"] == "0.00"
    assert row["accessories_amount"] == "0.00"
    assert row["subsidy_amount"] == "0.00" and row["bonus_amount"] == "0.00"
    assert row["deduction_amount"] == "0.00"
    assert row["gross_amount"] == "1800.00" and row["net_amount"] == "1800.00"
    assert row["source"] == "employee_placeholder"
    assert "pending_payroll" in row["caliber"] and "base_follows_table" in row["caliber"]
    assert "work_role=上门" in row["caliber"]
    assert row["user_id"] == USER_ID
    assert row["month"] == "2026-11" and row["person_name"] == "示例甲"
    assert "已占位 1 行" in out and "ignore-duplicates" in out

    req = mod.build_placeholder_insert_request(
        "https://example.supabase.co", "test-secret", row)
    parsed = urllib.parse.urlparse(req.full_url)
    assert parsed.path == "/rest/v1/salary_realtime_draft"
    assert "on_conflict=month,person_name" in (parsed.query or "")
    assert req.get_method() == "POST"
    assert req.get_header("Prefer") == "resolution=ignore-duplicates,return=representation"
    assert "merge-duplicates" not in (req.get_header("Prefer") or "")
    assert b"test-secret" not in req.data
    body = json.loads(req.data.decode("utf-8"))
    assert body[0]["source"] == "employee_placeholder"
    assert body[0]["base_amount"] == "1800.00"
    try:
        mod.build_placeholder_insert_request("http://example.supabase.co", "test-secret", row)
    except mod.DraftWriteRejected:
        pass
    else:
        raise SystemExit("非 https 应被拒绝")

    # 已有草案：DO NOTHING，不覆盖底薪、补贴、奖金、业绩。
    existing = draft_row(base_amount="1500.00", subsidy_amount="12.50", bonus_amount="8.00",
                         door_amount="9.00", source="realtime_payroll")

    def have_row(month, name):
        assert month == "2026-11" and name == "示例甲"
        return [dict(existing)]

    skipped = Log()

    def refuse_insert(table, rows):
        raise SystemExit("已有行不应插入")

    code, out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "1800", "--work-role", "上门",
         "--month", "2026-11", "--apply"],
        env_open, reader=have_row, writer=refuse_insert)
    assert code == 0, err
    assert "DO NOTHING" in out and "未发写请求" in out
    assert "已占位" not in out
    assert skipped.events == []

    kept = mod.ensure_employee_placeholder(
        person("1800", "上门"), month="2026-11", now=NOW, env=env_open, apply=True,
        existing_reader=have_row, writer=refuse_insert)
    assert kept["action"] == "skip" and kept["wrote"] is False
    assert kept["base_amount"] == "1500.00"
    assert kept["requested_base_amount"] == "1800.00"
    assert kept["subsidy_amount"] == "12.50" and kept["bonus_amount"] == "8.00"
    assert kept["source"] == "realtime_payroll"
    assert kept["insert_row"] is None and kept["patch"] is None

    # 底薪同步：保留手填补贴奖金和业绩列，只改底薪并重算应发。
    synced = Log()

    def always_existing(month, name):
        return [draft_row()]

    def refuse_sync_insert(table, rows):
        raise SystemExit("已有草案不应再占位")

    code, out, err = run_main(
        mod, "sync",
        ["--person-name", "示例甲", "--base-salary", "2500", "--work-role", "上门",
         "--month", "2026-11", "--apply"],
        env_open, reader=always_existing, writer=refuse_sync_insert, patcher=synced.patch)
    assert code == 0, err
    assert [event[0] for event in synced.events] == ["patch"]
    patch = synced.events[0][2]
    assert set(patch) == set(mod.PATCH_KEYS)
    assert not (set(patch) & set(mod.FORBIDDEN_PATCH_KEYS))
    gross, net = mod.payable_texts(2500, 10, 1, 2, 3, "12.50", 8, 4, patch["as_of"])
    assert gross == "2536.50" and net == "2532.50"
    assert patch["base_amount"] == "2500.00"
    assert patch["gross_amount"] == gross and patch["net_amount"] == net
    assert "subsidy_amount" not in patch and "bonus_amount" not in patch
    assert "door_amount" not in patch
    assert "保留 subsidy_amount=12.50 bonus_amount=8.00" in out
    assert "door_amount=10.00" in out

    preq = mod.build_base_patch_request(
        "https://example.supabase.co", "test-secret", "2026-11", "示例 甲", patch)
    preq_parsed = urllib.parse.urlparse(preq.full_url)
    assert preq.get_method() == "PATCH"
    assert preq_parsed.path == "/rest/v1/salary_realtime_draft"
    assert "month=eq.2026-11" in (preq_parsed.query or "")
    assert "person_name=eq." in (preq_parsed.query or "")
    assert "示例" not in (preq_parsed.query or "")
    pdata = json.loads(preq.data.decode("utf-8"))
    assert "subsidy_amount" not in pdata and "bonus_amount" not in pdata
    assert pdata["base_amount"] == "2500.00"
    assert b"test-secret" not in preq.data

    # 缺行：先占位，再按读回写底薪。读回若已有手填补贴，PATCH 仍不覆盖。
    race_reads = {"n": 0}
    rich = draft_row(
        base_amount="0.00", door_amount="0.00", online_amount="0.00",
        store_amount="0.00", accessories_amount="0.00",
        subsidy_amount="5.00", bonus_amount="1.00", deduction_amount="0.00",
        source="realtime_payroll")

    def race_reader(month, name):
        race_reads["n"] += 1
        if race_reads["n"] == 1:
            return []
        return [dict(rich)]

    race_log = []

    def race_insert(table, rows):
        race_log.append(("insert", rows[0]["source"], rows[0]["subsidy_amount"], rows[0]["base_amount"]))
        assert rows[0]["source"] == "employee_placeholder"
        assert rows[0]["subsidy_amount"] == "0.00"
        assert rows[0]["base_amount"] == "2200.00"
        assert rows[0]["door_amount"] == "0.00"
        return 1

    def race_patch(table, body):
        race_log.append(("patch", body["base_amount"], body["gross_amount"], body["net_amount"]))
        assert "subsidy_amount" not in body and "bonus_amount" not in body
        return 1

    raced = mod.sync_employee_base_salary(
        person("2200", "上门"), month="2026-11", now=NOW, env=env_open, apply=True,
        existing_reader=race_reader, writer=race_insert, patcher=race_patch)
    assert raced["action"] == "placeholder_then_patch" and raced["wrote"] is True
    assert race_log[0][0] == "insert" and race_log[1][0] == "patch"
    assert raced["base_amount"] == "2200.00"
    assert raced["gross_amount"] == "2206.00" and raced["net_amount"] == "2206.00"
    assert raced["subsidy_amount"] == "5.00" and raced["bonus_amount"] == "1.00"
    assert raced["source"] == "realtime_payroll"
    assert raced["patch"]["gross_amount"] == "2206.00"

    # 缺行且读回仍为空：占位行的 0 补贴保持，底薪写在 PATCH。
    fresh = Log()
    created = mod.sync_employee_base_salary(
        person("1800.5", "上门"), month="2026-11", now=NOW, env=env_open, apply=True,
        existing_reader=fresh.reader, writer=fresh.insert, patcher=fresh.patch)
    assert created["action"] == "placeholder_then_patch"
    assert [event[0] for event in fresh.events] == ["read", "insert", "read", "patch"]
    assert fresh.events[1][2][0]["base_amount"] == "1800.50"
    assert fresh.events[1][2][0]["source"] == "employee_placeholder"
    assert fresh.events[1][2][0]["subsidy_amount"] == "0.00"
    assert fresh.row["subsidy_amount"] == "0.00" and fresh.row["bonus_amount"] == "0.00"
    assert fresh.row["base_amount"] == "1800.50"
    assert fresh.row["gross_amount"] == "1800.50" and fresh.row["net_amount"] == "1800.50"
    assert fresh.row["door_amount"] == "0.00"
    assert "subsidy_amount" not in fresh.events[3][2]

    # 只给 user_id：写库时读 employees，上门底薪跟表，不改成 0。
    fetched = {}

    def employee_reader(spec):
        fetched["spec"] = spec
        return [{
            "person_name": "示例甲",
            "base_salary": "1600",
            "work_role": "上门",
            "employment_status": "在职",
            "user_id": USER_ID,
        }]

    via_table = Log()
    code, out, err = run_main(
        mod, "placeholder",
        ["--user-id", USER_ID, "--month", "2026-11", "--apply"],
        env_open, reader=via_table.reader, writer=via_table.insert,
        employee_reader=employee_reader)
    assert code == 0, err
    assert fetched["spec"]["user_id"] == USER_ID
    assert via_table.row["base_amount"] == "1600.00"
    assert via_table.row["source"] == "employee_placeholder"
    assert "work_role=上门" in via_table.row["caliber"]
    assert via_table.row["base_amount"] != "0.00"

    # 表上已离职：不占位。
    def left_reader(spec):
        return [{
            "person_name": "示例甲", "base_salary": "1600", "work_role": "门店",
            "employment_status": "离职", "user_id": USER_ID,
        }]

    quiet = Log()
    code, out, err = run_main(
        mod, "placeholder",
        ["--user-id", USER_ID, "--month", "2026-11", "--apply"],
        env_open, reader=quiet.reader, writer=quiet.insert, employee_reader=left_reader)
    assert code == 4 and "非在职" in err and quiet.events == [], err

    code, _out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "2000", "--employment-status", "离职",
         "--month", "2026-11", "--apply"],
        env_open, reader=quiet.reader, writer=quiet.insert)
    assert code == 4 and quiet.events == [], err

    code, _out, err = run_main(
        mod, "sync",
        ["--person-name", "示例甲", "--base-salary", "2100", "--employment-status", "离职",
         "--month", "2026-11", "--apply"],
        env_open, reader=lambda month, name: [], writer=quiet.insert, patcher=quiet.patch)
    assert code == 4 and "不占位" in err and quiet.events == [], err

    left_sync = Log()
    code, out, err = run_main(
        mod, "sync",
        ["--person-name", "示例甲", "--base-salary", "2100", "--employment-status", "离职",
         "--month", "2026-11", "--apply"],
        env_open, reader=always_existing, writer=refuse_sync_insert, patcher=left_sync.patch)
    assert code == 0, err
    assert left_sync.events[0][2]["base_amount"] == "2100.00"
    assert "subsidy_amount" not in left_sync.events[0][2]

    code, _out, err = run_main(
        mod, "placeholder",
        ["--person-name", "吕文飞", "--base-salary", "2000", "--month", "2026-11", "--apply"],
        env_open, reader=quiet.reader, writer=quiet.insert)
    assert code == 3 and "硬排除" in err and quiet.events == [], err

    code, _out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "-1", "--month", "2026-11"],
        env_closed)
    assert code == 1 and "不能为负" in err

    code, _out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--month", "2026-11"],
        env_closed)
    assert code == 1 and "不读库" in err

    code, _out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "2000", "--month", "2026-10", "--apply"],
        env_open, reader=quiet.reader, writer=quiet.insert)
    assert code == 2 and "十月禁灌" in err and quiet.events == [], err

    code, _out, err = run_main(
        mod, "sync",
        ["--person-name", "示例甲", "--base-salary", "2000", "--month", "2026-09", "--apply"],
        env_open, reader=quiet.reader, writer=quiet.insert, patcher=quiet.patch)
    assert code == 2 and "生效月" in err and quiet.events == [], err

    later = datetime(2026, 10, 15, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    code, _out, err = run_main(
        mod, "placeholder",
        ["--person-name", "示例甲", "--base-salary", "2000"],
        env_open, reader=quiet.reader, writer=quiet.insert, now=later)
    assert code == 2 and "十月禁灌" in err and quiet.events == [], err

    code, out, err = run_main(
        mod, "sync",
        ["--person-name", "示例甲", "--base-salary", "2000", "--work-role", "门店"],
        env_closed, now=NOW)
    assert code == 0 and "未写库" in out and "dry-run 不读库" in out and "2026-11" in out, err

    dup_called = {"n": 0}

    def dup_reader(month, name):
        return [draft_row(), draft_row(person_name="示例甲")]

    def count_insert(table, rows):
        dup_called["n"] += 1
        return 1

    try:
        mod.ensure_employee_placeholder(
            person(), month="2026-11", now=NOW, env=env_open, apply=True,
            existing_reader=dup_reader, writer=count_insert)
    except mod.DraftWriteRejected as exc:
        assert exc.code == 1 and "多行" in str(exc)
    else:
        raise SystemExit("重名草案应中止")
    assert dup_called["n"] == 0

    get_req = mod.build_draft_get_request(
        "https://example.supabase.co", "test-secret", "2026-11", "示例甲")
    get_parsed = urllib.parse.urlparse(get_req.full_url)
    assert get_req.get_method() == "GET" and get_req.data is None
    assert get_parsed.path == "/rest/v1/salary_realtime_draft"
    assert "subsidy_amount" in get_parsed.query and "bonus_amount" in get_parsed.query
    assert "door_amount" in get_parsed.query
    assert "test-secret" not in (get_parsed.query or "")

    emp_req = mod.build_employee_get_request(
        "https://example.supabase.co", "test-secret", {"person_name": "示例甲"})
    emp_parsed = urllib.parse.urlparse(emp_req.full_url)
    assert emp_parsed.path == "/rest/v1/employees"
    assert emp_req.get_method() == "GET"
    assert "base_salary" in emp_parsed.query and "employment_status" in emp_parsed.query

    # 应发与 apply.recompute_payable 同一结果。
    gross, net = mod.payable_texts("1500", "10", "1", "2", "3", "12.50", "0", "10",
                                   "2026-11-02T09:30:00+08:00")
    probe = {key: "0.00" for key in apply.UPSERT_COLUMNS}
    probe.update({
        "month": "2026-11", "person_name": "示例甲",
        "as_of": "2026-11-02T09:30:00+08:00", "updated_at": "2026-11-02T09:30:00+08:00",
        "caliber": "x", "source": "realtime_payroll",
        "base_amount": "1500.00", "door_amount": "10.00", "online_amount": "1.00",
        "store_amount": "2.00", "accessories_amount": "3.00",
        "subsidy_amount": "12.50", "bonus_amount": "0.00", "deduction_amount": "10.00",
    })
    apply.recompute_payable(probe)
    assert gross == probe["gross_amount"] == "1528.50"
    assert net == probe["net_amount"] == "1518.50"

    child_env = os.environ.copy()
    child_env.pop("ALLOW_APPLY_REALTIME_DRAFT", None)
    child_env.pop("SUPABASE_SERVICE_ROLE_KEY", None)
    child_env.pop("SUPABASE_URL", None)
    proc = subprocess.run(
        [sys.executable, str(ENSURE), "--person-name", "示例甲", "--base-salary", "2000",
         "--work-role", "上门", "--month", "2026-11"],
        cwd=str(REPO), env=child_env, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "未写库" in proc.stdout and "employee_placeholder" in proc.stdout
    assert "1800" not in proc.stdout
    proc_sync = subprocess.run(
        [sys.executable, str(SYNC), "--person-name", "示例甲", "--base-salary", "2200",
         "--month", "2026-11"],
        cwd=str(REPO), env=child_env, capture_output=True, text=True, check=False)
    assert proc_sync.returncode == 0, proc_sync.stderr
    assert "未写库" in proc_sync.stdout and "十月禁灌" in proc_sync.stdout

    assert CONFIG.read_bytes() == cfg_before
    again = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert again["APPLY"] is False and again["UPLOAD"] is False
    print("smoke_employee_draft_write PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
