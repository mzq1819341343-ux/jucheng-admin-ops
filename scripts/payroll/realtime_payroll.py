#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指令192 · 实时工资草案：加盟商后台订单明细（orders37）+ salary_rules 口径
→ 按抓数月自动算出每人应发拆分（底薪分项 + 各提成）。无任何「跨月」交互依赖：
月份由 --month 指定（默认取输入文件完成时间的最大月份），打开即出当月草案。

规则来源：public.salary_rules「通用工资体系（老板口述版）」一行
（supabase/migration_004_contracts_rules.sql 种子的 commission_rules JSON），
本脚本常量与其逐项镜像，改规则须两处同步。
EFFECTIVE_FROM 对应 salary_rules.effective_from：老板 2026-10-28 拍板 = 2026-10；
库内落地材料 = supabase/migration_010_salary_effective_from.sql（老板在 SQL Editor
执行一次即可，本脚本不连库、不写库）。

用法：
  python3 scripts/payroll/realtime_payroll.py \
    --input ~/Desktop/05_Muse督工/fetch_samples_191/native_export_2026-10_oldMachineOrder.xlsx \
    --month 2026-10 --out-dir ~/Desktop/05_Muse督工/payroll_192/

输出（个人级，仅落 --out-dir 指定目录，禁止入仓/外传）：
  payroll_<month>.json / payroll_<month>.csv
stdout 只打印汇总锚点（总单量/分店单量/人数/各项合计），不打印个人清单。

计算口径与假设（与 docs/工资字段映射_192.md 一致，假设均标【假设·待老板确认】）：
  - 人员归属主字段 = 门店验货人。
  - 上门提成 = 个人「取货方式=上门」订单分润金额合计 × 50%（规则原文「卖出机子
    利润」即分润金额；分润为空的行按缺失不补值，该单不计入基数）。
  - 线上提成【假设】= 来源类型=「官网」的订单，其「订单金额（不含券）」合计 × 1%
    （191 样本中官网来源 78 单该列全非空，可识别可复算；对照口径 交易方式=
    「O2O订单」90 单的合计在映射表中并列，待老板选定后改常量 ONLINE_MODE）。
  - 门店提成【假设】业绩基数 = 「订单金额（不含券）」：先按门店汇总当月基数定档
    （≥60000→4%，≥30000→3%，未达 30000→0%，取最高达成档），个人提成 =
    Σ（个人在该店的基数 × 该店档位率）；分润基数的档位对照同步输出在 JSON
    的 store_tier_reference 段（本轮两基数档位不同，待老板确认基数）。
  - 配件提成 = 0 占位：191 订单明细无配件销售数据，未从 191 取得，不编造。
  - 底薪 = 2000（基础 1000 + 全勤 500 + 拉新 500）全员默认；仓内/历史材料未查到
    个人底薪例外配置，如老板给出例外表，用 --base-exceptions <json> 注入。
金额一律 Decimal；订单编号不参与计算；中文 UTF-8。
"""

import argparse
import csv
import importlib.util
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# ---- 与 salary_rules 对应（老板 2026-10-28 拍板，见 migration_010） ----
EFFECTIVE_FROM = "2026-10"

BASE_SALARY = Decimal("2000")
BASE_BREAKDOWN = {"basic": Decimal("1000"), "attendance": Decimal("500"),
                  "new_customer": Decimal("500")}
ONLINE_PCT = Decimal("0.01")          # 线上订单提成 1%
ONLINE_MODE = "source_官网"           # 【假设】线上单判定口径，见模块 docstring
STORE_TIERS = [(Decimal("60000"), Decimal("0.04")),
               (Decimal("30000"), Decimal("0.03"))]  # 取最高达成档，未达最低档 = 0
STORE_BASE_FIELD = "订单金额（不含券）"  # 【假设】门店提成绩效基数
ACCESSORIES_TIERS = [(Decimal("10000"), Decimal("0.40")),
                     (Decimal("6500"), Decimal("0.30")),
                     (None, Decimal("0.20"))]  # 本轮无数据源，占位 0
DOOR_PROFIT_PCT = Decimal("0.50")     # 上门提成 = 卖出利润（分润）× 50%

STORE_IDS = {"石河子万达店": "4594", "石河子友谊商厦店": "5511"}  # batch001 验收对应口径

CENT = Decimal("0.01")


def money(d):
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="实时工资草案（orders37 + salary_rules 口径）")
    ap.add_argument("--input", required=True, help="加盟商后台原生导出（xlsx/csv）")
    ap.add_argument("--month", default=None, help="计算月份 YYYY-MM；缺省取完成时间最大月份")
    ap.add_argument("--out-dir", required=True, help="个人级输出目录（仅本机，禁止入仓）")
    ap.add_argument("--source-label", default="orders37/191",
                    help="数据来源标注（写入草案 meta）")
    ap.add_argument("--base-exceptions", default=None,
                    help="可选：个人底薪例外 JSON {姓名: 底薪}，本轮未提供 = 全员默认")
    args = ap.parse_args(argv)

    if args.month and args.month < EFFECTIVE_FROM:
        print("警告：月份 %s 早于生效月 EFFECTIVE_FROM=%s，现行规则自 %s 起算，本草案不适用"
              % (args.month, EFFECTIVE_FROM, EFFECTIVE_FROM), file=sys.stderr)
        return 2

    af = load_auto_fetch()
    tables = af.load_tables(args.input)
    kind, sheet, _hi, header, raw_rows = af.detect_format(tables)
    rows = [r for r in raw_rows if any(str(c).strip() for c in r)]
    idx = {h: i for i, h in enumerate(header)}

    def g(r, col):
        return str(r[idx[col]]).strip() if col in idx else ""

    def num(r, col):
        return to_decimal(r[idx[col]]) if col in idx else None

    months = [month_of(g(r, "完成时间")) for r in rows]
    month = args.month or max(m for m in months if m)
    in_month = [r for r, m in zip(rows, months) if m == month]
    out_month = len(rows) - len(in_month)

    exceptions = {}
    if args.base_exceptions:
        exceptions = {k: Decimal(str(v)) for k, v in
                      json.loads(Path(args.base_exceptions).read_text(encoding="utf-8")).items()}

    # 门店基数合计 → 定档（默认基数 + 分润对照基数）
    store_base = defaultdict(Decimal)
    store_profit_base = defaultdict(Decimal)
    store_count = defaultdict(int)
    for r in in_month:
        s = g(r, "门店名称")
        store_count[s] += 1
        v = num(r, STORE_BASE_FIELD)
        if v is not None:
            store_base[s] += v
        p = num(r, "分润金额")
        if p is not None:
            store_profit_base[s] += p
    store_rate = {s: tier_rate(store_base[s]) for s in store_count}
    store_rate_ref = {s: tier_rate(store_profit_base[s]) for s in store_count}

    # 按人聚合（门店拆分单列，供门店提成按店档位率计算）
    agg = defaultdict(lambda: {
        "orders": 0, "base_amt": Decimal("0"), "base_amt_missing": 0,
        "profit": Decimal("0"), "profit_missing": 0,
        "door_orders": 0, "door_profit": Decimal("0"),
        "online_orders": 0, "online_amt": Decimal("0"),
        "stores": defaultdict(lambda: {"orders": 0, "base_amt": Decimal("0")}),
    })
    for r in in_month:
        person, store = g(r, "门店验货人"), g(r, "门店名称")
        a = agg[person]
        a["orders"] += 1
        a["stores"][store]["orders"] += 1
        v = num(r, STORE_BASE_FIELD)
        if v is None:
            a["base_amt_missing"] += 1
        else:
            a["base_amt"] += v
            a["stores"][store]["base_amt"] += v
        p = num(r, "分润金额")
        if p is None:
            a["profit_missing"] += 1
        else:
            a["profit"] += p
        if g(r, "取货方式") == "上门":
            a["door_orders"] += 1
            if p is not None:
                a["door_profit"] += p
        if g(r, "来源类型") == "官网":  # ONLINE_MODE = source_官网
            a["online_orders"] += 1
            if v is not None:
                a["online_amt"] += v

    people = []
    for person in sorted(agg, key=lambda p: -agg[p]["orders"]):
        a = agg[person]
        store_comm = sum((a["stores"][s]["base_amt"] * store_rate[s] for s in a["stores"]),
                         Decimal("0"))
        base = exceptions.get(person, BASE_SALARY)
        if person in exceptions:
            bd = {"basic": base, "attendance": Decimal("0"), "new_customer": Decimal("0")}
        else:
            bd = dict(BASE_BREAKDOWN)
        door_comm = money(a["door_profit"] * DOOR_PROFIT_PCT)
        online_comm = money(a["online_amt"] * ONLINE_PCT)
        store_comm_m = money(store_comm)
        row = {
            "person": person,
            "stores": {s: {"store_id": STORE_IDS.get(s, ""),
                           "orders": a["stores"][s]["orders"],
                           "base_amt": float(money(a["stores"][s]["base_amt"])),
                           "tier_rate": float(store_rate[s])}
                       for s in a["stores"]},
            "orders": a["orders"],
            "base_salary": float(money(base)),
            "base_breakdown": {k: float(money(v)) for k, v in bd.items()},
            "base_salary_source": "个人例外" if person in exceptions else "默认2000（未查到个人例外配置）",
            "door_orders": a["door_orders"],
            "door_profit": float(money(a["door_profit"])),
            "door_commission": float(door_comm),
            "online_orders": a["online_orders"],
            "online_amt": float(money(a["online_amt"])),
            "online_commission": float(online_comm),
            "store_base_amt": float(money(a["base_amt"])),
            "store_commission": float(store_comm_m),
            "accessories_commission": 0.0,
            "accessories_note": "占位0：191 订单明细无配件销售数据，未从191取得",
            "profit_total": float(money(a["profit"])),
            "profit_missing_rows": a["profit_missing"],
            "base_amt_missing_rows": a["base_amt_missing"],
        }
        row["gross"] = float(money(base + door_comm + online_comm + store_comm_m))
        people.append(row)

    total_orders = sum(p["orders"] for p in people)
    assert total_orders == len(in_month), \
        "单量锚点不符：个人合计 %d != 月内行数 %d" % (total_orders, len(in_month))

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
        "assumptions": [
            "线上提成口径【假设·待老板确认】：来源类型=官网 的订单金额（不含券）×1%",
            "门店提成绩效基数【假设·待老板确认】：订单金额（不含券），按门店合计定档，个人按所在店基数×该店档位率（未达3万档位率0）",
            "配件提成占位0：191 无配件销售数据，未从191取得",
            "底薪全员默认2000（基础1000+全勤500+拉新500）：未查到个人底薪例外配置",
            "分润金额为空的行按缺失不补值，不计入上门/分润基数（延续既有口径）",
        ],
        "store_tier_reference": {
            s: {"store_id": STORE_IDS.get(s, ""),
                "orders": store_count[s],
                "base_amt_excl_coupon": float(money(store_base[s])),
                "tier_rate_by_base": float(store_rate[s]),
                "profit_base": float(money(store_profit_base[s])),
                "tier_rate_by_profit": float(store_rate_ref[s])}
            for s in store_count},
    }
    totals = {
        "people": len(people),
        "orders": total_orders,
        "base_salary_total": float(money(sum(Decimal(str(p["base_salary"])) for p in people))),
        "door_commission_total": float(money(sum(Decimal(str(p["door_commission"])) for p in people))),
        "online_commission_total": float(money(sum(Decimal(str(p["online_commission"])) for p in people))),
        "store_commission_total": float(money(sum(Decimal(str(p["store_commission"])) for p in people))),
        "accessories_commission_total": 0.0,
        "gross_total": float(money(sum(Decimal(str(p["gross"])) for p in people))),
    }

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "totals": totals, "people": people}
    (out_dir / ("payroll_%s.json" % month)).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out_dir / ("payroll_%s.csv" % month), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["月份", "验货人", "单量", "底薪", "基础", "全勤", "拉新",
                    "上门单量", "上门分润", "上门提成(50%)", "线上单量(官网来源)",
                    "线上金额(不含券)", "线上提成(1%)", "门店提成基数(不含券)",
                    "门店提成", "配件提成(占位0)", "分润合计", "应发草案合计",
                    "分润缺失行", "不含券缺失行"])
        for p in people:
            w.writerow([month, p["person"], p["orders"], p["base_salary"],
                        p["base_breakdown"]["basic"], p["base_breakdown"]["attendance"],
                        p["base_breakdown"]["new_customer"], p["door_orders"], p["door_profit"],
                        p["door_commission"], p["online_orders"], p["online_amt"],
                        p["online_commission"], p["store_base_amt"], p["store_commission"],
                        p["accessories_commission"], p["profit_total"], p["gross"],
                        p["profit_missing_rows"], p["base_amt_missing_rows"]])

    print("输入 %d 行 → 月份 %s 内 %d 行（月外 %d 行）" % (len(rows), month, len(in_month), out_month))
    print("单量锚点 PASS：每人单量合计 %d == 月内行数 %d" % (total_orders, len(in_month)))
    for s in sorted(store_count):
        print("  %s(%s)：%d 单，基数(不含券) %s → 档位 %.0f%%（分润基数对照 %s → %.0f%%）"
              % (s, STORE_IDS.get(s, ""), store_count[s], money(store_base[s]),
                 store_rate[s] * 100, money(store_profit_base[s]), store_rate_ref[s] * 100))
    print("人数 %d；应发草案合计 %s = 底薪 %s + 上门提成 %s + 线上提成 %s + 门店提成 %s + 配件提成 0（占位）"
          % (totals["people"], totals["gross_total"], totals["base_salary_total"],
             totals["door_commission_total"], totals["online_commission_total"],
             totals["store_commission_total"]))
    print("个人级输出（仅本机）：%s / .csv" % (out_dir / ("payroll_%s.json" % month)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
