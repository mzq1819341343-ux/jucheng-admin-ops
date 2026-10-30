#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工资新口径冒烟。

线上仍是 ONLINE_MODE=source_not_门店。另测：
  门店角色的上门单 → 线上 ×1%，不进 door、不进门店阶梯；
  上门角色的上门单 → 分润 ×50%；
  无配件源 → 0；有独立导出 → 实付 ×30%；191 列冒充被拒绝；
  缺 role / 仅姓名列表 → 失败；自定义底薪；
  硬排除只剩吕文飞、杨燕辉，张浩冉、张静怡可以出现在计薪名单；
  employees 只读分支（注入假数据，不连库）。

不连生产库，不灌草案。个人级输出只落临时目录并在结束时删除。
"""

import csv
import importlib.util
import io
import json
import sys
import tempfile
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "docs" / "fetch-pipeline" / "samples" / "191" / "normalized_sample.csv"
EXAMPLE = Path(__file__).resolve().parent / "active_roster.example.json"
CONFIG = REPO / "docs" / "fetch-pipeline" / "pipeline_config.json"
HEADER = ["物品编号", "门店名称", "取货方式", "订单状态", "分润金额", "完成时间",
          "来源类型", "订单金额（不含券）", "门店验货人"]
NOW = datetime(2026, 10, 30, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_payroll():
    return load_mod("realtime_payroll", Path(__file__).resolve().parent / "realtime_payroll.py")


def load_apply():
    return load_mod("apply_realtime_payroll_draft", REPO / "scripts" / "apply_realtime_payroll_draft.py")


def write_orders(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)


def person(payload, name):
    hits = [p for p in payload["people"] if p["person"] == name]
    assert len(hits) == 1, (name, [p["person"] for p in payload["people"]])
    return hits[0]


def dec(value):
    return Decimal(str(value))


def assert_gross(row):
    expect = (dec(row["base_salary"]) + dec(row["door_commission"]) + dec(row["online_commission"])
              + dec(row["store_commission"]) + dec(row["accessories_commission"]))
    assert dec(row["gross"]) == expect, (row["person"], row["gross"], expect)


def dump_roster(path, active, excluded=None):
    path.write_text(json.dumps({
        "active": active,
        "excluded": excluded or [],
    }, ensure_ascii=False), encoding="utf-8")


def run(pay, argv, env=None, fetcher=None):
    if fetcher is None:
        def fetcher(_env):
            raise AssertionError("JSON 路径不应读取 employees")
    err = io.StringIO()
    with redirect_stderr(err):
        rc = pay.main(list(argv), env={} if env is None else env, employees_fetcher=fetcher)
    return rc, err.getvalue()


def base_orders():
    return [
        ["A1", "石河子万达店", "上门", "已完成", "200", "2026-10-01 10:00:00", "官网", "10000", "甲"],
        ["A2", "石河子万达店", "到店", "已完成", "10", "2026-10-02 10:00:00", "BD", "20000", "甲"],
        ["A3", "石河子万达店", "到店", "已完成", "0", "2026-10-03 10:00:00", "门店", "30000", "甲"],
        ["B1", "石河子友谊商厦店", "上门", "已完成", "80", "2026-10-04 10:00:00", "其他渠道", "10000", "乙"],
        ["B2", "石河子友谊商厦店", "到店", "已完成", "", "2026-10-05 10:00:00", "", "5000", "乙"],
        ["B3", "石河子友谊商厦店", "到店", "已完成", "40", "2026-10-06 10:00:00", "门店", "", "乙"],
    ]


def both_door_roster(path):
    dump_roster(path, [
        {"name": "甲", "role": "上门"},
        {"person_name": "乙", "role": "上门"},
    ])


def main():
    pay = load_payroll()
    cfg_before = CONFIG.read_bytes()
    assert not hasattr(pay, "ACCESSORIES_TIERS")
    assert pay.ACCESSORIES_PCT == Decimal("0.30")
    assert pay.ONLINE_MODE == "source_not_门店"
    assert pay.ONLINE_PCT == Decimal("0.01")
    assert pay.BASE_SALARY == Decimal("2000")
    assert pay.BASE_BREAKDOWN == {
        "basic": Decimal("1000"),
        "attendance": Decimal("500"),
        "new_customer": Decimal("500"),
    }
    assert pay.DOOR_PROFIT_PCT == Decimal("0.50")
    assert pay.STORE_BASE_FIELD == "订单金额（不含券）"
    assert pay.STORE_TIERS == [
        (Decimal("60000"), Decimal("0.04")),
        (Decimal("30000"), Decimal("0.03")),
    ]
    assert pay.EFFECTIVE_FROM == "2026-10"
    assert pay.HARD_EXCLUDED == ("吕文飞", "杨燕辉")
    assert "张浩冉" not in pay.HARD_EXCLUDED
    assert "张静怡" not in pay.HARD_EXCLUDED
    for src, expect in (
        ("官网", True),
        ("BD", True),
        ("其他渠道", True),
        ("", True),
        ("  ", True),
        ("门店", False),
        (" 门店 ", False),
    ):
        got = pay.is_online_source(src)
        assert got is expect, (src, got, expect)
    locked = pay.ONLINE_MODE
    try:
        pay.ONLINE_MODE = "source_官网"
        try:
            pay.is_online_source("官网")
        except RuntimeError as exc:
            assert "source_官网" in str(exc)
        else:
            raise SystemExit("旧口径 source_官网 不应再被静默接受")
    finally:
        pay.ONLINE_MODE = locked

    req = pay.build_employees_request("https://example.supabase.co", "test-secret")
    parsed = urllib.parse.urlparse(req.full_url)
    assert parsed.path == "/rest/v1/employees"
    assert req.get_method() == "GET"
    assert req.data is None
    qs = urllib.parse.parse_qs(parsed.query)
    assert qs["select"] == [pay.EMPLOYEES_SELECT]
    assert qs["employment_status"] == ["eq.在职"]
    assert "test-secret" not in (parsed.query or "")
    try:
        pay.build_employees_request("http://example.supabase.co", "test-secret")
    except pay.DraftRejected as exc:
        assert exc.code == pay.EXIT_ERROR
    else:
        raise SystemExit("非 https 的 employees 读取应被拒绝")
    try:
        pay.default_employees_fetcher({})
    except pay.DraftRejected as exc:
        assert "SUPABASE" in str(exc)
    else:
        raise SystemExit("没有凭据时不应连接 employees")

    with SAMPLE.open(encoding="utf-8") as fh:
        sample_header = next(csv.reader(fh))
    assert "来源类型" not in sample_header, sample_header
    assert "source_type" in sample_header

    with tempfile.TemporaryDirectory(prefix="payroll_smoke_") as tmp:
        tmp = Path(tmp)
        sample_out = tmp / "sample_rejected"
        try:
            pay.main(["--input", str(SAMPLE), "--month", "2026-10",
                      "--out-dir", str(sample_out)], env={})
        except Exception as exc:
            assert "关键列缺失" in str(exc), exc
        else:
            raise SystemExit("脱敏样本不应被当成 orders37 工资输入")

        early, _err = run(pay, ["--input", str(SAMPLE), "--month", "2026-09",
                                 "--out-dir", str(tmp / "early")])
        assert early == 2, early

        rows = base_orders()
        src = tmp / "orders.csv"
        write_orders(src, rows)
        roster = tmp / "roster.json"
        both_door_roster(roster)
        out = tmp / "out"
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--source-label", "smoke", "--active-roster", str(roster),
                            "--out-dir", str(out)])
        assert rc == 0, err
        payload = json.loads((out / "payroll_2026-10.json").read_text(encoding="utf-8"))
        text = "\n".join(payload["meta"]["assumptions"])
        assert "已拍板" in text
        assert "假设·待老板确认" not in text
        assert "缺配件源" in text
        assert payload["meta"]["online_mode"] == "source_not_门店"
        assert payload["meta"]["caliber"]["online_mode"] == "source_not_门店+store_role_door_as_online"
        assert payload["meta"]["caliber"]["door_profit_pct"] == "50|role=上门_only"
        assert payload["meta"]["caliber"]["accessories_pct"] == "30|source=none"
        assert payload["meta"]["caliber"]["roster"] == "json"
        assert payload["meta"]["accessories"]["pct"] == 30
        assert payload["meta"]["effective_from"] == "2026-10"

        jia = person(payload, "甲")
        yi = person(payload, "乙")
        assert jia["role"] == "上门" and yi["role"] == "上门"
        assert jia["online_orders"] == 2, jia
        assert jia["online_amt"] == 30000.0, jia
        assert jia["online_commission"] == 300.0, jia
        assert yi["online_orders"] == 2, yi
        assert yi["online_amt"] == 15000.0, yi
        assert yi["online_commission"] == 150.0, yi
        assert jia["door_commission"] == 100.0, jia
        assert yi["door_commission"] == 40.0, yi
        assert jia["base_salary"] == 2000.0
        assert yi["base_salary"] == 2000.0
        assert jia["base_breakdown"] == {"basic": 1000.0, "attendance": 500.0, "new_customer": 500.0}
        assert "不做1–9月个人例外" in jia["base_salary_source"]
        assert jia["accessories_commission"] == 0.0
        assert yi["accessories_commission"] == 0.0
        assert jia["store_commission"] == 2400.0, jia
        assert yi["store_commission"] == 0.0, yi
        stores = payload["meta"]["store_tier_reference"]
        assert stores["石河子万达店"]["tier_rate_by_base"] == 0.04
        assert stores["石河子友谊商厦店"]["tier_rate_by_base"] == 0.0
        assert payload["totals"]["online_commission_total"] == 450.0
        assert payload["totals"]["door_commission_total"] == 140.0
        assert payload["totals"]["base_salary_total"] == 4000.0
        assert payload["totals"]["store_commission_total"] == 2400.0
        assert payload["totals"]["accessories_commission_total"] == 0.0
        assert payload["totals"]["gross_total"] == 6990.0
        assert_gross(jia)
        assert_gross(yi)
        csv_head = (out / "payroll_2026-10.csv").read_text(encoding="utf-8").splitlines()[0]
        assert "线上提成(1%)" in csv_head
        assert "上门提成(50%·仅上门角色)" in csv_head
        assert "配件提成(实付×30%)" in csv_head
        assert "官网来源" not in csv_head

        missing_acc = tmp / "no-such-accessories.csv"
        out_missing = tmp / "missing_acc"
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--active-roster", str(roster),
                            "--accessories-input", str(missing_acc),
                            "--out-dir", str(out_missing)])
        assert rc == 0, err
        missing_payload = json.loads((out_missing / "payroll_2026-10.json").read_text(encoding="utf-8"))
        assert missing_payload["meta"]["accessories"]["source"] == "none"
        assert "不存在" in missing_payload["meta"]["accessories"]["note"]
        assert person(missing_payload, "甲")["accessories_commission"] == 0.0

        roster_one = tmp / "roster_one.json"
        dump_roster(roster_one, [{"name": "甲", "role": "上门"}],
                    [{"person": "乙", "reason": "冒烟排除"}])
        out_r = tmp / "roster_out"
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--active-roster", str(roster_one), "--out-dir", str(out_r)])
        assert rc == 0, err
        filtered = json.loads((out_r / "payroll_2026-10.json").read_text(encoding="utf-8"))
        assert [p["person"] for p in filtered["people"]] == ["甲"]
        assert filtered["totals"]["excluded_orders"] == 3
        assert filtered["meta"]["active_roster"]["excluded_people"] == 1
        assert person(filtered, "甲")["online_commission"] == 300.0
        assert person(filtered, "甲")["door_commission"] == 100.0

        exc_path = tmp / "exceptions.json"
        exc_path.write_text(json.dumps({"甲": 1500}, ensure_ascii=False), encoding="utf-8")
        out_e = tmp / "exc_out"
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--active-roster", str(roster),
                            "--base-exceptions", str(exc_path), "--out-dir", str(out_e)])
        assert rc == 0, err
        excused = json.loads((out_e / "payroll_2026-10.json").read_text(encoding="utf-8"))
        assert person(excused, "甲")["base_salary"] == 1500.0
        assert person(excused, "甲")["base_breakdown"]["basic"] == 1500.0
        assert person(excused, "甲")["base_breakdown"]["attendance"] == 0.0
        assert person(excused, "乙")["base_salary"] == 2000.0
        assert any("非现行路径" in a for a in excused["meta"]["assumptions"])

        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--out-dir", str(tmp / "no_roster")])
        assert rc == pay.EXIT_ROSTER, (rc, err)
        assert "拒绝出草案" in err and "role" in err
        assert not (tmp / "no_roster").exists()

        names_only = tmp / "names_only.json"
        names_only.write_text(json.dumps({"active": ["甲"]}, ensure_ascii=False), encoding="utf-8")
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--active-roster", str(names_only),
                            "--out-dir", str(tmp / "names_only_out")])
        assert rc == pay.EXIT_ROSTER, (rc, err)
        assert "仅姓名" in err
        assert not (tmp / "names_only_out").exists()

        no_role = tmp / "no_role.json"
        dump_roster(no_role, [{"name": "甲"}])
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--active-roster", str(no_role),
                            "--out-dir", str(tmp / "no_role_out")])
        assert rc == pay.EXIT_ROSTER, (rc, err)
        assert "缺少 role" in err
        assert not (tmp / "no_role_out").exists()

        blocked = tmp / "blocked.json"
        dump_roster(blocked, [{"name": "吕文飞", "role": "门店"}, {"name": "甲", "role": "上门"}])
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--active-roster", str(blocked),
                            "--out-dir", str(tmp / "blocked_out")])
        assert rc == pay.EXIT_HARD_EXCLUDED, (rc, err)
        assert "吕文飞" in err
        assert not (tmp / "blocked_out").exists()

        role_rows = [
            ["C1", "石河子万达店", "上门", "已完成", "1000", "2026-10-01 10:00:00", "门店", "30000", "丙"],
            ["C2", "石河子万达店", "到店", "已完成", "0", "2026-10-02 10:00:00", "官网", "30000", "戊"],
            ["C3", "石河子万达店", "上门", "已完成", "400", "2026-10-03 10:00:00", "官网", "5000", "庚"],
            ["D1", "石河子友谊商厦店", "上门", "已完成", "200", "2026-10-04 10:00:00", "官网", "8000", "己"],
            ["D2", "石河子友谊商厦店", "到店", "已完成", "50", "2026-10-05 10:00:00", "门店", "1000", "己"],
        ]
        role_src = tmp / "roles.csv"
        write_orders(role_src, role_rows)
        role_roster = tmp / "roles.json"
        dump_roster(role_roster, [
            {"name": "丙", "role": "门店"},
            {"name": "戊", "role": "门店"},
            {"name": "庚", "role": "门店"},
            {"name": "己", "role": "上门"},
        ])
        out_role = tmp / "roles_out"
        rc, err = run(pay, ["--input", str(role_src), "--month", "2026-10",
                            "--active-roster", str(role_roster), "--out-dir", str(out_role)])
        assert rc == 0, err
        roles = json.loads((out_role / "payroll_2026-10.json").read_text(encoding="utf-8"))
        bing = person(roles, "丙")
        wu = person(roles, "戊")
        geng = person(roles, "庚")
        ji = person(roles, "己")
        assert bing["role"] == "门店"
        assert bing["online_amt"] == 30000.0 and bing["online_commission"] == 300.0
        assert bing["door_commission"] == 0.0 and bing["door_orders"] == 1
        assert bing["store_commission"] == 0.0 and bing["store_base_amt"] == 0.0
        assert wu["online_commission"] == 300.0
        assert wu["store_base_amt"] == 30000.0 and wu["store_commission"] == 900.0
        assert geng["online_amt"] == 5000.0 and geng["online_commission"] == 50.0
        assert geng["door_commission"] == 0.0 and geng["store_commission"] == 0.0
        assert ji["role"] == "上门"
        assert ji["door_commission"] == 100.0
        assert ji["online_commission"] == 80.0
        assert ji["store_base_amt"] == 9000.0 and ji["store_commission"] == 0.0
        ref = roles["meta"]["store_tier_reference"]
        assert ref["石河子万达店"]["base_amt_excl_coupon"] == 30000.0
        assert ref["石河子万达店"]["tier_rate_by_base"] == 0.03
        assert ref["石河子万达店"]["store_role_door_excluded_amt"] == 35000.0
        assert ref["石河子友谊商厦店"]["base_amt_excl_coupon"] == 9000.0
        assert ref["石河子友谊商厦店"]["tier_rate_by_base"] == 0.0
        for row in roles["people"]:
            assert_gross(row)
        assert roles["totals"]["accessories_commission_total"] == 0.0

        acc = tmp / "accessories.csv"
        acc.write_text(
            "归属人,实付金额,完成时间\n"
            "己,1000,2026-10-01\n"
            "己,500,2026-10-02\n"
            "路人,200,2026-10-03\n"
            "己,999,2026-09-01\n",
            encoding="utf-8")
        out_acc = tmp / "acc_out"
        rc, err = run(pay, ["--input", str(role_src), "--month", "2026-10",
                            "--active-roster", str(role_roster),
                            "--accessories-input", str(acc), "--out-dir", str(out_acc)])
        assert rc == 0, err
        with_acc = json.loads((out_acc / "payroll_2026-10.json").read_text(encoding="utf-8"))
        ji_acc = person(with_acc, "己")
        assert ji_acc["accessories_paid"] == 1500.0
        assert ji_acc["accessories_commission"] == 450.0
        assert ji_acc["gross"] == 2630.0
        assert_gross(ji_acc)
        assert all(p["person"] != "路人" for p in with_acc["people"])
        assert with_acc["meta"]["accessories"]["unmatched_people"] == 1
        assert with_acc["meta"]["accessories"]["rows_in_month"] == 3
        assert with_acc["totals"]["accessories_commission_total"] == 450.0
        assert "30|source=" in with_acc["meta"]["caliber"]["accessories_pct"]

        fake_191 = tmp / "fake_191.csv"
        write_orders(fake_191, role_rows[:1])
        rc, err = run(pay, ["--input", str(role_src), "--month", "2026-10",
                            "--active-roster", str(role_roster),
                            "--accessories-input", str(fake_191),
                            "--out-dir", str(tmp / "fake_191_out")])
        assert rc == pay.EXIT_ERROR, (rc, err)
        assert "冒充" in err
        assert not (tmp / "fake_191_out").exists()

        custom = tmp / "custom.json"
        dump_roster(custom, [
            {"name": "丙", "role": "门店"},
            {"name": "戊", "role": "门店"},
            {"name": "庚", "role": "门店"},
            {"name": "己", "role": "上门", "base_salary": 2500},
        ])
        out_custom = tmp / "custom_out"
        rc, err = run(pay, ["--input", str(role_src), "--month", "2026-10",
                            "--active-roster", str(custom), "--out-dir", str(out_custom)])
        assert rc == 0, err
        custom_payload = json.loads((out_custom / "payroll_2026-10.json").read_text(encoding="utf-8"))
        ji_custom = person(custom_payload, "己")
        assert ji_custom["base_salary"] == 2500.0
        assert ji_custom["base_breakdown"] == {"basic": 2500.0, "attendance": 0.0, "new_customer": 0.0}
        assert "自定义" in ji_custom["base_salary_source"]
        assert ji_custom["gross"] == 2680.0
        assert_gross(ji_custom)
        zero = {"name": "闲", "role": "门店", "base_salary": 2300}
        dump_roster(tmp / "with_idle.json", [
            {"name": "丙", "role": "门店"},
            {"name": "戊", "role": "门店"},
            {"name": "庚", "role": "门店"},
            {"name": "己", "role": "上门"},
            zero,
        ])
        out_idle = tmp / "idle_out"
        rc, err = run(pay, ["--input", str(role_src), "--month", "2026-10",
                            "--active-roster", str(tmp / "with_idle.json"),
                            "--out-dir", str(out_idle)])
        assert rc == 0, err
        idle_payload = json.loads((out_idle / "payroll_2026-10.json").read_text(encoding="utf-8"))
        idle = person(idle_payload, "闲")
        assert idle["orders"] == 0 and idle["base_salary"] == 2300.0 and idle["gross"] == 2300.0
        assert idle["base_breakdown"]["basic"] == 2300.0

        calls = {"n": 0}

        def employees(_env):
            calls["n"] += 1
            return [
                {"person_name": "甲", "work_role": "上门", "base_salary": "1800",
                 "employment_status": "在职"},
                {"person_name": "乙", "work_role": "门店", "base_salary": None,
                 "employment_status": "在职"},
                {"person_name": "离职丙", "work_role": None, "base_salary": 2000,
                 "employment_status": "离职"},
            ]

        out_emp = tmp / "emp_out"
        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--roster-from", "employees", "--out-dir", str(out_emp)],
                      fetcher=employees)
        assert rc == 0, err
        assert calls["n"] == 1
        emp = json.loads((out_emp / "payroll_2026-10.json").read_text(encoding="utf-8"))
        assert emp["meta"]["caliber"]["roster"] == "employees"
        emp_jia = person(emp, "甲")
        emp_yi = person(emp, "乙")
        assert emp_jia["role"] == "上门" and emp_jia["base_salary"] == 1800.0
        assert emp_jia["base_breakdown"]["basic"] == 1800.0
        assert emp_jia["door_commission"] == 100.0
        assert emp_jia["online_commission"] == 300.0
        assert emp_jia["store_commission"] == 2400.0
        assert emp_yi["role"] == "门店" and emp_yi["base_salary"] == 2000.0
        assert emp_yi["base_breakdown"] == {"basic": 1000.0, "attendance": 500.0, "new_customer": 500.0}
        assert emp_yi["door_commission"] == 0.0
        assert emp_yi["online_commission"] == 150.0
        assert emp_yi["store_commission"] == 0.0
        assert emp["meta"]["store_tier_reference"]["石河子友谊商厦店"]["store_role_door_excluded_amt"] == 10000.0
        assert all(p["person"] != "离职丙" for p in emp["people"])
        assert_gross(emp_jia)
        assert_gross(emp_yi)

        def missing_role(_env):
            return [{"person_name": "甲", "work_role": "", "base_salary": 2000,
                     "employment_status": "在职"}]

        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--out-dir", str(tmp / "emp_bad")],
                      env={"ROSTER_SOURCE": "employees"}, fetcher=missing_role)
        assert rc == pay.EXIT_ROSTER, (rc, err)
        assert "work_role" in err
        assert not (tmp / "emp_bad").exists()

        def conflict_fetcher(_env):
            raise AssertionError("来源冲突时不应读库")

        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--roster-from", "json", "--active-roster", str(roster),
                            "--out-dir", str(tmp / "conflict")],
                      env={"ROSTER_SOURCE": "employees"}, fetcher=conflict_fetcher)
        assert rc == pay.EXIT_ROSTER, (rc, err)
        assert "冲突" in err

        rc, err = run(pay, ["--input", str(src), "--month", "2026-10",
                            "--roster-from", "employees", "--active-roster", str(roster),
                            "--out-dir", str(tmp / "both")],
                      fetcher=conflict_fetcher)
        assert rc == pay.EXIT_ROSTER, (rc, err)

        example = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        by_role = {}
        for item in example["active"]:
            assert item["role"] in ("上门", "门店")
            assert item.get("base_salary", 2000) == 2000
            by_role.setdefault(item["role"], set()).add(item["name"])
            assert item["name"] not in pay.HARD_EXCLUDED
        assert by_role["上门"] == {"张浩冉", "张静怡"}
        assert by_role["门店"] == {"李冬浩", "刘市伟", "子拉兰", "王毅杰", "杜飞"}
        ex_src = tmp / "example_orders.csv"
        write_orders(ex_src, [
            ["H1", "石河子万达店", "上门", "已完成", "200", "2026-10-08 10:00:00", "官网", "1000", "张浩冉"],
            ["H2", "石河子万达店", "到店", "已完成", "10", "2026-10-08 11:00:00", "官网", "100", "吕文飞"],
        ])
        out_ex = tmp / "example_out"
        rc, err = run(pay, ["--input", str(ex_src), "--month", "2026-10",
                            "--active-roster", str(EXAMPLE), "--out-dir", str(out_ex)])
        assert rc == 0, err
        official = json.loads((out_ex / "payroll_2026-10.json").read_text(encoding="utf-8"))
        assert {p["person"] for p in official["people"]} == set().union(*by_role.values())
        hao = person(official, "张浩冉")
        jing = person(official, "张静怡")
        du = person(official, "杜飞")
        assert official["meta"]["store_tier_reference"]["石河子万达店"]["base_amt_excl_coupon"] == 1100.0
        assert hao["role"] == "上门" and hao["door_commission"] == 100.0 and hao["orders"] == 1
        assert jing["role"] == "上门" and jing["orders"] == 0 and jing["base_salary"] == 2000.0
        assert jing["base_breakdown"] == {"basic": 1000.0, "attendance": 500.0, "new_customer": 500.0}
        assert du["role"] == "门店" and du["orders"] == 0 and du["gross"] == 2000.0
        assert all(p["person"] not in pay.HARD_EXCLUDED for p in official["people"])
        excluded_names = {e["person"] for e in official["meta"]["active_roster"]["excluded"]}
        assert "吕文飞" in excluded_names
        assert_gross(hao)
        assert_gross(du)

        apply = load_apply()
        out_buf = io.StringIO()

        def forbid_write(_table, _rows):
            raise AssertionError("dry-run 不应写库")

        with redirect_stdout(out_buf):
            code = apply.main(
                ["--input", str(out_acc / "payroll_2026-10.json")],
                env={}, writer=forbid_write, now=NOW)
        assert code == 0, out_buf.getvalue()
        assert "未写库" in out_buf.getvalue()
        assert "dry-run" in out_buf.getvalue()

        grabbed = {}

        def grab(table, batch):
            grabbed["table"] = table
            grabbed["rows"] = batch
            return len(batch)

        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            code = apply.main(
                ["--input", str(out_acc / "payroll_2026-10.json"), "--apply"],
                env={apply.ENV_GATE: "true"}, writer=grab, now=NOW)
        assert code == 0, out_buf.getvalue()
        assert grabbed["table"] == "salary_realtime_draft"
        ji_row = next(r for r in grabbed["rows"] if r["person_name"] == "己")
        assert ji_row["accessories_amount"] == "450.00"
        assert ji_row["door_amount"] == "100.00"
        assert ji_row["gross_amount"] == "2630.00"
        assert "role" not in ji_row
        assert set(ji_row) == set(apply.UPSERT_COLUMNS)
        assert "30|source=" in ji_row["caliber"]

    assert CONFIG.read_bytes() == cfg_before
    print("smoke_online_mode PASS：A1 门店上门走线上且不进阶梯，上门角色上门 50%，"
          "缺配件源为 0，实付×30%，缺 role 拒绝，自定义底薪，"
          "硬排除仅吕文飞/杨燕辉，employees 只读分支与 dry-run 未写生产库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
