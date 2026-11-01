#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""灌数脚本冒烟：默认 dry-run、双闸才写、默认只处理当月、只写 salary_realtime_draft。

--month 只改目标月，不单独放行写库。不连库。个人级样例用虚构姓名，跑完即删。
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


def run(mod, data, apply_flag, env, writer, now=NOW, month=None, existing_reader=None):
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory(prefix="apply_draft_") as tmp:
        path = Path(tmp) / "payroll.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        argv = ["--input", str(path)]
        if month is not None:
            argv.extend(["--month", month])
        if apply_flag:
            argv.append("--apply")
        with redirect_stdout(out), redirect_stderr(err):
            code = mod.main(
                argv, env=env, writer=writer, now=now, existing_reader=existing_reader)
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
    assert row["subsidy_amount"] == "0.00"
    assert row["bonus_amount"] == "0.00"
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
    body = json.loads(req.data.decode("utf-8"))
    assert body[0]["subsidy_amount"] == "0.00"
    assert body[0]["bonus_amount"] == "0.00"
    assert "示例甲".encode("utf-8") in req.data
    adj = mod.build_adjustments_request("https://example.supabase.co", "test-secret", "2026-10")
    adj_parsed = urllib.parse.urlparse(adj.full_url)
    assert adj.get_method() == "GET"
    assert adj.data is None
    assert adj_parsed.path == "/rest/v1/salary_realtime_draft"
    adj_qs = urllib.parse.parse_qs(adj_parsed.query)
    assert adj_qs["select"] == [mod.HAND_FILLED_SELECT]
    assert "subsidy_amount" in adj_qs["select"][0] and "bonus_amount" in adj_qs["select"][0]
    assert adj_qs["month"] == ["eq.2026-10"]
    assert "test-secret" not in (adj_parsed.query or "")
    try:
        mod.build_adjustments_request("http://example.supabase.co", "test-secret", "2026-10")
    except RuntimeError:
        pass
    else:
        raise SystemExit("读取补贴时非 https 应被拒绝")
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
    code, out, err = run(mod, payload(), True, {mod.ENV_GATE: "true"}, cap, now=later)
    assert code == 2 and "当月" in err and "终数回写月" not in out and len(cap.calls) == 1
    # 日历已是 2026-11：省略 --month 仍拒绝 2026-10，即使双闸开齐也不写。
    code, out, err = run(mod, payload(month="2026-10"), True, {mod.ENV_GATE: "true"}, cap, now=later)
    assert code == 2 and "当月" in err and "2026-11" in err and len(cap.calls) == 1
    code, out, err = run(mod, payload(month="2026-11"), False, {}, Capture(), now=later)
    assert code == 0 and "当月" in out and "终数回写月" not in out and "未写库" in out

    # --month=2026-10：匹配的 payload 在 dry-run 路径放行，单闸仍不写。
    cap_m = Capture()
    code, out, err = run(
        mod, payload(month="2026-10"), False, {}, cap_m, now=later, month="2026-10")
    assert code == 0, err
    assert cap_m.calls == []
    assert "未写库" in out and "dry-run" in out
    assert "终数回写月" in out and "explicit month" in out and "2026-10" in out
    code, out, err = run(
        mod, payload(month="2026-10"), True, {}, cap_m, now=later, month="2026-10")
    assert code == 0 and "dry-run" in out and cap_m.calls == []
    code, out, err = run(
        mod, payload(month="2026-10"), False, {mod.ENV_GATE: "true"}, cap_m,
        now=later, month="2026-10")
    assert code == 0 and "dry-run" in out and cap_m.calls == []
    code, out, err = run(
        mod, payload(month="2026-10"), True, {mod.ENV_GATE: "true"}, cap_m,
        now=later, month="2026-10")
    assert code == 0, err
    assert len(cap_m.calls) == 1
    assert cap_m.calls[0][0] == "salary_realtime_draft"
    assert cap_m.calls[0][1][0]["month"] == "2026-10"
    assert "终数回写月" in out and "已 upsert 1 行" in out
    code, _out, err = run(
        mod, payload(month="2026-11"), True, {mod.ENV_GATE: "true"}, cap_m,
        now=later, month="2026-10")
    assert code == 2 and "指定月" in err and "2026-10" in err and len(cap_m.calls) == 1
    code, _out, err = run(
        mod, payload(month="2026-10"), False, {}, cap_m, now=later, month="2026-13")
    assert code == 1 and "YYYY-MM" in err and len(cap_m.calls) == 1
    blocked_explicit = payload(people=[{
        "person": pay.HARD_EXCLUDED[0],
        "base_salary": "2000",
        "gross": "2000",
    }])
    code, _out, err = run(
        mod, blocked_explicit, True, {mod.ENV_GATE: "true"}, cap_m,
        now=later, month="2026-10")
    assert code == 3 and len(cap_m.calls) == 1

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

    reads = {"n": 0}

    def counting_reader():
        reads["n"] += 1
        raise RuntimeError("dry-run 不应读取补贴")

    code, out, _err = run(
        mod, payload(), False, {}, Capture(), existing_reader=counting_reader)
    assert code == 0 and reads["n"] == 0 and "未写库" in out and "补贴" in out

    def boom():
        raise RuntimeError("读取已有补贴/奖金失败 HTTP 400，已中止未写库")

    cap_boom = Capture()
    code, out, err = run(
        mod, payload(), True, {mod.ENV_GATE: "true"}, cap_boom, existing_reader=boom)
    assert code == 1 and cap_boom.calls == [] and "补贴" in err and "已 upsert" not in out

    def dup_reader():
        return [
            {"person_name": "示例甲", "subsidy_amount": "1", "bonus_amount": "1"},
            {"person_name": "示例甲", "subsidy_amount": "2", "bonus_amount": "2"},
        ]

    cap_dup = Capture()
    code, _out, err = run(
        mod, payload(), True, {mod.ENV_GATE: "true"}, cap_dup, existing_reader=dup_reader)
    assert code == 1 and cap_dup.calls == [] and "重复" in err

    def preserved_reader():
        return [
            {"person_name": "示例甲", "subsidy_amount": "12.50", "bonus_amount": "0.00"},
            {"person_name": "不在本次", "subsidy_amount": "9.00", "bonus_amount": "8.00"},
        ]

    hand = payload(people=[
        {
            "person": "示例甲",
            "base_salary": "1500",
            "door_commission": "10",
            "online_commission": "1",
            "store_commission": "2",
            "accessories_commission": "3",
            "subsidy": "99",
            "bonus": "88",
            "deduction_amount": "10",
            "gross": "0",
        },
        {
            "person": "示例乙",
            "base_salary": "1000",
            "subsidy": "50",
            "bonus": "40",
            "gross": "1090",
        },
    ])
    cap_p = Capture()
    code, out, err = run(
        mod, hand, True, {mod.ENV_GATE: "true"}, cap_p, existing_reader=preserved_reader)
    assert code == 0, err
    assert len(cap_p.calls) == 1
    assert cap_p.calls[0][0] == "salary_realtime_draft"
    written = {row["person_name"]: row for row in cap_p.calls[0][1]}
    assert set(written) == {"示例甲", "示例乙"}
    kept = written["示例甲"]
    assert kept["base_amount"] == "1500.00"
    assert kept["door_amount"] == "10.00"
    assert kept["online_amount"] == "1.00"
    assert kept["store_amount"] == "2.00"
    assert kept["accessories_amount"] == "3.00"
    assert kept["subsidy_amount"] == "12.50"
    assert kept["bonus_amount"] == "0.00"
    assert kept["gross_amount"] == "1528.50"
    assert kept["deduction_amount"] == "10.00"
    assert kept["net_amount"] == "1518.50"
    fresh = written["示例乙"]
    assert fresh["subsidy_amount"] == "0.00"
    assert fresh["bonus_amount"] == "0.00"
    assert fresh["base_amount"] == "1000.00"
    assert fresh["gross_amount"] == "1000.00"
    assert fresh["net_amount"] == "1000.00"
    assert "不在本次" not in written
    assert "已 upsert 2 行" in out

    # 双闸开齐但没有库凭据：失败退出，且不使用调用方环境里的密钥
    code, out, err = run(mod, payload(), True, {mod.ENV_GATE: "true"}, None)
    assert code == 1 and "已 upsert" not in out and "未写库" in err
    print("smoke_apply_realtime_draft PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
