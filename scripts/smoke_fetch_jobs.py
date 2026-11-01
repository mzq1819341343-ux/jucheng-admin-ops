#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取任务冒烟。

覆盖文件缓存，以及用假 PostgREST 走 claim→complete、dry、accessories 缺
old_machine、latest。不连生产，不写桌面，不读 2026-10 样本，不改 UPLOAD / APPLY。
"""

import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "fetch_jobs.py"
PARSE = REPO / "scripts" / "fetch_jobs_parse_check.mjs"
FIXTURE = REPO / "scripts" / "fetch_jobs_dry_rows.json"
CONFIG = REPO / "docs" / "fetch-pipeline" / "pipeline_config.json"
AUTO_FETCH = REPO / "docs" / "fetch-pipeline" / "auto_fetch.py"
TZ = ZoneInfo("Asia/Shanghai")
T0 = datetime(2026, 11, 2, 9, 0, tzinfo=TZ)


def load_mod():
    spec = importlib.util.spec_from_file_location("fetch_jobs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_job(mod, root, job_id):
    return json.loads(mod._job_path(root, job_id).read_text(encoding="utf-8"))


def web_parse(cases):
    path = cases_path
    path.write_text(json.dumps({"cases": cases}, ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run(
        ["node", str(PARSE), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def cli(args, jobs_dir, extra_env=None):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["FETCH_JOBS_DIR"] = str(jobs_dir)
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )
    return proc


def snapshot(root):
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


def main():
    global cases_path
    for key in (
        "SUPABASE_URL", "NEXT_PUBLIC_SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_ROLE",
        "FETCH_JOBS_DB_DRY_RUN",
    ):
        os.environ.pop(key, None)
    import urllib.request

    def _no_network(*args, **kwargs):
        raise AssertionError("smoke 不应打开网络")

    urllib.request.urlopen = _no_network
    urllib.request.build_opener = _no_network

    mod = load_mod()
    assert mod.ALLOW_UPLOAD is False
    assert mod.ALLOW_APPLY is False
    assert mod.TABLE == "salary_fetch_runs"
    assert mod.DRY_ROW_COUNTS == {"old_machine": 2, "accessories": 1}
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["old_machine"]["row_count"] == 2
    assert fixture["accessories"]["row_count"] == 1

    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("urlopen", "requests", "http.client", "socket.create_connection",
                  "salary_realtime_draft", "supabase", "mhhjujgmkpkxeqexktbv"):
        assert token not in source, token
    assert "urllib.request" in source

    config_before = CONFIG.read_bytes()
    auto_before = AUTO_FETCH.read_bytes()
    supabase_before = snapshot(REPO / "supabase")
    pipeline_before = snapshot(REPO / "docs" / "fetch-pipeline")
    config = json.loads(config_before)
    assert config["UPLOAD"] is False and config["APPLY"] is False
    assert b"ALLOW_UPLOAD_PHASE = False" in auto_before
    assert list((REPO / "supabase").glob("*017*")) == []
    assert list((REPO / "supabase").glob("*fetch_run*")) == []

    import tempfile
    with tempfile.TemporaryDirectory(prefix="fetch_jobs_smoke_") as tmp:
        root = Path(tmp)
        cases_path = root / "cases.json"
        run_library(mod, root)
        run_db(mod, root / "db")
        run_cli(root / "cli")
        run_web_vectors()

    assert CONFIG.read_bytes() == config_before
    assert AUTO_FETCH.read_bytes() == auto_before
    assert snapshot(REPO / "supabase") == supabase_before
    assert snapshot(REPO / "docs" / "fetch-pipeline") == pipeline_before
    assert list(REPO.rglob("fj_*.json")) == []
    print("PASS fetch-jobs smoke")
    return 0


def run_library(mod, root):
    desktop = Path.home() / "Desktop" / "05_Muse督工" / "fetch_jobs"
    existed = desktop.exists()
    old_env = os.environ.pop("FETCH_JOBS_DIR", None)
    try:
        assert mod.resolve_jobs_dir() == desktop
        assert desktop.exists() is existed
    finally:
        if old_env is not None:
            os.environ["FETCH_JOBS_DIR"] = old_env

    missing = root / "missing"
    assert mod.latest_payload("2099-01", jobs_dir=missing) == {"latest": None}
    assert not missing.exists()

    for month, scope in (("2026-13", "all"), ("2026-00", "all"), ("2026-11", "old"), ("202611", "all")):
        try:
            mod.create_job(month, scope, jobs_dir=missing)
        except mod.JobError as exc:
            assert str(exc) == "月份或范围不正确"
        else:
            raise AssertionError("应当拒绝 %s %s" % (month, scope))
    assert not missing.exists()

    empty = mod.latest_payload("2026-11", jobs_dir=root / "jobs")
    assert empty == {"latest": None}

    all_id = mod.create_job(
        "2026-11", "all", jobs_dir=root / "jobs", now=T0, 
    )["job_id"]
    assert set(mod.create_job("2026-12", "accessories", jobs_dir=root / "side", now=T0)) == {"job_id"}
    queued = read_job(mod, root / "jobs", all_id)
    assert queued["status"] == "queued"
    assert queued["scope"] == "all"
    assert queued["upload"] is False and queued["apply"] is False and queued["login"] is False
    assert queued["dry"] is False
    assert queued["old_machine"]["status"] == "pending"
    assert queued["accessories"]["status"] == "pending"
    assert queued["entry_url"] == mod.ENTRY_URL

    acc_id = mod.create_job(
        "2026-11", "accessories", jobs_dir=root / "jobs", now=T0 + timedelta(minutes=5),
    )["job_id"]
    latest = mod.latest_payload("2026-11", jobs_dir=root / "jobs")
    assert latest["latest"]["job_id"] == acc_id
    assert "old_machine" not in latest["latest"]
    assert latest["latest"]["accessories"] == {"status": "pending"}
    assert "finished_at" not in latest["latest"]
    assert mod.latest_payload("2099-01", jobs_dir=root / "jobs") == {"latest": None}

    before = (root / "jobs" / ("%s.json" % all_id)).read_bytes()
    refused, code = mod.run_once(jobs_dir=root / "jobs", job_id=all_id, dry=False)
    assert code == 2 and refused["ok"] is False and refused["claimed"] is False
    assert refused["login"] is False and refused["apply"] is False and refused["upload"] is False
    assert "云端 IP 会被拦" in refused["error"]
    assert (root / "jobs" / ("%s.json" % all_id)).read_bytes() == before

    claimed = mod.claim_job(all_id, jobs_dir=root / "jobs", now=T0 + timedelta(minutes=10), claimer="smoke")
    assert claimed["status"] == "running"
    running = read_job(mod, root / "jobs", all_id)
    assert running["status"] == "running"
    assert running["claimed_by"] == "smoke"
    assert running["started_at"]
    assert running["finished_at"] is None
    assert running["old_machine"]["status"] == "running"
    assert running["accessories"]["status"] == "running"
    assert (root / "jobs" / "locks" / ("%s.lock" % all_id)).is_file()
    try:
        mod.claim_job(all_id, jobs_dir=root / "jobs", claimer="other")
    except mod.JobError as exc:
        assert "认领" in str(exc)
    else:
        raise AssertionError("重复认领应当失败")
    assert read_job(mod, root / "jobs", all_id)["status"] == "running"

    # 先下发的 all 还在跑，页面仍看后下发的配件任务。
    assert mod.latest_payload("2026-11", jobs_dir=root / "jobs")["latest"]["job_id"] == acc_id

    try:
        mod.complete_job(all_id, {"apply": True, "old_machine": {"ok": True, "row_count": 1}}, jobs_dir=root / "jobs")
    except mod.JobError as exc:
        assert str(exc) == "禁止 UPLOAD / APPLY"
    else:
        raise AssertionError("APPLY 应当被拒绝")
    assert read_job(mod, root / "jobs", all_id)["status"] == "running"

    done = mod.complete_job(
        all_id,
        {
            "old_machine": {"ok": True, "rows": 3},
            "accessories": {"status": "success", "count": 4},
        },
        jobs_dir=root / "jobs",
        now=T0 + timedelta(minutes=11),
        dry=False,
    )
    assert done["status"] == "success" and done["dry"] is False
    assert done["login"] is False and done["apply"] is False
    finished = read_job(mod, root / "jobs", all_id)
    assert finished["status"] == "success"
    assert finished["dry"] is False
    assert finished["old_machine"]["row_count"] == 3
    assert finished["old_machine"]["message"] is None
    assert finished["accessories"]["row_count"] == 4
    assert finished["finished_at"]
    assert not (root / "jobs" / "locks" / ("%s.lock" % all_id)).exists()
    # 完成时间更晚，创建时间仍早于配件任务，latest 还是配件。
    assert mod.latest_payload("2026-11", jobs_dir=root / "jobs")["latest"]["job_id"] == acc_id

    failed_run, code = mod.run_once(
        jobs_dir=root / "jobs",
        job_id=acc_id,
        dry=True,
        fail=True,
        error="空文件",
        now=T0 + timedelta(minutes=20),
        claimer="smoke",
    )
    assert code == 0 and failed_run["claimed"] is True and failed_run["status"] == "failed"
    assert failed_run["dry"] is True and failed_run["login"] is False
    acc = read_job(mod, root / "jobs", acc_id)
    assert acc["status"] == "failed"
    assert acc["old_machine"] is None
    assert acc["accessories"]["status"] == "failed"
    assert acc["accessories"]["row_count"] == 0
    assert acc["accessories"]["error"] == "空文件"
    acc_latest = mod.latest_payload("2026-11", jobs_dir=root / "jobs")
    assert acc_latest["latest"]["job_id"] == acc_id
    assert acc_latest["latest"]["finished_at"] == acc_latest["latest"]["at"]
    assert "old_machine" not in acc_latest["latest"]
    assert acc_latest["latest"]["accessories"]["error"] == "空文件"

    partial_id = mod.create_job(
        "2026-11", "all", jobs_dir=root / "jobs", now=T0 + timedelta(minutes=30),
    )["job_id"]
    partial, code = mod.run_once(
        jobs_dir=root / "jobs",
        job_id=partial_id,
        dry=True,
        fail_leg="accessories",
        error="空文件",
        now=T0 + timedelta(minutes=31),
        claimer="smoke",
    )
    assert code == 0 and partial["status"] == "failed"
    partial_job = read_job(mod, root / "jobs", partial_id)
    assert partial_job["old_machine"]["status"] == "success"
    assert partial_job["old_machine"]["row_count"] == 2
    assert partial_job["old_machine"]["message"] == mod.DRY_MESSAGE
    assert partial_job["accessories"]["status"] == "failed"
    assert partial_job["accessories"]["error"] == "空文件"

    custom = root / "custom_rows.json"
    custom.write_text(json.dumps({
        "old_machine": {"row_count": 4},
        "accessories": 5,
    }), encoding="utf-8")
    custom_id = mod.create_job(
        "2026-12", "all", jobs_dir=root / "jobs", now=T0 + timedelta(hours=1),
    )["job_id"]
    custom_run, code = mod.run_once(
        jobs_dir=root / "jobs",
        job_id=custom_id,
        dry=True,
        fixture=custom,
        now=T0 + timedelta(hours=1, minutes=1),
        claimer="smoke",
    )
    assert code == 0 and custom_run["status"] == "success"
    custom_job = read_job(mod, root / "jobs", custom_id)
    assert custom_job["old_machine"]["row_count"] == 4
    assert custom_job["accessories"]["row_count"] == 5
    assert custom_job["started_at"]
    assert "2026-10" not in json.dumps(custom_job)
    assert "458" not in json.dumps(custom_job)

    poisoned_id = "fj_20261102T120000_abcdef"
    poisoned = {
        "job_id": poisoned_id,
        "month": "2026-12",
        "scope": "all",
        "status": "queued",
        "created_at": "2026-11-02T14:00:00+08:00",
        "updated_at": "2026-11-02T14:00:00+08:00",
        "started_at": None,
        "finished_at": None,
        "upload": True,
        "apply": False,
        "login": False,
        "old_machine": mod._blank_leg(),
        "accessories": mod._blank_leg(),
    }
    (root / "jobs" / ("%s.json" % poisoned_id)).write_text(
        json.dumps(poisoned, ensure_ascii=False), encoding="utf-8")
    clean_id = mod.create_job(
        "2026-12", "accessories", jobs_dir=root / "jobs", now=T0 + timedelta(hours=4),
    )["job_id"]
    skipped, code = mod.run_once(
        jobs_dir=root / "jobs", dry=True, now=T0 + timedelta(hours=4, minutes=1), claimer="smoke",
    )
    assert code == 0 and skipped["job_id"] == clean_id
    assert poisoned_id in skipped["skipped_forbidden"]
    assert json.loads((root / "jobs" / ("%s.json" % poisoned_id)).read_text(encoding="utf-8"))["upload"] is True
    assert json.loads((root / "jobs" / ("%s.json" % poisoned_id)).read_text(encoding="utf-8"))["status"] == "queued"

    (root / "jobs" / "notes.json").write_text("{", encoding="utf-8")
    (root / "jobs" / "fj_not_a_job.json").write_text("[]", encoding="utf-8")
    assert mod.latest_payload("2026-12", jobs_dir=root / "jobs")["latest"]["job_id"] == poisoned_id

    none, code = mod.run_once(jobs_dir=root / "empty", dry=True)
    assert code == 0 and none["claimed"] is False
    assert not (root / "empty").exists()

    cases = [
        {
            "id": "create",
            "op": "create",
            "status": 200,
            "json": True,
            "body": {"job_id": all_id},
        },
        {
            "id": "empty",
            "op": "latest",
            "status": 200,
            "json": True,
            "body": {"latest": None},
        },
        {
            "id": "pending",
            "op": "latest",
            "status": 200,
            "json": True,
            "body": {
                "latest": {
                    "job_id": all_id,
                    "at": queued["created_at"],
                    "old_machine": {"status": "pending"},
                    "accessories": {"status": "pending"},
                }
            },
        },
        {
            "id": "failed_acc",
            "op": "latest",
            "status": 200,
            "json": True,
            "body": acc_latest,
        },
        {
            "id": "partial",
            "op": "latest",
            "status": 200,
            "json": True,
            "body": mod.latest_payload("2026-11", jobs_dir=root / "jobs"),
        },
        {
            "id": "bad_status",
            "op": "create",
            "status": 400,
            "json": True,
            "body": {"error": "月份或范围不正确"},
        },
        {
            "id": "bad_wrapped_as_200",
            "op": "create",
            "status": 200,
            "json": True,
            "body": {"error": "月份或范围不正确"},
        },
        {
            "id": "not_json",
            "op": "latest",
            "status": 400,
            "json": False,
            "body": None,
        },
    ]
    # 上面 pending 用的是构造体。再解析一次真实的排队响应形状。
    parsed = web_parse(cases)
    assert parsed["create"] == {"ok": True, "jobId": all_id}
    assert parsed["empty"]["ok"] is True and parsed["empty"]["latest"]["empty"] is True
    assert parsed["pending"]["latest"]["empty"] is False
    assert parsed["pending"]["usedLabel"] == "进行中 · 行数未返回"
    assert parsed["pending"]["latest"]["used"]["ok"] is None
    assert parsed["failed_acc"]["latest"]["jobId"] == acc_id
    assert parsed["failed_acc"]["accessoriesLabel"] == "失败 · 0 行（空文件）"
    assert parsed["failed_acc"]["usedLabel"] == "未返回 · 行数未返回"
    assert parsed["failed_acc"]["latest"]["at"] == acc["finished_at"]
    assert "成功 · 2 行（占位，未登录加盟后台）" == parsed["partial"]["usedLabel"]
    assert parsed["partial"]["accessoriesLabel"] == "失败 · 0 行（空文件）"
    assert parsed["partial"]["latest"]["jobId"] == partial_id
    assert parsed["bad_status"] == {"ok": False, "message": "月份或范围不正确"}
    assert parsed["bad_wrapped_as_200"] == {"ok": False, "message": "下发没有返回任务号"}
    assert parsed["not_json"]["message"] == "任务接口未就绪"


def run_cli(root):
    root.mkdir(parents=True)
    empty = cli(["latest", "--month", "2099-01"], root)
    assert empty.returncode == 0, empty.stderr
    assert json.loads(empty.stdout) == {"latest": None}
    assert empty.stderr == ""

    bad = cli(["create", "--month", "2026-11", "--scope", "both"], root)
    assert bad.returncode == 2
    assert json.loads(bad.stdout)["error"] == "参数不正确"

    refused_month = cli(["create", "--month", "2026-13", "--scope", "all"], root)
    assert refused_month.returncode == 2
    assert json.loads(refused_month.stdout)["error"] == "月份或范围不正确"
    assert list(root.glob("fj_*.json")) == []

    created = cli(["create", "--month", "2026-11", "--scope", "all"], root)
    assert created.returncode == 0, created.stderr
    body = json.loads(created.stdout)
    assert set(body) == {"job_id"}
    job_id = body["job_id"]
    preview = json.loads(created.stderr)
    assert preview["db_dry_run"] is True and preview["table"] == "salary_fetch_runs"
    preview_row = preview["writes"][0]["row"]
    assert preview["writes"][0]["op"] == "insert"
    assert preview_row["status"] == "queued" and preview_row["dry"] is False
    assert preview_row["upload"] is False and preview_row["apply"] is False
    assert preview_row["login"] is False
    assert preview_row["old_machine"]["status"] == "pending"
    assert "entry_url" not in preview_row and "created_by" not in preview_row

    other = root / "other"
    other.mkdir()
    flagged = cli(
        ["--jobs-dir", str(other), "create", "--month", "2026-11", "--scope", "accessories"],
        root,
    )
    assert flagged.returncode == 0, flagged.stderr
    assert list(other.glob("fj_*.json"))
    assert len(list(root.glob("fj_*.json"))) == 1

    bare = cli(["run"], root)
    assert bare.returncode == 2
    payload = json.loads(bare.stdout)
    assert payload["claimed"] is False and payload["apply"] is False
    assert json.loads((root / ("%s.json" % job_id)).read_text(encoding="utf-8"))["status"] == "queued"

    dry = cli(["run", "--dry", "--fixture", str(FIXTURE)], root)
    assert dry.returncode == 0, dry.stderr
    dry_body = json.loads(dry.stdout)
    assert dry_body["job_id"] == job_id and dry_body["status"] == "success" and dry_body["dry"] is True
    stored = json.loads((root / ("%s.json" % job_id)).read_text(encoding="utf-8"))
    assert stored["old_machine"]["row_count"] == 2
    assert stored["accessories"]["row_count"] == 1
    assert stored["upload"] is False and stored["apply"] is False and stored["login"] is False
    latest = json.loads(cli(["latest", "--month", "2026-11"], root).stdout)
    assert latest["latest"]["job_id"] == job_id
    assert latest["latest"]["old_machine"]["row_count"] == 2

    idle = cli(["run", "--dry"], root)
    assert idle.returncode == 0
    assert json.loads(idle.stdout)["claimed"] is False

    blocked_dir = root / "blocked"
    blocked = cli(
        ["--db-dry-run", "create", "--month", "2026-11", "--scope", "accessories"],
        blocked_dir,
        {
            "SUPABASE_URL": "https://fetch-jobs.invalid",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role",
        },
    )
    assert blocked.returncode == 0, blocked.stderr
    blocked_preview = json.loads(blocked.stderr)
    assert blocked_preview["db_dry_run"] is True
    blocked_row = blocked_preview["writes"][0]["row"]
    assert blocked_row["scope"] == "accessories"
    assert blocked_row["old_machine"] is None
    assert blocked_row["accessories"]["status"] == "pending"
    assert blocked_row["upload"] is False and blocked_row["apply"] is False
    assert blocked_row["login"] is False
    assert "test-service-role" not in blocked.stdout
    assert "test-service-role" not in blocked.stderr
    assert "fetch-jobs.invalid" not in blocked.stdout


def _eq(qs, key):
    raw = qs[key][0]
    assert raw.startswith("eq."), raw
    return raw[3:]


class FakeRest:
    """内存里的 PostgREST。只接受 salary_fetch_runs，不打开套接字。"""

    def __init__(self):
        self.rows = {}
        self.calls = []

    def __call__(self, method, url, headers, payload):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": headers,
            "payload": payload,
        })
        assert url.startswith("https://example.supabase.co/rest/v1/salary_fetch_runs"), url
        assert "salary_realtime_draft" not in url
        assert "mhhjujgmkpkxeqexktbv" not in url
        assert "test-service-role" not in url
        assert headers.get("apikey") == "test-service-role"
        assert headers.get("Authorization") == "Bearer test-service-role"
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        body = json.loads(payload.decode("utf-8")) if payload else None
        if body is not None:
            assert body.get("upload") is False
            assert body.get("apply") is False
            assert body.get("login") is False
            assert "entry_url" not in body and "source" not in body
        if method in ("POST", "PATCH"):
            assert headers.get("Prefer") == "return=representation"
        if method == "POST":
            if body["job_id"] in self.rows:
                return 409, b"{}"
            stored = dict(body)
            self.rows[stored["job_id"]] = stored
            return 201, json.dumps([stored], ensure_ascii=False).encode("utf-8")
        if method == "PATCH":
            job_id = _eq(qs, "job_id")
            status = _eq(qs, "status") if "status" in qs else None
            row = self.rows.get(job_id)
            if row is None or (status and row.get("status") != status):
                return 200, b"[]"
            row.update(body)
            return 200, json.dumps([row], ensure_ascii=False).encode("utf-8")
        if method == "GET":
            rows = list(self.rows.values())
            if "job_id" in qs:
                job_id = _eq(qs, "job_id")
                rows = [row for row in rows if row["job_id"] == job_id]
            if "month" in qs:
                month = _eq(qs, "month")
                rows = [row for row in rows if row["month"] == month]
            if "status" in qs:
                status = _eq(qs, "status")
                rows = [row for row in rows if row["status"] == status]
            order = (qs.get("order") or [""])[0]
            if "created_at.desc" in order:
                rows.sort(key=lambda row: (row.get("created_at") or "", row.get("job_id") or ""), reverse=True)
            elif "created_at.asc" in order:
                rows.sort(key=lambda row: (row.get("created_at") or "", row.get("job_id") or ""))
            limit = int((qs.get("limit") or ["100"])[0])
            return 200, json.dumps(rows[:limit], ensure_ascii=False).encode("utf-8")
        return 400, b""


def run_db(mod, root):
    fake = FakeRest()
    env = {
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "test-service-role",
    }
    jobs = root / "jobs"

    try:
        mod.create_job(
            "2026-11", "all", jobs_dir=root / "partial",
            env={"SUPABASE_URL": "https://example.supabase.co"},
        )
    except mod.JobError as exc:
        assert "SUPABASE_SERVICE_ROLE_KEY" in str(exc)
    else:
        raise AssertionError("缺少 service role 应当拒绝")
    assert not (root / "partial").exists()

    try:
        mod.create_job(
            "2026-11", "all", jobs_dir=root / "http",
            env={"SUPABASE_URL": "http://example.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "x"},
        )
    except mod.JobError as exc:
        assert "https" in str(exc)
    else:
        raise AssertionError("非 https 应当拒绝")
    assert not (root / "http").exists()

    try:
        mod.open_db(env={
            "SUPABASE_URL": "https://example.supabase.co/rest/v1",
            "SUPABASE_SERVICE_ROLE_KEY": "x",
        })
    except mod.JobError as exc:
        assert "项目根" in str(exc)
    else:
        raise AssertionError("带路径的 URL 应当拒绝")

    alias = mod.open_db(env={
        "NEXT_PUBLIC_SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE": "alias-key",
    })
    assert alias.enabled is True
    flagged = mod.open_db(env=dict(env, FETCH_JOBS_DB_DRY_RUN="true"), transport=fake)
    assert flagged.enabled is False
    calls_before = len(fake.calls)
    preview_id = mod.create_job(
        "2026-11", "all", jobs_dir=root / "preview", now=T0, db=flagged,
    )["job_id"]
    assert len(fake.calls) == calls_before
    assert flagged.previews[0]["op"] == "insert"
    assert flagged.previews[0]["row"]["job_id"] == preview_id
    assert flagged.previews[0]["row"]["upload"] is False
    assert "test-service-role" not in json.dumps(flagged.previews)
    assert (root / "preview" / ("%s.json" % preview_id)).is_file()

    poisoned = {
        "job_id": "fj_20261102T090000_bbbbbb",
        "month": "2026-11",
        "scope": "all",
        "status": "queued",
        "created_at": "2026-11-02T09:00:00+08:00",
        "updated_at": "2026-11-02T09:00:00+08:00",
        "old_machine": mod._blank_leg(),
        "accessories": mod._blank_leg(),
        "upload": True,
        "apply": False,
        "login": False,
        "dry": False,
    }
    try:
        mod.build_insert_row(poisoned)
    except mod.JobError as exc:
        assert str(exc) == "禁止 UPLOAD / APPLY"
    else:
        raise AssertionError("upload=true 不能拼进插入行")

    live = mod.open_db(env=env, transport=fake)
    all_id = mod.create_job("2026-11", "all", jobs_dir=jobs, now=T0, db=live)["job_id"]
    stored = fake.rows[all_id]
    assert stored["status"] == "queued"
    assert stored["dry"] is False
    assert stored["upload"] is False and stored["apply"] is False and stored["login"] is False
    assert stored["old_machine"]["status"] == "pending"
    assert stored["old_machine"]["row_count"] is None
    assert stored["accessories"]["status"] == "pending"
    assert stored["started_at"] is None and stored["claimed_by"] is None
    assert "entry_url" not in stored and "message" not in stored
    assert (jobs / ("%s.json" % all_id)).is_file()

    acc_id = mod.create_job(
        "2026-11", "accessories", jobs_dir=jobs, now=T0 + timedelta(minutes=5), db=live,
    )["job_id"]
    assert fake.rows[acc_id]["old_machine"] is None
    assert fake.rows[acc_id]["accessories"]["status"] == "pending"
    assert fake.rows[acc_id]["scope"] == "accessories"

    ghost_id = "fj_20261102T180000_ffffff"
    ghost = {
        "job_id": ghost_id,
        "month": "2026-11",
        "scope": "all",
        "status": "success",
        "created_at": "2026-11-02T18:00:00+08:00",
        "updated_at": "2026-11-02T18:00:00+08:00",
        "finished_at": "2026-11-02T18:00:01+08:00",
        "dry": False,
        "upload": False,
        "apply": False,
        "login": False,
        "old_machine": {"status": "success", "row_count": 9},
        "accessories": {"status": "success", "row_count": 9},
    }
    (jobs / ("%s.json" % ghost_id)).write_text(json.dumps(ghost), encoding="utf-8")
    latest = mod.latest_payload("2026-11", jobs_dir=jobs, db=live)
    assert latest["latest"]["job_id"] == acc_id
    assert "old_machine" not in latest["latest"]
    assert latest["latest"]["accessories"] == {"status": "pending"}
    assert mod.latest_payload("2099-01", jobs_dir=jobs, db=live) == {"latest": None}
    assert not (root / "missing-latest").exists()

    file_only = mod.create_job(
        "2026-11", "all", jobs_dir=root / "fileonly", now=T0, env={},
    )["job_id"]
    fallback = mod.latest_payload("2026-11", jobs_dir=root / "fileonly", env={})
    assert fallback["latest"]["job_id"] == file_only
    assert fallback["latest"]["job_id"] != acc_id

    claimed = mod.claim_job(
        all_id, jobs_dir=jobs, now=T0 + timedelta(minutes=10), claimer="smoke", db=live,
    )
    assert claimed["status"] == "running"
    assert claimed["login"] is False and claimed["apply"] is False
    running = fake.rows[all_id]
    assert running["status"] == "running"
    assert running["claimed_by"] == "smoke"
    assert running["started_at"] == "2026-11-02T09:10:00+08:00"
    assert running["finished_at"] is None
    assert running["dry"] is False
    assert running["old_machine"]["status"] == "running"
    assert running["accessories"]["status"] == "running"
    assert not (jobs / "locks").exists()
    try:
        mod.claim_job(all_id, jobs_dir=jobs, claimer="other", db=live)
    except mod.JobError as exc:
        assert "认领" in str(exc)
    else:
        raise AssertionError("重复认领应当失败")
    assert fake.rows[all_id]["status"] == "running"
    assert fake.rows[all_id]["claimed_by"] == "smoke"

    calls_before = len(fake.calls)
    try:
        mod.complete_job(
            all_id,
            {"login": True, "old_machine": {"ok": True, "row_count": 1},
             "accessories": {"ok": True, "row_count": 1}},
            jobs_dir=jobs,
            db=live,
        )
    except mod.JobError as exc:
        assert str(exc) == "禁止 UPLOAD / APPLY"
    else:
        raise AssertionError("login=true 应当被拒绝")
    assert len(fake.calls) == calls_before
    assert fake.rows[all_id]["status"] == "running"

    done = mod.complete_job(
        all_id,
        {
            "old_machine": {"ok": True, "rows": 3},
            "accessories": {"status": "success", "count": 4},
        },
        jobs_dir=jobs,
        now=T0 + timedelta(minutes=11),
        dry=False,
        db=live,
    )
    assert done["status"] == "success" and done["dry"] is False
    assert done["login"] is False and done["upload"] is False and done["apply"] is False
    finished = fake.rows[all_id]
    assert finished["status"] == "success"
    assert finished["dry"] is False
    assert finished["finished_at"] == "2026-11-02T09:11:00+08:00"
    assert finished["old_machine"] == {"status": "success", "row_count": 3}
    assert finished["accessories"] == {"status": "success", "row_count": 4}
    assert finished["upload"] is False and finished["login"] is False
    assert mod.latest_payload("2026-11", jobs_dir=jobs, db=live)["latest"]["job_id"] == acc_id

    mod.claim_job(
        acc_id, jobs_dir=jobs, now=T0 + timedelta(minutes=12), claimer="smoke", db=live,
    )
    assert fake.rows[acc_id]["old_machine"] is None
    assert fake.rows[acc_id]["accessories"]["status"] == "running"
    acc_done = mod.complete_job(
        acc_id,
        {"accessories": {"ok": True, "row_count": 1}},
        jobs_dir=jobs,
        now=T0 + timedelta(minutes=13),
        dry=True,
        db=live,
    )
    assert acc_done["status"] == "success" and acc_done["dry"] is True
    assert fake.rows[acc_id]["old_machine"] is None
    assert fake.rows[acc_id]["dry"] is True
    assert fake.rows[acc_id]["accessories"]["status"] == "success"
    assert fake.rows[acc_id]["accessories"]["row_count"] == 1
    assert fake.rows[acc_id]["accessories"]["message"] == mod.DRY_MESSAGE
    acc_latest = mod.latest_payload("2026-11", jobs_dir=jobs, db=live)
    assert acc_latest["latest"]["job_id"] == acc_id
    assert "old_machine" not in acc_latest["latest"]
    assert acc_latest["latest"]["accessories"]["row_count"] == 1
    assert acc_latest["latest"]["finished_at"] == acc_latest["latest"]["at"]
    assert mod.public_latest_from_row(fake.rows[acc_id]) == acc_latest

    try:
        mod.complete_job(
            acc_id,
            {"old_machine": {"ok": True, "row_count": 1}, "accessories": {"ok": True, "row_count": 1}},
            db=live,
        )
    except mod.JobError as exc:
        assert "running" in str(exc) or "范围" in str(exc)
    else:
        raise AssertionError("已完成的任务不能再回写")
    assert fake.rows[acc_id]["old_machine"] is None

    missing_id = mod.create_job(
        "2026-11", "all", jobs_dir=jobs, now=T0 + timedelta(minutes=30), db=live,
    )["job_id"]
    mod.claim_job(missing_id, jobs_dir=jobs, now=T0 + timedelta(minutes=31), claimer="smoke", db=live)
    missing = mod.complete_job(
        missing_id,
        {"old_machine": {"ok": True, "row_count": 2}},
        jobs_dir=jobs,
        now=T0 + timedelta(minutes=32),
        db=live,
    )
    assert missing["status"] == "failed"
    missed = fake.rows[missing_id]["accessories"]
    assert missed["status"] == "failed"
    assert missed["error"] == "未回写"
    assert missed["row_count"] == 0
    assert fake.rows[missing_id]["old_machine"]["status"] == "success"
    assert fake.rows[missing_id]["status"] == "failed"

    orphan_id = "fj_20261102T150000_abc123"
    fake.rows[orphan_id] = {
        "job_id": orphan_id,
        "month": "2026-11",
        "scope": "accessories",
        "status": "queued",
        "created_at": "2026-11-02T15:00:00+08:00",
        "updated_at": "2026-11-02T15:00:00+08:00",
        "started_at": None,
        "finished_at": None,
        "claimed_by": None,
        "dry": False,
        "upload": False,
        "apply": False,
        "login": False,
        "old_machine": None,
        "accessories": {"status": "pending", "row_count": None},
    }
    assert not (jobs / ("%s.json" % orphan_id)).exists()
    mod.claim_job(orphan_id, jobs_dir=jobs, now=T0 + timedelta(hours=6), claimer="mac", db=live)
    assert fake.rows[orphan_id]["status"] == "running"
    assert fake.rows[orphan_id]["claimed_by"] == "mac"
    assert fake.rows[orphan_id]["old_machine"] is None
    assert (jobs / ("%s.json" % orphan_id)).is_file()
    mod.complete_job(
        orphan_id,
        {"accessories": {"ok": False, "row_count": 0, "error": "空文件"}},
        jobs_dir=jobs,
        now=T0 + timedelta(hours=6, seconds=1),
        db=live,
    )
    assert fake.rows[orphan_id]["status"] == "failed"
    assert fake.rows[orphan_id]["old_machine"] is None
    assert fake.rows[orphan_id]["accessories"]["error"] == "空文件"
    assert fake.rows[orphan_id]["accessories"]["status"] == "failed"

    early_id = mod.create_job(
        "2026-12", "all", jobs_dir=jobs, now=T0 + timedelta(hours=1), db=live,
    )["job_id"]
    later_id = mod.create_job(
        "2026-12", "accessories", jobs_dir=jobs, now=T0 + timedelta(hours=2), db=live,
    )["job_id"]
    poisoned_id = "fj_20261102T120000_abcdef"
    fake.rows[poisoned_id] = {
        "job_id": poisoned_id,
        "month": "2026-12",
        "scope": "all",
        "status": "queued",
        "created_at": "2026-11-02T08:00:00+08:00",
        "updated_at": "2026-11-02T08:00:00+08:00",
        "dry": False,
        "upload": True,
        "apply": False,
        "login": False,
        "old_machine": {"status": "pending", "row_count": None},
        "accessories": {"status": "pending", "row_count": None},
    }
    dry_run, code = mod.run_once(
        jobs_dir=jobs, dry=True, now=T0 + timedelta(hours=3), claimer="smoke", db=live,
    )
    assert code == 0 and dry_run["claimed"] is True and dry_run["dry"] is True
    assert dry_run["job_id"] == early_id
    assert dry_run["status"] == "success"
    assert dry_run["login"] is False
    assert poisoned_id in dry_run["skipped_forbidden"]
    assert fake.rows[poisoned_id]["status"] == "queued"
    assert fake.rows[poisoned_id]["upload"] is True
    assert fake.rows[early_id]["dry"] is True
    assert fake.rows[early_id]["status"] == "success"
    assert fake.rows[early_id]["old_machine"]["row_count"] == 2
    assert fake.rows[early_id]["accessories"]["row_count"] == 1
    assert fake.rows[early_id]["old_machine"]["message"] == mod.DRY_MESSAGE
    assert fake.rows[early_id]["login"] is False
    assert fake.rows[later_id]["status"] == "queued"
    dec_latest = mod.latest_payload("2026-12", jobs_dir=jobs, db=live)
    assert dec_latest["latest"]["job_id"] == later_id
    assert "old_machine" not in dec_latest["latest"]

    same_time = "2026-11-02T16:00:00+08:00"
    low_id = "fj_20261102T160000_00000a"
    high_id = "fj_20261102T160000_00000b"
    for job_id in (low_id, high_id):
        fake.rows[job_id] = {
            "job_id": job_id,
            "month": "2026-08",
            "scope": "accessories",
            "status": "queued",
            "created_at": same_time,
            "updated_at": same_time,
            "dry": False,
            "upload": False,
            "apply": False,
            "login": False,
            "old_machine": None,
            "accessories": {"status": "pending", "row_count": None},
        }
    tie = mod.latest_payload("2026-08", jobs_dir=jobs, db=live)
    assert tie["latest"]["job_id"] == high_id

    def fail_transport(method, url, headers, payload):
        return 500, b'{"message":"nope"}'

    try:
        mod.create_job(
            "2026-11", "all", jobs_dir=root / "err", now=T0, env=env, transport=fail_transport,
        )
    except mod.JobError as exc:
        assert str(exc) == "写库失败 HTTP 500"
    else:
        raise AssertionError("HTTP 500 应当失败")
    assert list((root / "err").glob("fj_*.json")) == []

    def down(*args):
        raise OSError("down")

    try:
        mod.create_job(
            "2026-11", "all", jobs_dir=root / "down", now=T0, env=env, transport=down,
        )
    except mod.JobError as exc:
        assert str(exc) == "写库连接失败"
    else:
        raise AssertionError("连接失败应当拒绝")
    assert not (root / "down").exists()

    def lose_race(method, url, headers, payload):
        if method == "PATCH":
            return 200, b"[]"
        return fake(method, url, headers, payload)

    race = mod.open_db(env=env, transport=lose_race)
    race_id = mod.create_job(
        "2026-11", "all", jobs_dir=jobs, now=T0 + timedelta(hours=7), db=race,
    )["job_id"]
    try:
        mod.claim_job(race_id, jobs_dir=jobs, claimer="smoke", db=race)
    except mod.JobError as exc:
        assert str(exc) == "任务已被认领"
    else:
        raise AssertionError("条件更新落空应当算被认领")
    assert fake.rows[race_id]["status"] == "queued"

    bodies = []
    for call in fake.calls:
        assert "/rest/v1/salary_fetch_runs" in call["url"]
        assert "salary_realtime_draft" not in call["url"]
        if call["payload"]:
            bodies.append(json.loads(call["payload"].decode("utf-8")))
    assert any(body.get("claimed_by") == "smoke" and body.get("status") == "running" for body in bodies)
    assert any(body.get("dry") is True and body.get("status") == "success" for body in bodies)
    assert all(body.get("upload") is False and body.get("apply") is False and body.get("login") is False
               for body in bodies)
    assert any("order=created_at.desc,job_id.desc" in call["url"] and "limit=1" in call["url"]
               for call in fake.calls)
    assert any("status=eq.queued" in call["url"] and call["method"] == "PATCH" for call in fake.calls)
    assert any("status=eq.running" in call["url"] and call["method"] == "PATCH" for call in fake.calls)
    assert any("order=created_at.asc,job_id.asc" in call["url"] for call in fake.calls)
    assert all(row.get("month") != "2026-10" for row in fake.rows.values())
    query = mod.latest_rest_query("2026-11")
    assert "month=eq.2026-11" in query
    assert "order=created_at.desc,job_id.desc" in query
    assert query.startswith("select=")
    assert "limit=1" in query


def run_web_vectors():
    cases = [
        {
            "id": "web_rows",
            "op": "latest",
            "status": 200,
            "json": True,
            "body": {
                "job_id": "job-9",
                "finished_at": "2026-11-01T10:00:00+08:00",
                "old_machine": {"status": "success", "row_count": 12},
                "accessories": {"ok": False, "rows": 0, "error": "空文件"},
            },
        },
        {
            "id": "web_null",
            "op": "latest",
            "status": 200,
            "json": True,
            "body": {"latest": None},
        },
        {
            "id": "web_running",
            "op": "latest",
            "status": 200,
            "json": True,
            "body": {"at": "2026-11-01T10:00:00Z", "used": {"status": "running", "rows": 1}},
        },
        {
            "id": "web_create",
            "op": "create",
            "status": 202,
            "json": True,
            "body": {"jobId": "job-2"},
        },
    ]
    parsed = web_parse(cases)
    assert parsed["web_rows"]["usedLabel"] == "成功 · 12 行"
    assert parsed["web_rows"]["accessoriesLabel"] == "失败 · 0 行（空文件）"
    assert parsed["web_rows"]["latest"]["empty"] is False
    assert parsed["web_null"]["latest"]["empty"] is True
    assert parsed["web_running"]["usedLabel"] == "进行中 · 1 行"
    assert parsed["web_running"]["latest"]["used"]["ok"] is None
    assert parsed["web_create"] == {"ok": True, "jobId": "job-2"}


if __name__ == "__main__":
    sys.exit(main())
