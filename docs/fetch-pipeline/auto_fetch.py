# -*- coding: utf-8 -*-
"""爱回收官网抓数管道（指令186，2026-10-28 定案口径）。

权威源：爱回收官网 / 加盟商后台（能出单量、分润的那套后台；
2026-10-26 batch002 加盟商对账单即出自此后台）。
拍机堂已取消（2026-10-28 老板定案），不做备用，本脚本不含任何拍机堂入口。

暂缓上传（硬口径）：
- UPLOAD / APPLY 默认 false（见同目录 pipeline_config.json 与下方常量）；
- 本脚本只做「官网后台原生导出 -> 规范化样本落盘（draft）」，
  绝不 merge 生产业务表、绝不运行覆盖正式页的 cockpit 重算；
- 配置文件即使被改成 true，在 ALLOW_UPLOAD_PHASE=False 期间一律强制 false。

分工：浏览器登录官网后台并导出文件，由主线会话（已登录态）执行；
本脚本负责导出文件的识别、规范化、落盘与状态记录，全程离线、不碰凭据。

用法：
  python3 auto_fetch.py --check
      管道自检：配置可读、样本目录可写、打印 UPLOAD/APPLY 实际生效值。
  python3 auto_fetch.py --once --input <官网后台原生导出文件或目录>
      把一份（或一目录）原生导出规范化为样本，落盘到样本目录：
      normalized_sample.csv（前 200 行）+ fields.json + manifest.json + status.json。
      可加 --redact 生成脱敏副本（金额与单号打码，供入仓留存结构）。
  python3 auto_fetch.py --status
      打印样本目录 status.json（最近一次运行状态）。

退出码：
  0 成功；2 need_user_login（无输入，或样本/输入目录存在 NEED_USER_LOGIN 标记）；
  3 export_incomplete（格式不支持 / 关键列缺失 / 数据行数 0）；1 其他错误。

红线：不包含、不读取任何账号、密码、Cookie；不代登录、不绕验证码；
后台只读导出，不点确认对账 / 申诉 / 提现 / 发起结算 / 提交。
"""
import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone, timedelta
from xml.etree import ElementTree as ET

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "pipeline_config.json")

# ===== 暂缓上传硬开关（指令186 定案）=====
# 配置文件 pipeline_config.json 中 UPLOAD / APPLY 默认 false；
# 即使配置被改成 true，只要 ALLOW_UPLOAD_PHASE 仍为 False，一律强制关闭。
# 开启上传须另行验收并经督工点头，届时同步改此常量并留验收记录。
ALLOW_UPLOAD_PHASE = False
DEFAULT_UPLOAD = False
DEFAULT_APPLY = False

SOURCE_NAME = "爱回收官网后台"
STORE_IDS = {"石河子万达店": "4594", "石河子友谊商厦店": "5511"}
DEFAULT_SAMPLE_DIR = os.path.expanduser("~/Desktop/05_Muse督工/fetch_samples_186")
SAMPLE_MAX_ROWS = 200

EXIT_OK, EXIT_ERROR, EXIT_NEED_LOGIN, EXIT_INCOMPLETE = 0, 1, 2, 3


class NeedUserLogin(Exception):
    pass


class ExportIncomplete(Exception):
    pass


# ---------------- 配置与状态 ----------------

def load_config():
    cfg = {}
    if os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
    upload = bool(cfg.get("UPLOAD", DEFAULT_UPLOAD)) and ALLOW_UPLOAD_PHASE
    apply_ = bool(cfg.get("APPLY", DEFAULT_APPLY)) and ALLOW_UPLOAD_PHASE
    sample_dir = os.path.expanduser(cfg.get("SAMPLE_DIR") or DEFAULT_SAMPLE_DIR)
    return cfg, upload, apply_, sample_dir


def now_str():
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def write_status(sample_dir, state, detail, extra=None):
    os.makedirs(sample_dir, exist_ok=True)
    payload = {"last_run": now_str(), "state": state, "detail": detail}
    if extra:
        payload.update(extra)
    path = os.path.join(sample_dir, "status.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    return payload


# ---------------- 原生导出读取（仅标准库） ----------------

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col_idx(ref):
    m = re.match(r"([A-Z]+)", ref or "")
    if not m:
        return None
    idx = 0
    for ch in m.group(1):
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def read_xlsx(path):
    """返回 {sheet_name: [ [cell,...], ... ]}，仅用 zipfile + ElementTree。"""
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")))
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            rroot = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in rroot:
                rels[rel.get("Id")] = rel.get("Target")
        sheets = {}
        for sh in wb.find("m:sheets", NS):
            name = sh.get("name")
            target = rels.get(sh.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"), "")
            spath = "xl/" + target.lstrip("/") if not target.startswith("xl/") else target
            if spath not in names:
                continue
            sroot = ET.fromstring(zf.read(spath))
            rows = []
            for row in sroot.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"):
                cells, maxc = {}, -1
                for c in row.findall("m:c", NS):
                    ci = _col_idx(c.get("r"))
                    if ci is None:
                        ci = maxc + 1
                    t = c.get("t")
                    v = c.find("m:v", NS)
                    if t == "inlineStr":
                        isel = c.find("m:is", NS)
                        val = "".join(x.text or "" for x in isel.iter(
                            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")) if isel is not None else ""
                    elif v is None:
                        val = ""
                    elif t == "s":
                        val = shared[int(v.text)] if v.text is not None and int(v.text) < len(shared) else ""
                    else:
                        val = v.text or ""
                    cells[ci] = val
                    maxc = max(maxc, ci)
                rows.append([cells.get(i, "") for i in range(maxc + 1)])
            sheets[name] = rows
        return sheets


def read_csv_file(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ExportIncomplete(f"CSV 编码无法识别：{path}")
    return [row for row in csv.reader(io.StringIO(text))]


def load_tables(path):
    """返回 [(sheet_name, rows)]；按文件魔数识别真实格式，不信扩展名。"""
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic[:2] == b"PK":
        return list(read_xlsx(path).items())
    if magic == b"\xd0\xcf\x11\xe0":
        raise ExportIncomplete("真实 .xls（OLE）格式：请在官网后台导出 xlsx 后再规范化，不改名冒充")
    return [("csv", read_csv_file(path))]


# ---------------- 结构识别与规范化 ----------------

# 官网后台「旧机销售订单明细」原生导出（batch001 已知 37 列）
ORDER37_KEYS = ["物品编号", "门店名称", "取货方式", "订单状态", "分润金额"]
# 官网后台「加盟商对账单」首次出库订单结算明细（batch002 已知 29 列）
STMT_KEYS = ["回收小订单号", "门店名称", "分润金额"]
# 对账单列表页金额（列表导出/记录 CSV）
LIST_KEYS = ["账单总金额"]


def _find_header(rows):
    for i, row in enumerate(rows[:10]):
        yield i, [str(c).strip() for c in row]


def detect_format(tables):
    """返回 (kind, sheet_name, header_idx, header, data_rows)。"""
    for name, rows in tables:
        if not rows:
            continue
        for hi, header in _find_header(rows):
            hset = set(header)
            if set(ORDER37_KEYS) <= hset and ("完成时间" in hset or "回收完成时间" in hset):
                return "orders37", name, hi, header, rows[hi + 1:]
            if set(STMT_KEYS) <= hset:
                return "statement_detail", name, hi, header, rows[hi + 1:]
            if set(LIST_KEYS) <= hset:
                return "statement_list", name, hi, header, rows[hi + 1:]
    raise ExportIncomplete("关键列缺失：未识别为官网订单明细（37列）/ 对账单明细 / 列表金额任一结构")


def _to_number(v):
    if v is None or v == "":
        return ""
    try:
        f = float(str(v).replace(",", ""))
        return int(f) if f == int(f) and "." not in str(v) else round(f, 2)
    except (TypeError, ValueError):
        return v


def normalize(kind, header, data_rows):
    idx = {h: i for i, h in enumerate(header)}

    def g(row, *cols):
        for c in cols:
            if c in idx and idx[c] < len(row):
                return row[idx[c]]
        return ""

    out = []
    for row in data_rows:
        if not any(str(c).strip() for c in row):
            continue
        if kind == "orders37":
            rec = {
                "source_type": "orders37",
                "order_no": g(row, "物品编号"),
                "store_name": g(row, "门店名称"),
                "finish_time": g(row, "完成时间", "回收完成时间"),
                "pickup": g(row, "取货方式"),
                "order_status": g(row, "订单状态"),
                "order_amount": _to_number(g(row, "订单金额")),
                "profit_amount": _to_number(g(row, "分润金额")),
            }
        elif kind == "statement_detail":
            rec = {
                "source_type": "statement_detail",
                "order_no": g(row, "回收小订单号"),
                "store_name": g(row, "门店名称"),
                "finish_time": g(row, "首次出库日期"),
                "pickup": g(row, "交易方式"),
                "order_status": "",
                "order_amount": _to_number(g(row, "成交订单金额")),
                "profit_amount": _to_number(g(row, "分润金额")),
            }
        else:  # statement_list：列表金额行，逐行保留原始列
            rec = {h: row[idx[h]] if idx[h] < len(row) else "" for h in header}
            rec["source_type"] = "statement_list"
        rec["store_id"] = STORE_IDS.get(str(rec.get("store_name", "")).strip(), "")
        out.append(rec)
    return out


def infer_fields(header, data_rows):
    fields = []
    for i, h in enumerate(header):
        vals = [r[i] for r in data_rows if i < len(r) and str(r[i]).strip() != ""]
        typ = "string"
        if vals:
            if all(re.fullmatch(r"-?\d+", str(v).replace(",", "")) for v in vals):
                typ = "integer"
            elif all(_is_num(v) for v in vals):
                typ = "decimal"
            elif all(re.match(r"\d{4}-\d{2}-\d{2}", str(v)) for v in vals):
                typ = "date"
        fields.append({"name": h, "type": typ, "non_empty": len(vals)})
    return fields


def _is_num(v):
    try:
        float(str(v).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def mask(v):
    s = str(v)
    if len(s) <= 6:
        return "***"
    return s[:4] + "***" + s[-2:]


# ---------------- 子命令 ----------------

def cmd_check(sample_dir, upload, apply_):
    ok = True

    def line(label, good, detail=""):
        nonlocal ok
        ok = ok and good
        print(f"[{'OK' if good else 'FAIL'}] {label}{('：' + detail) if detail else ''}")

    line("配置文件 pipeline_config.json", os.path.isfile(CONFIG_PATH), CONFIG_PATH)
    try:
        os.makedirs(sample_dir, exist_ok=True)
        probe = os.path.join(sample_dir, ".check_probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("probe")
        os.remove(probe)
        line("样本目录可写", True, sample_dir)
    except OSError as e:
        line("样本目录可写", False, str(e))
    print(f"权威源：{SOURCE_NAME}（拍机堂已取消，不做备用）")
    print(f"UPLOAD={str(upload).lower()} / APPLY={str(apply_).lower()}（暂缓上传：强制 false，ALLOW_UPLOAD_PHASE={ALLOW_UPLOAD_PHASE}）")
    print(f"自检结论：{'全部通过' if ok else '有检查项未通过'}")
    return EXIT_OK if ok else EXIT_ERROR


def collect_inputs(path):
    if path is None:
        raise NeedUserLogin("未提供 --input：尚无官网后台导出文件（需主线会话先完成登录导出）")
    if not os.path.exists(path):
        raise NeedUserLogin(f"输入不存在：{path}（需主线会话先完成登录导出）")
    for base in (path, os.path.dirname(os.path.abspath(path))):
        if os.path.isfile(os.path.join(base, "NEED_USER_LOGIN")):
            raise NeedUserLogin("检测到 NEED_USER_LOGIN 标记：会话失效，只记录状态并停手")
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in sorted(os.listdir(path))
                 if os.path.isfile(os.path.join(path, f)) and not f.startswith(".")]
        if not files:
            raise NeedUserLogin(f"输入目录为空：{path}")
        return files
    return [path]


def cmd_once(input_path, sample_dir, upload, apply_, redact):
    try:
        files = collect_inputs(input_path)
    except NeedUserLogin as e:
        write_status(sample_dir, "need_user_login", str(e))
        print(f"[need_user_login] {e}")
        return EXIT_NEED_LOGIN
    all_records, manifests = [], []
    fields_out = []
    for f in files:
        try:
            tables = load_tables(f)
            kind, sheet, hi, header, data_rows = detect_format(tables)
        except ExportIncomplete as e:
            write_status(sample_dir, "export_incomplete", f"{os.path.basename(f)}：{e}")
            print(f"[export_incomplete] {os.path.basename(f)}：{e}")
            return EXIT_INCOMPLETE
        except Exception as e:  # 解析失败归其他错误
            write_status(sample_dir, "error", f"{os.path.basename(f)}：{e}")
            print(f"[error] {os.path.basename(f)}：{e}")
            return EXIT_ERROR
        records = normalize(kind, header, data_rows)
        if not records:
            write_status(sample_dir, "export_incomplete", f"{os.path.basename(f)}：数据行数为 0")
            print(f"[export_incomplete] {os.path.basename(f)}：数据行数为 0")
            return EXIT_INCOMPLETE
        sha = hashlib.sha256(open(f, "rb").read()).hexdigest()
        manifests.append({
            "input_file": os.path.basename(f), "input_sha256": sha,
            "input_bytes": os.path.getsize(f), "format": kind, "sheet": sheet,
            "rows_total": len(records),
        })
        fields_out.append({"input_file": os.path.basename(f), "format": kind,
                           "fields": infer_fields(header, data_rows)})
        all_records.extend(records)

    sample = all_records[:SAMPLE_MAX_ROWS]
    cols = ["source_type", "order_no", "store_id", "store_name", "finish_time",
            "pickup", "order_status", "order_amount", "profit_amount"]
    os.makedirs(sample_dir, exist_ok=True)
    csv_path = os.path.join(sample_dir, "normalized_sample.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for rec in sample:
            row = {c: rec.get(c, "") for c in cols}
            if redact:
                row["order_no"] = mask(row["order_no"])
                row["order_amount"] = "***" if row["order_amount"] != "" else ""
                row["profit_amount"] = "***" if row["profit_amount"] != "" else ""
            w.writerow(row)
    with open(os.path.join(sample_dir, "fields.json"), "w", encoding="utf-8") as fh:
        json.dump(fields_out, fh, ensure_ascii=False, indent=1)
    per_store = {}
    for rec in all_records:
        per_store[rec.get("store_name", "")] = per_store.get(rec.get("store_name", ""), 0) + 1
    has_profit = any(rec.get("profit_amount") not in ("", None) for rec in all_records)
    manifest = {
        "source": SOURCE_NAME,
        "fetched_at": now_str(),
        "timezone": "Asia/Shanghai",
        "pipeline": "jucheng-admin/docs/fetch-pipeline/auto_fetch.py（指令186）",
        "state": "draft",
        "upload": upload,
        "apply": apply_,
        "redacted": redact,
        "stores": STORE_IDS,
        "rows_total": len(all_records),
        "rows_sampled": len(sample),
        "per_store_rows": per_store,
        "has_order_count": True,
        "has_profit": has_profit,
        "inputs": manifests,
        "outputs": ["normalized_sample.csv", "fields.json", "manifest.json", "status.json"],
    }
    with open(os.path.join(sample_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    write_status(sample_dir, "ok",
                 f"规范化完成：{len(all_records)} 行（样本 {len(sample)} 行），upload=false 未上传",
                 {"rows_total": len(all_records), "manifest": "manifest.json"})
    print(f"[ok] {len(all_records)} 行 -> {csv_path}（upload=false，draft）")
    return EXIT_OK


def cmd_status(sample_dir):
    path = os.path.join(sample_dir, "status.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            print(fh.read())
    else:
        print(json.dumps({"state": "never_run", "detail": f"无状态文件：{path}"},
                         ensure_ascii=False))
    return EXIT_OK


def main(argv=None):
    ap = argparse.ArgumentParser(description="爱回收官网抓数管道（指令186）：导出规范化落盘，暂缓上传")
    ap.add_argument("--check", action="store_true", help="管道自检：配置/目录/开关，不登录不抓数")
    ap.add_argument("--once", action="store_true", help="规范化一份官网后台原生导出为样本并落盘")
    ap.add_argument("--input", help="--once 的输入：原生导出文件或目录")
    ap.add_argument("--redact", action="store_true", help="输出脱敏副本（金额/单号打码）")
    ap.add_argument("--status", action="store_true", help="打印最近一次运行状态 JSON")
    ap.add_argument("--sample-dir", help="样本目录（默认读 pipeline_config.json 的 SAMPLE_DIR）")
    args = ap.parse_args(argv)
    _cfg, upload, apply_, cfg_sample_dir = load_config()
    sample_dir = os.path.expanduser(args.sample_dir) if args.sample_dir else cfg_sample_dir
    if args.check:
        return cmd_check(sample_dir, upload, apply_)
    if args.status:
        return cmd_status(sample_dir)
    if args.once:
        return cmd_once(args.input, sample_dir, upload, apply_, args.redact)
    ap.error("必须给 --check / --once / --status 之一")


if __name__ == "__main__":
    sys.exit(main())
