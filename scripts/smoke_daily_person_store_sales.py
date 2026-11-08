#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人店日销售只读与种子冒烟。假客户端，不连生产，不写库。"""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "daily_person_store_sales.py"
FIXTURE = REPO / "scripts" / "fixtures" / "daily_person_store_sales_sample.csv"
CONTRACT = REPO / "docs" / "契约_人店日销售概览.md"
CONFIG = REPO / "docs" / "fetch-pipeline" / "pipeline_config.json"
AUTO_FETCH = REPO / "docs" / "fetch-pipeline" / "auto_fetch.py"
NOW = datetime(2026, 11, 8, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
STAMP = "2026-11-08T18:00:00+08:00"
SECRET_ENV = (
    "SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_SERVICE_ROLE",
    "ALLOW_DAILY_SALES_SEED",
    "ALLOW_APPLY_REALTIME_DRAFT",
    "ALLOW_EMPLOYEE_DRAFT_WRITE",
)


def load_mod():
    spec = importlib.util.spec_from_file_location("daily_person_store_sales_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def snapshot(root):
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def run_main(mod, argv, env, row_source=None, writer=None, transport=None, now=NOW):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(
            argv, env=env, row_source=row_source, writer=writer,
            transport=transport, now=now)
    return code, out.getvalue(), err.getvalue()


def cli(args, extra_env=None):
    env = os.environ.copy()
    for key in SECRET_ENV:
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )


def raw_row(day, person, store, store_amt, online, accessories, old=None, acc=None, month=None):
    return {
        "day": day,
        "month": month or day[:7],
        "person_name": person,
        "store_name": store,
        "recycle_store_amount": store_amt,
        "recycle_online_amount": online,
        "accessories_sales": accessories,
        "n_old_machine": old,
        "n_accessories": acc,
        "updated_at": STAMP,
        "id": "should-not-leak",
    }


def source_rows(spec):
    rows = [
        raw_row("2026-11-02", "示例甲", "示例店", "10.10", "5.02", "0.47"),
        raw_row("2026-11-01", "示例乙", "示例店", "50", "0", "0", 1, 0),
        raw_row("2026-11-01", "示例甲", "另一店", "1.00", "2.00", "3.00", 4, 5),
        raw_row("2026-11-01", "示例甲", "示例店", "100.50", "20.00", "1.25", 2, 1),
    ]
    return rows


class Capture:
    def __init__(self):
        self.calls = 0
        self.rows = None

    def __call__(self, rows):
        self.calls += 1
        self.rows = rows
        return len(rows)


def must_raise(mod, fn):
    try:
        fn()
    except mod.SalesError as exc:
        return exc
    raise AssertionError("应当拒绝")


def main():
    for key in SECRET_ENV:
        os.environ.pop(key, None)

    def _no_network(*_args, **_kwargs):
        raise AssertionError("smoke 不应打开网络")

    urllib.request.urlopen = _no_network
    urllib.request.build_opener = _no_network

    watched = {
        "config": CONFIG.read_bytes(),
        "auto": AUTO_FETCH.read_bytes(),
        "apply": (REPO / "scripts" / "apply_realtime_payroll_draft.py").read_bytes(),
        "fetch": (REPO / "scripts" / "fetch_jobs.py").read_bytes(),
        "payroll": snapshot(REPO / "scripts" / "payroll"),
        "supabase": snapshot(REPO / "supabase"),
        "pipeline": snapshot(REPO / "docs" / "fetch-pipeline"),
    }
    config = json.loads(watched["config"])
    assert config["UPLOAD"] is False and config["APPLY"] is False
    assert b"ALLOW_UPLOAD_PHASE = False" in watched["auto"]
    assert b"DEFAULT_APPLY = False" in watched["auto"]

    mod = load_mod()
    source = SCRIPT.read_text(encoding="utf-8")
    for token in (
        "salary_realtime_draft",
        "ALLOW_APPLY_REALTIME_DRAFT",
        "ALLOW_EMPLOYEE_DRAFT_WRITE",
        "realtime_payroll",
        "pipeline_config",
        "salary_monthly",
        "salary_detail",
        "mhhjujgmkpkxeqexktbv",
        "urlopen",
    ):
        assert token not in source, token
    assert "urllib.request" in source
    assert mod.TABLE == "daily_person_store_sales"
    assert mod.ENV_GATE == "ALLOW_DAILY_SALES_SEED"
    assert mod.CONFLICT == "day,person_name,store_name"
    assert mod.seed_gate_open({}) is False
    assert mod.seed_gate_open({mod.ENV_GATE: "1"}) is False
    assert mod.seed_gate_open({mod.ENV_GATE: "yes"}) is False
    assert mod.seed_gate_open({mod.ENV_GATE: "false"}) is False
    assert mod.seed_gate_open({"ALLOW_APPLY_REALTIME_DRAFT": "true"}) is False
    assert mod.seed_gate_open({"ALLOW_EMPLOYEE_DRAFT_WRITE": "true"}) is False
    assert mod.seed_gate_open({mod.ENV_GATE: " true "}) is True
    assert mod.seed_gate_open({mod.ENV_GATE: "TRUE"}) is True

    contract = CONTRACT.read_text(encoding="utf-8")
    for phrase in (
        "GET /api/ops/daily-person-store-sales/daily?month=YYYY-MM",
        "GET /api/ops/daily-person-store-sales?month=YYYY-MM",
        "ALLOW_DAILY_SALES_SEED",
        "60160.00",
        "190225.00",
        "2925.72",
        "UPLOAD",
        "owner",
        "service role",
    ):
        assert phrase in contract, phrase

    assert mod.format_money("2925.72") == "2925.72"
    assert mod.format_money("60160") == "60160.00"
    assert mod.format_money("190225.00") == "190225.00"
    assert mod.format_money(mod.parse_money("60,160")) == "60160.00"
    exc = must_raise(mod, lambda: mod.parse_money("1.005"))
    assert "两位小数" in str(exc)
    exc = must_raise(mod, lambda: mod.parse_month("2026-13"))
    assert "YYYY-MM" in str(exc)
    exc = must_raise(mod, lambda: mod.parse_month("202611"))
    assert "YYYY-MM" in str(exc)
    exc = must_raise(mod, lambda: mod.parse_day("2026-02-29"))
    assert "YYYY-MM-DD" in str(exc)
    assert mod.parse_day("2024-02-29") == "2024-02-29"
    exc = must_raise(mod, lambda: mod.read_spec("2026-11", day="2026-12-01"))
    assert "不在 month" in str(exc)

    totals = mod.daily_totals("2026-11", row_source=source_rows)
    assert totals == [
        {
            "day": "2026-11-01",
            "recycle_store_amount": "151.50",
            "recycle_online_amount": "22.00",
            "accessories_sales": "4.25",
        },
        {
            "day": "2026-11-02",
            "recycle_store_amount": "10.10",
            "recycle_online_amount": "5.02",
            "accessories_sales": "0.47",
        },
    ]
    assert "person_name" not in totals[0]
    assert "id" not in totals[0]

    detail = mod.person_store_days("2026-11", row_source=source_rows)
    assert [row["person_name"] + row["store_name"] for row in detail] == [
        "示例乙示例店", "示例甲另一店", "示例甲示例店", "示例甲示例店",
    ]
    assert [row["day"] for row in detail] == [
        "2026-11-01", "2026-11-01", "2026-11-01", "2026-11-02",
    ]
    assert "id" not in detail[0]
    assert detail[2]["n_old_machine"] == 2 and detail[2]["n_accessories"] == 1
    filtered = mod.person_store_days(
        "2026-11", day="2026-11-01", person_name="示例甲", store_name="示例店",
        row_source=source_rows)
    assert len(filtered) == 1
    assert filtered[0]["recycle_store_amount"] == "100.50"
    assert mod.person_store_days("2026-11", person_name="没有的人", row_source=source_rows) == []

    def wrong_month(_spec):
        return [raw_row("2026-10-01", "示例甲", "示例店", "1", "1", "1", month="2026-10")]

    exc = must_raise(mod, lambda: mod.daily_totals("2026-11", row_source=wrong_month))
    assert "月份" in str(exc)

    code, out, err = run_main(
        mod, ["daily", "--month", "2026-11"], {}, row_source=source_rows)
    assert code == 0 and err == "", err
    assert json.loads(out) == totals
    code, out, err = run_main(mod, ["daily", "--month", "2026-13"], {}, row_source=source_rows)
    assert code == 1 and out == "" and "YYYY-MM" in err

    spec = mod.read_spec("2026-11", person_name='示例,甲', store_name='店"A')
    url = mod.build_read_url("https://example.supabase.co", spec, limit=1000, offset=0)
    assert url.startswith("https://example.supabase.co/rest/v1/daily_person_store_sales?")
    assert urllib.parse.urlsplit(url).path == "/rest/v1/daily_person_store_sales"
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query, keep_blank_values=True)
    assert query["month"] == ["eq.2026-11"]
    assert query["person_name"] == ['eq."示例,甲"']
    assert query["store_name"] == ['eq."店""A"']
    assert "test-service-role" not in url
    exc = must_raise(
        mod, lambda: mod.build_read_url("http://example.supabase.co", spec, limit=1, offset=0))
    assert "https" in str(exc)
    exc = must_raise(
        mod, lambda: mod.build_read_url(
            "https://example.supabase.co/rest/v1", spec, limit=1, offset=0))
    assert "https" in str(exc)
    exc = must_raise(
        mod, lambda: mod.build_read_url(
            "https://user:pass@example.supabase.co", spec, limit=1, offset=0))
    assert "https" in str(exc)

    pages = []

    def paged(method, url, headers, data):
        assert method == "GET" and data is None
        assert headers["apikey"] == "alias-key"
        assert headers["Authorization"] == "Bearer alias-key"
        assert headers["Prefer"] == "count=exact"
        assert "alias-key" not in url
        assert urllib.parse.urlsplit(url).path == "/rest/v1/daily_person_store_sales"
        parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert parsed["month"] == ["eq.2026-11"]
        assert "person_name" not in parsed
        offset = int(parsed["offset"][0])
        limit = int(parsed["limit"][0])
        body = [
            raw_row("2026-11-01", "示例甲", "示例店", "0.10", "0.20", "0.30", 1, 2),
            raw_row("2026-11-02", "示例乙", "示例店", "0.40", "0.50", "0.60"),
            raw_row("2026-11-03", "示例甲", "示例店", "1.00", "1.00", "1.00", 3, 4),
        ]
        page = body[offset:offset + limit]
        pages.append(offset)
        end = offset + len(page) - 1 if page else offset
        content_range = "*/0" if not page else "%s-%s/%s" % (offset, end, len(body))
        return 200, json.dumps(page).encode("utf-8"), content_range

    summed = mod.daily_totals(
        "2026-11",
        env={
            "NEXT_PUBLIC_SUPABASE_URL": "https://example.supabase.co/",
            "SUPABASE_SERVICE_ROLE": "alias-key",
        },
        transport=paged,
        page_size=2,
    )
    assert pages == [0, 2]
    assert summed[0]["recycle_store_amount"] == "0.10"
    assert summed[1]["accessories_sales"] == "0.60"
    assert summed[2]["recycle_online_amount"] == "1.00"
    assert summed[0]["day"] == "2026-11-01"

    def stalled(method, url, headers, data):
        return 200, json.dumps([
            raw_row("2026-11-01", "示例甲", "示例店", "1", "1", "1"),
        ]).encode("utf-8")

    exc = must_raise(mod, lambda: mod.daily_totals(
        "2026-11",
        env={"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"},
        transport=stalled,
        page_size=1,
    ))
    assert "分页没有前进" in str(exc)

    def truncated(method, url, headers, data):
        row = raw_row("2026-11-01", "示例甲", "示例店", "1", "1", "1")
        return 200, json.dumps([row]).encode("utf-8"), "0-0/4"

    exc = must_raise(mod, lambda: mod.daily_totals(
        "2026-11",
        env={"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"},
        transport=truncated,
        page_size=2,
    ))
    assert "分页被截断" in str(exc)

    def redirected(method, url, headers, data):
        return 302, b""

    exc = must_raise(mod, lambda: mod.person_store_days(
        "2026-11",
        env={"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"},
        transport=redirected,
    ))
    assert "跳转" in str(exc)

    def broken(method, url, headers, data):
        return 200, b"{"

    exc = must_raise(mod, lambda: mod.daily_totals(
        "2026-11",
        env={"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"},
        transport=broken,
    ))
    assert "不是数组" in str(exc) or "不是 JSON" in str(exc)

    def denied(method, url, headers, data):
        return 401, b'{"message":"no"}'

    exc = must_raise(mod, lambda: mod.daily_totals(
        "2026-11",
        env={"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"},
        transport=denied,
    ))
    assert "HTTP 401" in str(exc)
    assert "no" not in str(exc)

    calls = []

    def no_http(*_args, **_kwargs):
        raise AssertionError("不该连接")

    closed = Capture()
    summary = mod.seed_csv(
        FIXTURE, month="2026-11", env={"ALLOW_APPLY_REALTIME_DRAFT": "true"},
        apply=True, writer=closed, transport=no_http, now=NOW)
    assert summary["wrote"] is False and closed.calls == 0
    assert summary["row_count"] == 3
    assert summary["totals"] == {
        "recycle_store_amount": "160.60",
        "recycle_online_amount": "25.02",
        "accessories_sales": "1.72",
    }
    assert summary["rows"][0]["n_accessories"] == 0
    assert summary["rows"][1]["n_old_machine"] == 2
    assert summary["rows"][2]["n_old_machine"] is None
    assert summary["rows"][2]["n_accessories"] is None
    assert summary["rows"][0]["month"] == "2026-11"
    assert "id" not in summary["rows"][0]
    assert "UPLOAD / APPLY" in summary["note"]

    gate_only = Capture()
    summary = mod.seed_csv(
        FIXTURE, month="2026-11", env={mod.ENV_GATE: "true"},
        apply=False, writer=gate_only, transport=no_http, now=NOW)
    assert summary["wrote"] is False and summary["gate_open"] is True and gate_only.calls == 0

    opened = Capture()
    summary = mod.seed_csv(
        FIXTURE, month="2026-11",
        env={mod.ENV_GATE: "true", "ALLOW_APPLY_REALTIME_DRAFT": "true"},
        apply=True, writer=opened, transport=no_http, now=NOW)
    assert summary["wrote"] is True and opened.calls == 1
    assert "rows" not in summary
    assert opened.rows[0]["updated_at"] == STAMP
    assert opened.rows[0]["month"] == "2026-11-01"[:7]
    assert all("id" not in row for row in opened.rows)

    with tempfile.TemporaryDirectory(prefix="daily_sales_smoke_") as tmp:
        root = Path(tmp)
        duplicate = root / "duplicate.csv"
        duplicate.write_text(
            "day,person_name,store_name,recycle_store_amount,recycle_online_amount,accessories_sales\n"
            "2026-11-01,示例甲,示例店,1,2,3\n"
            "2026-11-01,示例甲,示例店,4,5,6\n",
            encoding="utf-8",
        )
        caught = Capture()
        exc = must_raise(mod, lambda: mod.seed_csv(
            duplicate, env={mod.ENV_GATE: "true"}, apply=True, writer=caught, now=NOW))
        assert "重复" in str(exc) and caught.calls == 0

        mismatch = root / "mismatch.csv"
        mismatch.write_text(
            "day,month,person_name,store_name,recycle_store_amount,recycle_online_amount,accessories_sales\n"
            "2026-11-01,2026-10,示例甲,示例店,1.00,2.00,3.00\n",
            encoding="utf-8",
        )
        exc = must_raise(mod, lambda: mod.seed_csv(mismatch, now=NOW))
        assert "不一致" in str(exc)

        other_month = root / "october.csv"
        other_month.write_text(
            "day,person_name,store_name,recycle_store_amount,recycle_online_amount,accessories_sales\n"
            "2026-10-01,示例甲,示例店,1,2,3\n",
            encoding="utf-8",
        )
        exc = must_raise(mod, lambda: mod.seed_csv(other_month, month="2026-11", now=NOW))
        assert "不在 2026-11" in str(exc)

        extra = root / "extra.csv"
        extra.write_text(
            "id,day,person_name,store_name,recycle_store_amount,recycle_online_amount,"
            "accessories_sales,updated_at,n_old_machine\n"
            "ignore-me,2026-11-03,示例甲,示例店,\"1,000.00\",2.50,0,stale,3.0\n",
            encoding="utf-8",
        )
        parsed = mod.rows_from_csv(extra, now=NOW)
        assert parsed[0]["recycle_store_amount"] == "1000.00"
        assert parsed[0]["n_old_machine"] == 3
        assert "id" not in parsed[0]
        assert parsed[0]["updated_at"] == STAMP

        fraction = root / "fraction.csv"
        fraction.write_text(
            "day,person_name,store_name,recycle_store_amount,recycle_online_amount,"
            "accessories_sales,n_accessories\n"
            "2026-11-01,示例甲,示例店,1,2,3,1.5\n",
            encoding="utf-8",
        )
        exc = must_raise(mod, lambda: mod.rows_from_csv(fraction, now=NOW))
        assert "整数" in str(exc)

    posts = []

    def upsert_transport(method, url, headers, data):
        assert method == "POST"
        assert headers["apikey"] == "test-service-role"
        assert headers["Prefer"] == mod.PREFER_UPSERT
        assert "test-service-role" not in url
        assert url == (
            "https://example.supabase.co/rest/v1/daily_person_store_sales"
            "?on_conflict=day,person_name,store_name"
        )
        body = json.loads(data.decode("utf-8"))
        posts.append(body)
        return 201, b""

    summary = mod.seed_csv(
        FIXTURE, month="2026-11",
        env={
            "ALLOW_DAILY_SALES_SEED": "true",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role",
        },
        apply=True, transport=upsert_transport, now=NOW, batch_size=2)
    assert summary["wrote"] is True
    assert [len(batch) for batch in posts] == [2, 1]
    assert posts[0][0]["day"] == "2026-11-01"
    assert all("id" not in row for batch in posts for row in batch)

    def fail_second(method, url, headers, data):
        posts.append(method)
        if len(posts) == 1:
            return 201, b""
        return 500, b"secret-body"

    posts.clear()
    exc = must_raise(mod, lambda: mod.seed_csv(
        FIXTURE, month="2026-11",
        env={
            "ALLOW_DAILY_SALES_SEED": "true",
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role",
        },
        apply=True, transport=fail_second, now=NOW, batch_size=2))
    assert "HTTP 500" in str(exc)
    assert "secret-body" not in str(exc)

    missing = cli(["daily", "--month", "2026-11"])
    assert missing.returncode == 1
    assert missing.stdout == ""
    assert "SUPABASE_URL" in missing.stderr

    dry = cli([
        "seed", "--csv", str(FIXTURE), "--month", "2026-11", "--apply",
    ], extra_env={"ALLOW_APPLY_REALTIME_DRAFT": "true"})
    assert dry.returncode == 0, dry.stderr
    payload = json.loads(dry.stdout)
    assert payload["wrote"] is False and payload["gate_open"] is False
    assert payload["row_count"] == 3
    assert payload["totals"]["accessories_sales"] == "1.72"

    no_key = cli([
        "seed", "--csv", str(FIXTURE), "--month", "2026-11", "--apply",
    ], extra_env={mod.ENV_GATE: "true"})
    assert no_key.returncode == 1
    assert "SUPABASE_SERVICE_ROLE_KEY" in no_key.stderr
    assert "true" not in no_key.stdout

    assert CONFIG.read_bytes() == watched["config"]
    assert AUTO_FETCH.read_bytes() == watched["auto"]
    assert (REPO / "scripts" / "apply_realtime_payroll_draft.py").read_bytes() == watched["apply"]
    assert (REPO / "scripts" / "fetch_jobs.py").read_bytes() == watched["fetch"]
    assert snapshot(REPO / "scripts" / "payroll") == watched["payroll"]
    assert snapshot(REPO / "supabase") == watched["supabase"]
    assert snapshot(REPO / "docs" / "fetch-pipeline") == watched["pipeline"]
    print("PASS daily-person-store-sales smoke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
