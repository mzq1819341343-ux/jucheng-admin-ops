#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""线上判定冒烟：ONLINE_MODE=source_not_门店，不再只认官网。

仓内脱敏样本 docs/fetch-pipeline/samples/191/normalized_sample.csv
没有业务列「来源类型」（其 source_type 只是管道格式标记 orders37），
不能当工资输入，也不能复算线上池。本脚本：

1. 断言该样本缺「来源类型」，且 realtime_payroll 拒绝把它当 orders37。
2. 用临时 orders37 形状 CSV（官网 / BD / 其他 / 空来源 / 门店）跑草案，
   断言非门店来源都进线上、来源类型=门店不进。
3. 顺带锁底薪 2000、门店阶梯、上门 50%、配件 0、EFFECTIVE_FROM、硬排除。

不连库、不改样本、个人级输出只落临时目录并在结束时删除。
"""

import csv
import importlib.util
import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "docs" / "fetch-pipeline" / "samples" / "191" / "normalized_sample.csv"
HEADER = ["物品编号", "门店名称", "取货方式", "订单状态", "分润金额", "完成时间",
          "来源类型", "订单金额（不含券）", "门店验货人"]


def load_payroll():
    path = Path(__file__).resolve().parent / "realtime_payroll.py"
    spec = importlib.util.spec_from_file_location("realtime_payroll", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_orders(path, rows):
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)


def person(payload, name):
    hits = [p for p in payload["people"] if p["person"] == name]
    assert len(hits) == 1, payload["people"]
    return hits[0]


def main():
    pay = load_payroll()
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
    assert pay.HARD_EXCLUDED == ("张浩冉", "张静怡", "杨燕辉", "吕文飞")
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

    with SAMPLE.open(encoding="utf-8") as fh:
        sample_header = next(csv.reader(fh))
    assert "来源类型" not in sample_header, sample_header
    assert "source_type" in sample_header

    with tempfile.TemporaryDirectory(prefix="payroll_smoke_") as tmp:
        tmp = Path(tmp)
        sample_out = tmp / "sample_rejected"
        try:
            pay.main(["--input", str(SAMPLE), "--month", "2026-10",
                      "--out-dir", str(sample_out)])
        except Exception as exc:
            assert "关键列缺失" in str(exc), exc
        else:
            raise SystemExit("脱敏样本不应被当成 orders37 工资输入")

        early = pay.main(["--input", str(SAMPLE), "--month", "2026-09",
                          "--out-dir", str(tmp / "early")])
        assert early == 2, early

        rows = [
            ["A1", "石河子万达店", "上门", "已完成", "200", "2026-10-01 10:00:00", "官网", "10000", "甲"],
            ["A2", "石河子万达店", "到店", "已完成", "10", "2026-10-02 10:00:00", "BD", "20000", "甲"],
            ["A3", "石河子万达店", "到店", "已完成", "0", "2026-10-03 10:00:00", "门店", "30000", "甲"],
            ["B1", "石河子友谊商厦店", "上门", "已完成", "80", "2026-10-04 10:00:00", "其他渠道", "10000", "乙"],
            ["B2", "石河子友谊商厦店", "到店", "已完成", "", "2026-10-05 10:00:00", "", "5000", "乙"],
            ["B3", "石河子友谊商厦店", "到店", "已完成", "40", "2026-10-06 10:00:00", "门店", "", "乙"],
        ]
        src = tmp / "orders.csv"
        write_orders(src, rows)
        out = tmp / "out"
        rc = pay.main(["--input", str(src), "--month", "2026-10",
                       "--source-label", "smoke", "--out-dir", str(out)])
        assert rc == 0, rc
        payload = json.loads((out / "payroll_2026-10.json").read_text(encoding="utf-8"))
        text = "\n".join(payload["meta"]["assumptions"])
        assert "已拍板" in text
        assert "假设·待老板确认" not in text
        assert payload["meta"]["online_mode"] == "source_not_门店"
        assert payload["meta"]["effective_from"] == "2026-10"

        jia = person(payload, "甲")
        yi = person(payload, "乙")
        # 只认官网时甲的线上金额会是 10000、乙为 0。BD / 其他 / 空来源必须计入。
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
        # 万达不含券 60000 → 4%；友谊 15000 → 0%。门店来源的 30000 进阶梯、不进线上。
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
        csv_head = (out / "payroll_2026-10.csv").read_text(encoding="utf-8").splitlines()[0]
        assert "线上单量(非门店来源)" in csv_head
        assert "官网来源" not in csv_head

        roster = tmp / "roster.json"
        roster.write_text(json.dumps({
            "active": ["甲"],
            "excluded": [{"person": "乙", "reason": "冒烟排除"}],
        }, ensure_ascii=False), encoding="utf-8")
        out_r = tmp / "roster_out"
        rc = pay.main(["--input", str(src), "--month", "2026-10",
                       "--active-roster", str(roster), "--out-dir", str(out_r)])
        assert rc == 0, rc
        filtered = json.loads((out_r / "payroll_2026-10.json").read_text(encoding="utf-8"))
        assert [p["person"] for p in filtered["people"]] == ["甲"]
        assert filtered["totals"]["excluded_orders"] == 3
        assert filtered["meta"]["active_roster"]["excluded_people"] == 1
        assert person(filtered, "甲")["online_commission"] == 300.0

        exc_path = tmp / "exceptions.json"
        exc_path.write_text(json.dumps({"甲": 1500}, ensure_ascii=False), encoding="utf-8")
        out_e = tmp / "exc_out"
        rc = pay.main(["--input", str(src), "--month", "2026-10",
                       "--base-exceptions", str(exc_path), "--out-dir", str(out_e)])
        assert rc == 0, rc
        excused = json.loads((out_e / "payroll_2026-10.json").read_text(encoding="utf-8"))
        assert person(excused, "甲")["base_salary"] == 1500.0
        assert person(excused, "乙")["base_salary"] == 2000.0
        assert any("非现行路径" in a for a in excused["meta"]["assumptions"])

        leaked_rows = rows + [
            ["Z1", "石河子万达店", "到店", "已完成", "1", "2026-10-07 10:00:00", "官网", "100", "张浩冉"],
        ]
        leaked = tmp / "leaked.csv"
        write_orders(leaked, leaked_rows)
        rc = pay.main(["--input", str(leaked), "--month", "2026-10",
                       "--out-dir", str(tmp / "leaked_out")])
        assert rc == 3, rc
        assert not (tmp / "leaked_out").exists()

    print("smoke_online_mode PASS：非门店来源（官网/BD/其他/空）计入线上，来源类型=门店不计入；"
          "底薪 2000、门店阶梯、上门 50%、配件 0、EFFECTIVE_FROM、在职过滤与硬排除保持。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
