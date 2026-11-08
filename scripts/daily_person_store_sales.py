#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人店日销售只读，以及可选的 CSV upsert。

读 public.daily_person_store_sales：
  daily   按日合计门店回收、网单不含券、配件销售额
  detail  人×店×日明细

写库默认关闭。upsert 要同时成立：
  1. ALLOW_DAILY_SALES_SEED=true
  2. --apply
不读抓数管道的 UPLOAD / APPLY，也不读工资灌数闸。
冲突键 (day, person_name, store_name)。month 从 day 推导。
不删除本次 CSV 里没有的行。

契约：docs/契约_人店日销售概览.md
web 按同一形状写 Route Handler。本文件不是 Next 应用。
"""

from __future__ import annotations

import argparse
import csv
import io
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

TABLE = "daily_person_store_sales"
REST_PATH = "/rest/v1/" + TABLE
ENV_GATE = "ALLOW_DAILY_SALES_SEED"
CONFLICT = "day,person_name,store_name"
AMOUNT_FIELDS = (
    "recycle_store_amount",
    "recycle_online_amount",
    "accessories_sales",
)
COUNT_FIELDS = ("n_old_machine", "n_accessories")
DETAIL_FIELDS = (
    "day",
    "month",
    "person_name",
    "store_name",
    *AMOUNT_FIELDS,
    *COUNT_FIELDS,
    "updated_at",
)
SELECT = ",".join(DETAIL_FIELDS)
REQUIRED_CSV = ("day", "person_name", "store_name", *AMOUNT_FIELDS)
PAGE_SIZE = 1000
MAX_ROWS = 20000
SEED_BATCH = 1000
MONEY_MAX = Decimal("999999999999.99")
COUNT_MAX = 2_000_000_000
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
PREFER_UPSERT = "resolution=merge-duplicates,return=minimal"
PREFER_COUNT = "count=exact"
TZ_NAME = "Asia/Shanghai"
ORDER = "day.asc,person_name.asc,store_name.asc"


class SalesError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def seed_gate_open(env=None):
    """只认 ALLOW_DAILY_SALES_SEED=true。不读工资闸，也不读 UPLOAD / APPLY。"""
    if env is None:
        env = os.environ
    return str(env.get(ENV_GATE, "")).strip().lower() == "true"


def parse_month(value):
    text = str(value or "").strip()
    if not MONTH_RE.match(text):
        raise SalesError("month 须为 YYYY-MM")
    return text


def parse_day(value):
    text = str(value or "").strip()
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise SalesError("day 须为 YYYY-MM-DD")
    return parsed.date().isoformat()


def _name(value, label, *, required):
    if value is None or str(value).strip() == "":
        if required:
            raise SalesError("%s 为空" % label)
        return None
    text = str(value).strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise SalesError("%s 含控制字符" % label)
    return text


def format_money(value):
    """读回金额。numeric 文本或 JSON 数字都收成两位小数字符串。"""
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise SalesError("读回的金额非法")
    try:
        raw = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except Exception:
        raise SalesError("读回的金额非法")
    if not raw.is_finite():
        raise SalesError("读回的金额非法")
    quant = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(quant) > MONEY_MAX:
        raise SalesError("读回的金额超出 numeric(14,2)")
    return format(quant, "f")


def parse_money(value):
    """CSV 金额。最多两位小数，不把更长的刻度四舍五入后悄悄改掉。"""
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise SalesError("金额为空或非法")
    text = str(value).strip().replace(",", "")
    try:
        raw = Decimal(text)
    except Exception:
        raise SalesError("金额不是数字")
    if not raw.is_finite():
        raise SalesError("金额不是数字")
    exponent = raw.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise SalesError("金额最多两位小数")
    quant = raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(quant) > MONEY_MAX:
        raise SalesError("金额超出 numeric(14,2)")
    return format(quant, "f")


def parse_count(value):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise SalesError("件数不是整数")
    try:
        raw = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except Exception:
        raise SalesError("件数不是整数")
    if not raw.is_finite() or raw != raw.to_integral_value():
        raise SalesError("件数不是整数")
    number = int(raw)
    if number < 0 or number > COUNT_MAX:
        raise SalesError("件数超出范围")
    return number


def normalize_count(value):
    return parse_count(value)


def read_spec(month, day=None, person_name=None, store_name=None):
    month_text = parse_month(month)
    day_text = None
    if day not in (None, ""):
        day_text = parse_day(day)
        if day_text[:7] != month_text:
            raise SalesError("day 不在 month 内")
    return {
        "month": month_text,
        "day": day_text,
        "person_name": _name(person_name, "person_name", required=False),
        "store_name": _name(store_name, "store_name", required=False),
    }


def _eq(value, *, quoted):
    text = str(value)
    if quoted:
        text = '"%s"' % text.replace('"', '""')
    return "eq." + urllib.parse.quote(text, safe="")


def _https_origin(base_url):
    parts = urllib.parse.urlsplit((base_url or "").strip())
    if (
        parts.scheme != "https"
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise SalesError("SUPABASE_URL 须为 https 的项目根地址")
    return "%s://%s" % (parts.scheme, parts.netloc)


def _assert_table_url(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.path != REST_PATH:
        raise SalesError("只允许访问表 %s" % TABLE)
    return url


def credentials(env):
    env = env or {}
    url = (env.get("SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip()
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE_ROLE") or "").strip()
    if not url or not key:
        raise SalesError("需要 SUPABASE_URL 与 SUPABASE_SERVICE_ROLE_KEY")
    return _https_origin(url), key


def build_read_query(spec, *, limit, offset):
    parts = [
        "select=" + SELECT,
        "month=" + _eq(spec["month"], quoted=False),
    ]
    if spec.get("day"):
        parts.append("day=" + _eq(spec["day"], quoted=False))
    if spec.get("person_name"):
        parts.append("person_name=" + _eq(spec["person_name"], quoted=True))
    if spec.get("store_name"):
        parts.append("store_name=" + _eq(spec["store_name"], quoted=True))
    parts.append("order=" + ORDER)
    parts.append("limit=%d" % int(limit))
    parts.append("offset=%d" % int(offset))
    return "&".join(parts)


def build_read_url(base_url, spec, *, limit, offset):
    url = _https_origin(base_url) + REST_PATH + "?" + build_read_query(
        spec, limit=limit, offset=offset)
    return _assert_table_url(url)


def build_upsert_url(base_url):
    url = "%s%s?on_conflict=%s" % (_https_origin(base_url), REST_PATH, CONFLICT)
    return _assert_table_url(url)


def _auth_headers(key, *, content_type=False):
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
    }
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


def _exchange(method, url, key, body, prefer, transport):
    _assert_table_url(url)
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = _auth_headers(key, content_type=data is not None)
    if prefer:
        headers["Prefer"] = prefer
    if transport is not None:
        try:
            result = transport(method, url, headers, data)
        except SalesError:
            raise
        except OSError as exc:
            raise SalesError("连接失败") from exc
        if not isinstance(result, tuple) or len(result) not in (2, 3):
            raise SalesError("传输返回不正确")
        content_range = result[2] if len(result) == 3 else None
        return result[0], result[1], content_range
    return _urllib_exchange(method, url, headers, data)


def _urllib_exchange(method, url, headers, data):
    req = urllib.request.Request(url, data=data, method=method)
    for name, value in headers.items():
        req.add_header(name, value)
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=30) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
            content_range = resp.headers.get("Content-Range") if getattr(resp, "headers", None) else None
    except urllib.error.HTTPError as exc:
        exc.read()
        raise SalesError("请求失败 HTTP %s" % exc.code) from exc
    except urllib.error.URLError as exc:
        raise SalesError("连接失败") from exc
    return status, raw, content_range


def _content_range_total(value):
    if not value or "/" not in str(value):
        return None
    total = str(value).rsplit("/", 1)[-1].strip()
    if not total.isdigit():
        return None
    return int(total)


def _decode_rows(status, raw, *, what):
    if status is not None and 300 <= int(status) < 400:
        raise SalesError("%s被拒绝：不跟随跳转" % what)
    if status is None or int(status) >= 400:
        raise SalesError("%s失败 HTTP %s" % (what, status))
    if status == 204 or raw in (None, b"", ""):
        return []
    try:
        text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
        payload = json.loads(text, parse_float=Decimal)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SalesError("%s响应不是 JSON" % what) from exc
    if not isinstance(payload, list):
        raise SalesError("%s响应不是数组" % what)
    return payload


def _row_signature(row):
    return (
        str(row.get("day")),
        str(row.get("person_name")),
        str(row.get("store_name")),
    )


def fetch_rows(spec, *, env=None, transport=None, page_size=PAGE_SIZE):
    """按稳定顺序翻页。有 Content-Range 时必须凑满总数，短页不能冒充整月。"""
    if page_size < 1 or page_size > PAGE_SIZE:
        raise SalesError("分页大小非法")
    origin, key = credentials(env or {})
    offset = 0
    found = []
    previous = None
    while True:
        url = build_read_url(origin, spec, limit=page_size, offset=offset)
        status, raw, content_range = _exchange(
            "GET", url, key, None, PREFER_COUNT, transport)
        page = _decode_rows(status, raw, what="读取")
        total = _content_range_total(content_range)
        if not page:
            if total not in (None, 0) and offset < total:
                raise SalesError("分页被截断，已中止")
            break
        signature = _row_signature(page[0])
        if previous == signature:
            raise SalesError("分页没有前进，已中止")
        previous = signature
        found.extend(page)
        if total is not None and len(found) > total:
            raise SalesError("分页行数超出 Content-Range，已中止")
        if len(found) > MAX_ROWS:
            raise SalesError("读取达到上限，已中止")
        if total is not None and len(found) >= total:
            break
        if len(page) < page_size:
            if total is not None and len(found) < total:
                raise SalesError("分页被截断，已中止")
            break
        offset += page_size
    return found


def normalize_detail_row(raw, month):
    if not isinstance(raw, dict):
        raise SalesError("读回的行不是对象")
    day = parse_day(raw.get("day"))
    derived = day[:7]
    row_month = str(raw.get("month") or "").strip() or derived
    if row_month != month or derived != month:
        raise SalesError("读回的月份与查询不一致")
    updated = raw.get("updated_at")
    return {
        "day": day,
        "month": month,
        "person_name": _name(raw.get("person_name"), "person_name", required=True),
        "store_name": _name(raw.get("store_name"), "store_name", required=True),
        "recycle_store_amount": format_money(raw.get("recycle_store_amount")),
        "recycle_online_amount": format_money(raw.get("recycle_online_amount")),
        "accessories_sales": format_money(raw.get("accessories_sales")),
        "n_old_machine": normalize_count(raw.get("n_old_machine")),
        "n_accessories": normalize_count(raw.get("n_accessories")),
        "updated_at": None if updated is None or str(updated).strip() == "" else str(updated),
    }


def _matches(row, spec):
    if row["month"] != spec["month"] or row["day"][:7] != spec["month"]:
        return False
    if spec.get("day") and row["day"] != spec["day"]:
        return False
    if spec.get("person_name") and row["person_name"] != spec["person_name"]:
        return False
    if spec.get("store_name") and row["store_name"] != spec["store_name"]:
        return False
    return True


def _sort_key(row):
    return (row["day"], row["person_name"], row["store_name"])


def load_detail_rows(spec, *, env=None, transport=None, row_source=None, page_size=PAGE_SIZE):
    if row_source is not None:
        raw_rows = row_source(spec)
    else:
        raw_rows = fetch_rows(spec, env=env, transport=transport, page_size=page_size)
    if not isinstance(raw_rows, list):
        raise SalesError("读回的响应不是数组")
    rows = [normalize_detail_row(raw, spec["month"]) for raw in raw_rows]
    rows = [row for row in rows if _matches(row, spec)]
    rows.sort(key=_sort_key)
    return rows


def sum_by_day(rows):
    buckets = {}
    for row in rows:
        bucket = buckets.get(row["day"])
        if bucket is None:
            bucket = {field: Decimal("0.00") for field in AMOUNT_FIELDS}
            buckets[row["day"]] = bucket
        for field in AMOUNT_FIELDS:
            bucket[field] += Decimal(row[field])
    out = []
    for day in sorted(buckets):
        item = {"day": day}
        for field in AMOUNT_FIELDS:
            item[field] = format(
                buckets[day][field].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
        out.append(item)
    return out


def daily_totals(month, *, env=None, transport=None, row_source=None, page_size=PAGE_SIZE):
    spec = read_spec(month)
    rows = load_detail_rows(
        spec, env=env, transport=transport, row_source=row_source, page_size=page_size)
    return sum_by_day(rows)


def person_store_days(
    month, *, day=None, person_name=None, store_name=None,
    env=None, transport=None, row_source=None, page_size=PAGE_SIZE,
):
    spec = read_spec(month, day=day, person_name=person_name, store_name=store_name)
    return load_detail_rows(
        spec, env=env, transport=transport, row_source=row_source, page_size=page_size)


def as_of_iso(now):
    tz = ZoneInfo(TZ_NAME)
    if now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)
    return now.replace(microsecond=0).isoformat()


def rows_from_csv(path, *, month=None, now=None):
    file_path = Path(path)
    if not file_path.is_file():
        raise SalesError("CSV 不存在")
    text = file_path.read_text(encoding="utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    try:
        header_row = next(reader)
    except StopIteration:
        raise SalesError("CSV 没有表头")
    header = [cell.strip() for cell in header_row]
    if any(not name for name in header) or len(header) != len(set(header)):
        raise SalesError("CSV 表头重复或为空")
    missing = [name for name in REQUIRED_CSV if name not in header]
    if missing:
        raise SalesError("CSV 缺少列: " + ",".join(missing))
    expect_month = parse_month(month) if month not in (None, "") else None
    stamped = as_of_iso(now or datetime.now(ZoneInfo(TZ_NAME)))
    rows = []
    seen = set()
    for cells in reader:
        if not any(cell.strip() for cell in cells):
            continue
        item = {
            header[index]: (cells[index].strip() if index < len(cells) else "")
            for index in range(len(header))
        }
        day = parse_day(item.get("day"))
        derived = day[:7]
        given_month = item.get("month", "").strip()
        if given_month and given_month != derived:
            raise SalesError("CSV 的 month 与 day 不一致: %s" % day)
        if expect_month and derived != expect_month:
            raise SalesError("CSV 的 day 不在 %s: %s" % (expect_month, day))
        person = _name(item.get("person_name"), "person_name", required=True)
        store = _name(item.get("store_name"), "store_name", required=True)
        key = (day, person, store)
        if key in seen:
            raise SalesError("CSV 冲突键重复: %s %s %s" % key)
        seen.add(key)
        rows.append({
            "day": day,
            "month": derived,
            "person_name": person,
            "store_name": store,
            "recycle_store_amount": parse_money(item.get("recycle_store_amount")),
            "recycle_online_amount": parse_money(item.get("recycle_online_amount")),
            "accessories_sales": parse_money(item.get("accessories_sales")),
            "n_old_machine": parse_count(item.get("n_old_machine")),
            "n_accessories": parse_count(item.get("n_accessories")),
            "updated_at": stamped,
        })
    if not rows:
        raise SalesError("CSV 没有数据行")
    if len(rows) > MAX_ROWS:
        raise SalesError("CSV 行数超过上限")
    rows.sort(key=_sort_key)
    return rows


def _totals(rows):
    sums = {field: Decimal("0.00") for field in AMOUNT_FIELDS}
    for row in rows:
        for field in AMOUNT_FIELDS:
            sums[field] += Decimal(row[field])
    return {
        field: format(sums[field].quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
        for field in AMOUNT_FIELDS
    }


def _chunks(rows, size):
    step = int(size)
    for start in range(0, len(rows), step):
        yield rows[start:start + step]


def postgrest_upsert(base_url, service_key, rows, *, transport=None, batch_size=SEED_BATCH):
    if not rows:
        raise SalesError("没有行可写")
    if batch_size < 1:
        raise SalesError("批次大小非法")
    origin = _https_origin(base_url)
    key = (service_key or "").strip()
    if not key:
        raise SalesError("缺少 SUPABASE_SERVICE_ROLE_KEY")
    url = build_upsert_url(origin)
    total = 0
    for batch in _chunks(rows, batch_size):
        for row in batch:
            if "id" in row:
                raise SalesError("upsert 不写 id")
        status, raw, _content_range = _exchange(
            "POST", url, key, batch, PREFER_UPSERT, transport)
        if status is not None and 300 <= int(status) < 400:
            raise SalesError("写库被拒绝：不跟随跳转")
        if status is None or int(status) not in (200, 201, 204):
            raise SalesError("写库失败 HTTP %s" % status)
        if raw not in (None, b"", ""):
            # return=minimal 通常为空。若库仍回了正文，确认它是 JSON，但不拿去覆盖本地行。
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                raise SalesError("写库响应不是 JSON") from exc
        total += len(batch)
    return total


def seed_csv(
    path, *, month=None, env=None, apply=False, writer=None, transport=None,
    now=None, batch_size=SEED_BATCH,
):
    if env is None:
        env = os.environ
    rows = rows_from_csv(path, month=month, now=now)
    gate = seed_gate_open(env)
    will_write = bool(apply) and gate
    summary = {
        "apply": bool(apply),
        "wrote": False,
        "gate": ENV_GATE,
        "gate_open": gate,
        "table": TABLE,
        "on_conflict": CONFLICT,
        "row_count": len(rows),
        "months": sorted({row["month"] for row in rows}),
        "totals": _totals(rows),
        "note": (
            "未写库。写库要 %s=true 与 --apply 同时成立。"
            "不读 UPLOAD / APPLY。" % ENV_GATE
        ),
    }
    if not will_write:
        summary["rows"] = rows
        return summary
    if writer is None:
        origin, key = credentials(env)
        written = postgrest_upsert(
            origin, key, rows, transport=transport, batch_size=batch_size)
    else:
        written = writer(rows)
    if written != len(rows):
        raise SalesError("写库返回行数不一致")
    summary["wrote"] = True
    summary["note"] = "已 upsert %s。冲突键 %s。不删除 CSV 里没有的行。" % (TABLE, CONFLICT)
    return summary


def _emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None, env=None, transport=None, row_source=None, writer=None, now=None):
    parser = argparse.ArgumentParser(
        description="人店日销售：按日合计与明细只读；CSV 种子默认不写库")
    sub = parser.add_subparsers(dest="cmd", required=True)

    daily = sub.add_parser("daily", help="按日合计三个金额")
    daily.add_argument("--month", required=True, help="YYYY-MM")

    detail = sub.add_parser("detail", help="人×店×日明细")
    detail.add_argument("--month", required=True, help="YYYY-MM")
    detail.add_argument("--day", default=None, help="YYYY-MM-DD，可省略")
    detail.add_argument("--person-name", default=None)
    detail.add_argument("--store-name", default=None)

    seed = sub.add_parser("seed", help="从 CSV upsert，默认不写库")
    seed.add_argument("--csv", required=True, help="列与表一致；month 可由 day 推导")
    seed.add_argument("--month", default=None, help="若给出，每一行的 day 都必须属于该月")
    seed.add_argument(
        "--apply", action="store_true",
        help="与 ALLOW_DAILY_SALES_SEED=true 同时成立才写库")

    args = parser.parse_args(argv)
    if env is None:
        env = os.environ
    try:
        if args.cmd == "daily":
            payload = daily_totals(
                args.month, env=env, transport=transport, row_source=row_source)
        elif args.cmd == "detail":
            payload = person_store_days(
                args.month, day=args.day, person_name=args.person_name,
                store_name=args.store_name, env=env, transport=transport,
                row_source=row_source)
        else:
            payload = seed_csv(
                args.csv, month=args.month, env=env, apply=args.apply,
                writer=writer, transport=transport, now=now)
    except SalesError as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    _emit(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
