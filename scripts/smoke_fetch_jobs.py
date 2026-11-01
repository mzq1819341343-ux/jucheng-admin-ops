#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取任务文件态冒烟。

覆盖：scope all / accessories、空月、有记录、queued→running→success/failed。
用临时目录。不连库，不写桌面，不读 2026-10 样本，不改 UPLOAD / APPLY。
"""

import importlib.util
import json
import os
import subprocess
import sys
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


def cli(args, jobs_dir):
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["FETCH_JOBS_DIR"] = str(jobs_dir)
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
    mod = load_mod()
    assert mod.ALLOW_UPLOAD is False
    assert mod.ALLOW_APPLY is False
    assert mod.DRY_ROW_COUNTS == {"old_machine": 2, "accessories": 1}
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["old_machine"]["row_count"] == 2
    assert fixture["accessories"]["row_count"] == 1

    source = SCRIPT.read_text(encoding="utf-8")
    for token in ("urlopen", "urllib", "requests", "http.client", "socket.create_connection",
                  "salary_realtime_draft", "supabase"):
        assert token not in source, token

    config_before = CONFIG.read_bytes()
    auto_before = AUTO_FETCH.read_bytes()
    supabase_before = snapshot(REPO / "supabase")
    pipeline_before = snapshot(REPO / "docs" / "fetch-pipeline")
    config = json.loads(config_before)
    assert config["UPLOAD"] is False and config["APPLY"] is False
    assert b"ALLOW_UPLOAD_PHASE = False" in auto_before

    import tempfile
    with tempfile.TemporaryDirectory(prefix="fetch_jobs_smoke_") as tmp:
        root = Path(tmp)
        cases_path = root / "cases.json"
        run_library(mod, root)
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
