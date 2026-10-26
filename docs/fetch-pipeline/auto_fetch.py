# -*- coding: utf-8 -*-
"""爱回收驾驶舱自动抓取骨架（v3，未启用）。

目标流程：检查登录会话 -> 只读导出订单/对账单 -> 读取列表金额
-> 落盘 muse_v3/data -> 运行 scripts/update_cockpit_v3.py 重算 -> 按退出码处理。

红线：
- 本脚本不包含、不读取任何账号、密码、Cookie 或会话文件；
- 登录 / 扫码 / 短信验证码 / 二次验证一律由用户本人在浏览器中操作，
  本脚本只做“会话是否可用”的只读判断，不可代登录、不绕验证码、不自动重试登录；
- 后台只读：不点确认对账、申诉、提现、发起结算、提交；
- 原始文件只追加不覆盖；导出不完整时不落盘，校验失败（退出码 3）时保留旧驾驶舱。

启用前置条件（缺一不可）：
1. 用户已确认调度方式（Muse 定时任务 / 本地 launchd）与时区 Asia/Shanghai；
2. 用户本人已在 Muse 浏览器中完成一次登录且会话有效；
3. 已用 --dry-run 在测试日期上验证过导出文件结构与命名。

用法：
  python3 auto_fetch.py --date 2026-10-25            # 抓该日完成订单增量（所属月份文件）
  python3 auto_fetch.py --month 2026-10             # 抓整月订单 + 对账单 + 列表金额
  python3 auto_fetch.py --date 2026-10-25 --dry-run # 只检查会话与计划步骤，不导出不落盘
  python3 auto_fetch.py --check                     # 管道自检：只校验配置/目录/状态文件可写并打印管道状态，不登录、不抓数
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai，启用前须经用户确认
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # GOAL 根目录
V3_ROOT = os.path.join(ROOT, "muse_v3")
DATA_ORDERS = os.path.join(V3_ROOT, "data", "orders")
DATA_STMTS = os.path.join(V3_ROOT, "data", "statements")
STATUS_DIR = os.path.join(V3_ROOT, "auto_fetch", "status")
COCKPIT_SCRIPT = os.path.join(ROOT, "scripts", "update_cockpit_v3.py")
STORE_IDS = ("4594", "5511")  # 4594=石河子万达店，5511=石河子友谊商厦店


class NeedUserLogin(Exception):
    """登录会话不可用：立即停，留给用户本人处理，禁止自动重试。"""


class ExportIncomplete(Exception):
    """导出不完整：不落盘、不覆盖旧数据。"""


def write_status(job, result, detail, files=None):
    """每次运行写一份状态 JSON，供用户/店长排查。绝不写入任何凭据信息。"""
    os.makedirs(STATUS_DIR, exist_ok=True)
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    payload = {
        "time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S %z"),
        "job": job,
        "result": result,  # ok / need_user_login / export_incomplete / validation_failed / error
        "detail": detail,
        "files": files or [],
    }
    path = os.path.join(STATUS_DIR, f"status_{stamp}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"[status] {result}: {detail} -> {path}")
    return path


def notify(message):
    """通知出口（骨架）：只写状态与标准输出，不自动对外发消息/邮件。"""
    # TODO: 如用户批准外发渠道后在此接入；未批准前仅本地状态文件 + 控制台。
    print(f"[notify] {message}")


def check_session():
    """只读判断 Muse 浏览器中的后台登录会话是否可用。

    TODO（需用户已登录会话）：在已登录的 Muse 浏览器里打开加盟商工作台首页，
    判断是否落到登录页/验证页。登录、扫码、验证码由用户本人操作；
    本函数不输入任何凭据、不读会话文件。会话不可用时抛 NeedUserLogin 并立即停止。
    """
    raise NeedUserLogin("TODO: 会话检查未实现；启用前需用户本人在 Muse 浏览器完成登录")


def export_orders(month, upto_day=None):
    """只读导出两店订单明细（后台原生导出），按标准命名落盘 DATA_ORDERS。

    TODO（需用户已登录会话）：按完成时间筛选 month（upto_day 非空时只到该日），
    两店各导出一次，保存为 YYYY-MM_门店ID_订单导出.xlsx。
    落盘前校验：文件存在、含必要列、两店齐全；不完整抛 ExportIncomplete 且不覆盖旧文件。
    """
    raise ExportIncomplete("TODO: 订单导出未实现")


def export_statements(month):
    """只读导出两店对账单（账单时间=month），落盘 DATA_STMTS，命名 YYYY-MM_门店ID_对账单.xlsx。

    TODO（需用户已登录会话）：财务管理 -> 加盟商对账单，按账单时间选 month 导出；
    校验 6 个工作表齐全；只读，不点确认对账/申诉/提现/发起结算。
    """
    raise ExportIncomplete("TODO: 对账单导出未实现")


def read_list_amounts(month):
    """从对账单列表页读取两店三项金额，补行到 DATA_STMTS/列表金额.csv。

    TODO（需用户已登录会话）：列表金额（账单总金额、商户开票金额（含税）、
    爱回收开票金额（实际））只出现在列表页，只能页面读取；
    读不到时抛 ExportIncomplete，不要填 0。
    """
    raise ExportIncomplete("TODO: 列表金额读取未实现")


def run_cockpit():
    """运行 v3 重算并处理退出码：0 正常；1 运行错误；2 环境问题；3 校验未通过（门禁）。"""
    proc = subprocess.run([sys.executable, COCKPIT_SCRIPT], cwd=ROOT,
                          capture_output=True, text=True)
    if proc.returncode == 0:
        return "ok", "驾驶舱重算完成"
    if proc.returncode == 3:
        # 门禁：脚本本身不会替换正式页面，旧驾驶舱保持不动；这里只报警，不出结论。
        return "validation_failed", "数据未通过校验（退出码 3），已保留旧驾驶舱，禁止据此做决策"
    return "error", f"驾驶舱重算失败，退出码 {proc.returncode}: {proc.stderr.strip()[:500]}"


def cmd_check():
    """管道自检（指令53 C1）：只校验配置/目录/状态可写并打印管道状态。

    不登录、不抓数、不碰浏览器会话。检查项：
    1. 目录结构（data/orders、data/statements、auto_fetch/status）存在且 status 可写（探针文件写后删）；
    2. 驾驶舱脚本与《待启用配置.md》在位；
    3. 《状态记录.md》最近状态（是否 need_user_login）；
    4. 打印调度口径与管道状态（已备未启用 / enabled 以状态记录为准）。
    """
    ok = True

    def line(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"[{'OK' if good else 'FAIL'}] {label}{('：' + detail) if detail else ''}")

    for d in (V3_ROOT, DATA_ORDERS, DATA_STMTS):
        line(f"目录 {os.path.relpath(d, ROOT)}", os.path.isdir(d))
    line("驾驶舱脚本 scripts/update_cockpit_v3.py", os.path.isfile(COCKPIT_SCRIPT))
    cfg = os.path.join(V3_ROOT, "auto_fetch", "待启用配置.md")
    line("待启用配置.md", os.path.isfile(cfg))
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        probe = os.path.join(STATUS_DIR, ".check_probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("probe")
        os.remove(probe)
        line("状态目录可写 auto_fetch/status", True)
    except OSError as e:
        line("状态目录可写 auto_fetch/status", False, str(e))

    record = os.path.join(V3_ROOT, "auto_fetch", "状态记录.md")
    login_state = "未知（无状态记录）"
    enabled = False
    if os.path.isfile(record):
        with open(record, encoding="utf-8") as fh:
            text = fh.read()
        if "need_user_login" in text:
            login_state = "need_user_login（据状态记录.md）"
        if "enabled" in text:
            enabled = True
    print(f"登录状态：{login_state}")
    print("调度口径：每日 09:30 增量抓取；每月 3 日 10:00 上月整月；时区 Asia/Shanghai")
    print(f"管道状态：{'已启用' if enabled else '已备未启用'}（自检不登录、不抓数）")
    print(f"自检结论：{'全部通过' if ok else '有检查项未通过，见上方 FAIL'}")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="爱回收驾驶舱自动抓取（骨架，未启用）")
    ap.add_argument("--date", help="抓该日（YYYY-MM-DD）完成订单增量")
    ap.add_argument("--month", help="抓整月（YYYY-MM）订单 + 对账单 + 列表金额")
    ap.add_argument("--dry-run", action="store_true", help="只检查会话与计划步骤，不导出不落盘")
    ap.add_argument("--check", action="store_true", help="管道自检：校验配置/目录/状态可写并打印管道状态，不登录不抓数")
    args = ap.parse_args(argv)
    if args.check:
        return cmd_check()
    if not args.date and not args.month:
        ap.error("必须给 --date 或 --month 之一")
    job = f"month={args.month}" if args.month else f"date={args.date}"
    month = args.month or args.date[:7]
    try:
        check_session()
        if args.dry_run:
            write_status(job, "ok", "dry-run：会话可用，未执行导出（骨架）")
            return 0
        files = export_orders(month, upto_day=args.date)
        if args.month:
            files += export_statements(month)
            files += read_list_amounts(month)
    except NeedUserLogin as e:
        write_status(job, "need_user_login", f"卡在登录会话检查：{e}；请用户本人完成登录后手工补跑")
        notify("自动抓取已停止：需要用户本人登录后台")
        return 4
    except ExportIncomplete as e:
        write_status(job, "export_incomplete", str(e))
        notify("自动抓取已停止：导出不完整，旧数据未动")
        return 5
    result, detail = run_cockpit()
    write_status(job, result, detail, files)
    if result != "ok":
        notify(f"自动抓取完成但重算异常：{detail}")
        return 3 if result == "validation_failed" else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
