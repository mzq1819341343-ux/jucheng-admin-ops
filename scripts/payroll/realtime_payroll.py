#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""指令192 · 实时工资草案：加盟商后台订单明细（orders37）+ salary_rules 口径
→ 按抓数月自动算出每人应发拆分（底薪分项 + 各提成）。无任何「跨月」交互依赖：
月份由 --month 指定（默认取输入文件完成时间的最大月份），打开即出当月草案。

规则来源：public.salary_rules「通用工资体系（老板口述版）」一行
（supabase/migration_004_contracts_rules.sql 种子的 commission_rules JSON），
本脚本常量与其逐项镜像，改规则须两处同步：
  base_salary 2000 = basic 1000 + attendance 500 + new_customer 500
  online_order_pct 1 → ONLINE_PCT（百分数 1 = 0.01）
  store_tiers ≥30000→3% / ≥60000→4%（阶梯取最高达成档）
  door_profit_pct 50 → DOOR_PROFIT_PCT
  accessories_tiers 保留；无配件数据时提成占位 0
commission_rules 只有 online_order_pct，没有「线上订单 / 门店业绩」的列定义。
线上判定不改种子、不跑 migration，锁在本文件 ONLINE_MODE（老板 2026-10-29 拍板）。
EFFECTIVE_FROM 对应 salary_rules.effective_from：老板 2026-10-28 拍板 = 2026-10；
库内落地材料 = supabase/migration_010_salary_effective_from.sql（老板在 SQL Editor
执行一次即可，本脚本不连库、不写库）。
乱码：库侧无证据，本脚本不改编码处理（读入仍走 auto_fetch 既有识别，不新增转码）。

用法：
  python3 scripts/payroll/realtime_payroll.py \
    --input ~/Desktop/05_Muse督工/fetch_samples_191/native_export_2026-10_oldMachineOrder.xlsx \
    --month 2026-10 --out-dir ~/Desktop/05_Muse督工/payroll_192/
  # 指令193 在职名单过滤（老板 2026-10-28 20:05 拍板：只算在职，张浩冉与
  # 已离职者不计薪，其订单进排除清单）：
  python3 scripts/payroll/realtime_payroll.py \
    --input ~/Desktop/05_Muse督工/fetch_samples_191/native_export_2026-10_oldMachineOrder.xlsx \
    --month 2026-10 \
    --active-roster ~/Desktop/05_Muse督工/payroll_193/active_roster_2026-10.json \
    --out-dir ~/Desktop/05_Muse督工/payroll_193/
  不带 --active-roster 时不过滤人员，但硬断言仍然生效：
  只要计薪结果含张浩冉/张静怡/杨燕辉/吕文飞任一人即报错中止（退出码 3）——
  193 之后不带名单的「全员应发草案」不再可出，防误用。
  线上口径已是 source_not_门店，与 192 当时「仅来源类型=官网」不同。

输出（个人级，仅落 --out-dir 指定目录，禁止入仓/外传）：
  payroll_<month>.json / payroll_<month>.csv
  带 --active-roster 时另出 excluded_roster.json（被排除人员姓名/单量/金额/原因），
  并在 payroll JSON 的 meta.active_roster 与 totals 中同步汇总。
stdout 只打印汇总锚点（总单量/分店单量/人数/各项合计），不打印个人清单。

计算口径（与 docs/工资字段映射_192.md 一致；H1/H2/底薪均为【已拍板】）：
  - 人员归属主字段 = 门店验货人。
  - 上门提成 = 个人「取货方式=上门」订单分润金额合计 × 50%（规则原文「卖出机子
    利润」即分润金额；分润为空的行按缺失不补值，该单不计入基数）。
  - 线上提成【已拍板】= 来源类型 ≠「门店」的订单，其「订单金额（不含券）」合计 × 1%
    （BD、官网及其他非门店来源都算线上；空来源也 ≠ 门店，计入线上）。
    门店业绩的仓内字段定义 = orders37「来源类型」等于「门店」。
    「是否门店订单」/「交易方式=门店订单」是另一刀，映射表标为不用/对照，不采用。
    旧口径 ONLINE_MODE=source_官网（只认来源类型=官网）已废止，写回该值会直接报错。
  - 门店提成【已拍板】业绩基数 = 「订单金额（不含券）」：先按门店汇总当月基数定档
    （≥60000→4%，≥30000→3%，未达 30000→0%，取最高达成档），个人提成 =
    Σ（个人在该店的基数 × 该店档位率）。基数范围含全部来源（含来源类型=门店），
    与线上提成不是互斥切分：非门店来源的不含券同时进入线上 1% 与门店阶梯基数。
    分润基数只作对照，写在 JSON 的 store_tier_reference，不参与定档。
  - 配件提成 = 0 占位：191 订单明细无配件销售数据，未从 191 取得，不编造。
  - 底薪【已拍板】= 2000（基础 1000 + 全勤 500 + 拉新 500）全员统一。
    不做 1–9 月个人例外。--base-exceptions 保留为非现行路径，缺省不读、不注入。
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
ONLINE_PCT = Decimal("0.01")          # 线上订单提成 1%（salary_rules.online_order_pct）
# 【已拍板】除来源类型=门店外一律算线上。旧值 source_官网 已废止，见 is_online_source。
ONLINE_MODE = "source_not_门店"
STORE_TIERS = [(Decimal("60000"), Decimal("0.04")),
               (Decimal("30000"), Decimal("0.03"))]  # 【已拍板】取最高达成档，未达最低档 = 0
STORE_BASE_FIELD = "订单金额（不含券）"  # 【已拍板】门店提成绩效基数
ACCESSORIES_TIERS = [(Decimal("10000"), Decimal("0.40")),
                     (Decimal("6500"), Decimal("0.30")),
                     (None, Decimal("0.20"))]  # 本轮无数据源，占位 0
DOOR_PROFIT_PCT = Decimal("0.50")     # 上门提成 = 卖出利润（分润）× 50%

STORE_IDS = {"石河子万达店": "4594", "石河子友谊商厦店": "5511"}  # batch001 验收对应口径

# 指令193 老板拍板硬排除（断言用）：这四人绝不允许出现在计薪 people 中
HARD_EXCLUDED = ("张浩冉", "张静怡", "杨燕辉", "吕文飞")

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


def is_online_source(source_type):
    """线上单判定。现行唯一口径：来源类型去掉空白后不等于「门店」。

    空来源、BD、官网及其他任何非门店来源都算线上。ONLINE_MODE 锁死为
    source_not_门店；旧值 source_官网 不再保留分支，避免静默退回「只认官网」。
    """
    if ONLINE_MODE != "source_not_门店":
        raise RuntimeError(
            "ONLINE_MODE=%r 未锁定。现行唯一合法值是 source_not_门店"
            "（旧值 source_官网 已废止，不得作为可切换口径）" % (ONLINE_MODE,))
    return str(source_type).strip() != "门店"


def main(argv=None):
    ap = argparse.ArgumentParser(description="实时工资草案（orders37 + salary_rules 口径）")
    ap.add_argument("--input", required=True, help="加盟商后台原生导出（xlsx/csv）")
    ap.add_argument("--month", default=None, help="计算月份 YYYY-MM；缺省取完成时间最大月份")
    ap.add_argument("--out-dir", required=True, help="个人级输出目录（仅本机，禁止入仓）")
    ap.add_argument("--source-label", default="orders37/191",
                    help="数据来源标注（写入草案 meta）")
    ap.add_argument("--base-exceptions", default=None,
                    help="非现行路径：老板定案底薪全员统一 2000，不做 1–9 月个人例外。"
                         "本参数仅保留供历史对照，缺省不读取、不改变现行默认")
    ap.add_argument("--active-roster", default=None,
                    help="可选（指令193）：在职名单 JSON {active:[姓名], excluded:[{person, reason}]}；"
                         "非名单人员不计薪，订单汇入 excluded 输出；缺省 = 不过滤（计算口径同 192，"
                         "但计薪含硬排除四人时断言中止）")
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
        print("警告：本跑注入了 --base-exceptions（非现行路径）。"
              "老板定案底薪全员统一 2000，不做 1–9 月个人例外；"
              "本文件不得当作现行应发。", file=sys.stderr)
        exceptions = {k: Decimal(str(v)) for k, v in
                      json.loads(Path(args.base_exceptions).read_text(encoding="utf-8")).items()}

    # 指令193：在职名单（计薪白名单 + 排除原因表）；缺省不过滤
    active_set = None
    roster_reasons = {}
    if args.active_roster:
        roster = json.loads(Path(args.active_roster).read_text(encoding="utf-8"))
        active_set = set(roster.get("active", []))
        roster_reasons = {e["person"]: e.get("reason", "不在在职名单")
                          for e in roster.get("excluded", [])}

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

    # 指令193：被排除人员订单单独聚合（不计薪，仅留痕进排除清单）
    excluded_agg = {}
    if active_set is not None:
        tmp = defaultdict(lambda: {"orders": 0, "base_amt": Decimal("0"),
                                   "profit": Decimal("0")})
        for r in in_month:
            person = g(r, "门店验货人")
            if person in active_set:
                continue
            e = tmp[person]
            e["orders"] += 1
            v0 = num(r, STORE_BASE_FIELD)
            if v0 is not None:
                e["base_amt"] += v0
            p0 = num(r, "分润金额")
            if p0 is not None:
                e["profit"] += p0
        excluded_agg = {p: {"person": p,
                            "orders": tmp[p]["orders"],
                            "base_amt_excl_coupon": float(money(tmp[p]["base_amt"])),
                            "profit_total": float(money(tmp[p]["profit"])),
                            "reason": roster_reasons.get(p, "不在在职名单")}
                        for p in sorted(tmp, key=lambda x: -tmp[x]["orders"])}

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
        if active_set is not None and person not in active_set:
            continue  # 指令193：非在职名单不计薪（已在 excluded_agg 留痕）
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
        if is_online_source(g(r, "来源类型")):  # ONLINE_MODE = source_not_门店
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
            "base_salary_source": (
                "个人例外（非现行路径，--base-exceptions 注入；老板定案不做例外）"
                if person in exceptions else
                "统一2000（已拍板：基础1000+全勤500+拉新500，不做1–9月个人例外）"),
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
    excluded_orders = sum(e["orders"] for e in excluded_agg.values())
    assert total_orders + excluded_orders == len(in_month), \
        "单量锚点不符：计薪 %d + 排除 %d != 月内行数 %d" % (total_orders, excluded_orders, len(in_month))
    # 指令193 硬断言：老板点名四人绝不允许进计薪（出现即报错退出，不出草案）
    leaked = [p["person"] for p in people if p["person"] in HARD_EXCLUDED]
    if leaked:
        print("错误（指令193）：计薪名单中出现硬排除人员 %s，已中止" % "、".join(leaked),
              file=sys.stderr)
        return 3

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
        "assumptions": [
            "线上提成口径【已拍板】：来源类型≠门店（BD/官网及其他非门店来源，含空来源）的订单金额（不含券）×1%；旧口径「仅来源类型=官网」已废止",
            "门店提成绩效基数【已拍板】：订单金额（不含券），按门店合计定档（≥60000→4%，≥30000→3%，未达→0%），个人按所在店基数×该店档位率",
            "配件提成占位0：191 无配件销售数据，未从191取得",
            "底薪全员统一2000（基础1000+全勤500+拉新500）【已拍板】：不做1–9月个人例外；--base-exceptions 非现行路径，缺省不注入",
            "分润金额为空的行按缺失不补值，不计入上门/分润基数（延续既有口径）",
            "上门提成【维持】：取货方式=上门的分润金额×50%",
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
    if exceptions:
        meta["assumptions"].append(
            "本跑注入了 --base-exceptions（非现行路径）：老板定案不做 1–9 月个人例外，"
            "现行默认仍是全员统一 2000；本文件仅供对照，不得当作现行应发")
    if active_set is not None:
        meta["assumptions"].append(
            "在职名单过滤（指令193·老板拍板）：仅在职名单人员计薪，张浩冉与已离职者不计薪、"
            "其订单进排除清单；门店定档基数仍按当月全量订单（H2 基数口径未改），本月两店按全量与"
            "仅计薪口径档位均为 4%，过滤不改档位率")
        meta["active_roster"] = {
            "file": str(Path(args.active_roster).resolve()),
            "active": sorted(active_set),
            "excluded": list(excluded_agg.values()),
            "excluded_people": len(excluded_agg),
            "excluded_orders": excluded_orders,
        }
        totals["excluded_people"] = len(excluded_agg)
        totals["excluded_orders"] = excluded_orders

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {"meta": meta, "totals": totals, "people": people}
    (out_dir / ("payroll_%s.json" % month)).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if active_set is not None:
        (out_dir / "excluded_roster.json").write_text(
            json.dumps({"month": month,
                        "rule": "指令193 老板拍板：只算在职名单，张浩冉与已离职者不计薪",
                        "active": sorted(active_set),
                        "excluded": list(excluded_agg.values()),
                        "excluded_people": len(excluded_agg),
                        "excluded_orders": excluded_orders,
                        "paid_orders": total_orders,
                        "rows_in_month": len(in_month)},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out_dir / ("payroll_%s.csv" % month), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["月份", "验货人", "单量", "底薪", "基础", "全勤", "拉新",
                    "上门单量", "上门分润", "上门提成(50%)", "线上单量(非门店来源)",
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
    print("线上口径 ONLINE_MODE=%s：来源类型≠门店 的订单金额（不含券）×1%%"
          "（BD/官网及其他非门店来源；旧口径仅官网已废止）" % ONLINE_MODE)
    if not exceptions:
        print("底薪口径：全员统一 2000（基础1000+全勤500+拉新500），不做 1–9 月个人例外")
    if active_set is not None:
        print("单量锚点 PASS：计薪单量 %d + 排除单量 %d == 月内行数 %d"
              % (total_orders, excluded_orders, len(in_month)))
    else:
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
    if active_set is not None:
        print("在职名单过滤（指令193）：计薪人数 %d / 排除人数 %d / 排除单量 %d"
              "（计薪单量 %d + 排除单量 %d == 月内 %d）"
              % (totals["people"], len(excluded_agg), excluded_orders,
                 total_orders, excluded_orders, len(in_month)))
        print("排除清单（仅本机）：%s" % (out_dir / "excluded_roster.json"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
