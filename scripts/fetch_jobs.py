#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取任务队列。真源是表 salary_fetch_runs，本地 JSON 只是缓存。

工资页只下发任务、只读上次结果。Route 在 web 仓。本模块给 Mac 上的
runner 做 claim / complete：有 SUPABASE_URL 与 service role 时写库；
没有凭据时不连库，latest 回退读文件，写操作可把将写入的行打到标准错误。
这里不登录加盟后台，不写工资表，不把 UPLOAD / APPLY / login 写成 true。

契约：docs/契约_fetch-jobs.md
前端解析：jucheng-admin-web lib/fetch-jobs.ts

  python3 scripts/fetch_jobs.py create --month 2026-11 --scope all
  python3 scripts/fetch_jobs.py latest --month 2026-11
  python3 scripts/fetch_jobs.py run --dry
  python3 scripts/fetch_jobs.py claim --job-id <job_id>
  python3 scripts/fetch_jobs.py complete --job-id <job_id> --results '<json>'
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
ENTRY_URL = "https://franchise-admin.aihuishou.com/"
SOURCE = "franchise-admin"

# 阶段锁。本文件没有上传分支；常量若被改成 true，写入直接拒绝。
ALLOW_UPLOAD = False
ALLOW_APPLY = False

# 占位行数。不是任何月份的生产导出。
DRY_ROW_COUNTS = {"old_machine": 2, "accessories": 1}
DRY_MESSAGE = "占位，未登录加盟后台"
FAIL_MESSAGE = "占位失败，未登录加盟后台"

SCOPES = ("all", "accessories")
LEG_NAMES = ("old_machine", "accessories")
LEG_STATUSES = ("pending", "running", "success", "failed")
JOB_STATUSES = ("queued", "running", "success", "failed")
JOB_ID_RE_TEXT = r"^fj_\d{8}T\d{6}_[0-9a-f]{6}$"
TABLE = "salary_fetch_runs"
SELECT_COLUMNS = (
    "job_id,month,scope,status,started_at,finished_at,"
    "old_machine,accessories,claimed_by,dry,upload,apply,login,"
    "created_at,updated_at"
)

REFUSE_REAL = (
    "实抓须在 Mac 上用保险柜登录加盟后台 "
    + ENTRY_URL
    + " 。云端 IP 会被拦。本进程不认领、不回写、不登录，也不 APPLY。"
    "占位回写请加 --dry。"
)

_JOB_ID_RE = re.compile(JOB_ID_RE_TEXT)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

_GATE_KEYS = {"apply", "upload", "login"}
_SECRET_KEYS = {
    "password", "passwd", "cookie", "cookies", "set-cookie",
    "token", "authorization", "secret", "credential", "credentials",
}

__all__ = [
    "JobError",
    "TABLE",
    "resolve_jobs_dir",
    "create_job",
    "latest_payload",
    "claim_job",
    "complete_job",
    "run_once",
    "open_db",
    "build_insert_row",
    "build_claim_patch",
    "build_complete_patch",
    "latest_rest_query",
    "public_latest_from_row",
    "main",
]


class JobError(Exception):
    def __init__(self, message, exit_code=2):
        super().__init__(message)
        self.exit_code = exit_code


def resolve_jobs_dir(jobs_dir=None):
    """任务目录。参数优先，其次 FETCH_JOBS_DIR，否则 Mac 桌面约定路径。

    只返回路径，不创建目录。
    """
    if jobs_dir is not None and str(jobs_dir).strip() != "":
        return Path(jobs_dir).expanduser()
    env = os.environ.get("FETCH_JOBS_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / "Desktop" / "05_Muse督工" / "fetch_jobs"


def create_job(
    month, scope, jobs_dir=None, now=None, *,
    db=None, env=None, transport=None, db_dry_run=None,
):
    """写入一条 queued 任务，返回 {"job_id": ...}。月份或范围不合法时抛 JobError。

    有库凭据时同时插入 salary_fetch_runs。没有凭据时只写文件缓存。
    """
    _guard_phase()
    month = _require_month(month)
    scope = _require_scope(scope)
    db = _coerce_db(db, env, transport, db_dry_run)
    root = resolve_jobs_dir(jobs_dir)
    stamp = _coerce_now(now)
    created = _format_time(stamp)
    job_id, path = _allocate_id(root, stamp)
    job = {
        "job_id": job_id,
        "month": month,
        "scope": scope,
        "status": "queued",
        "created_at": created,
        "updated_at": created,
        "started_at": None,
        "finished_at": None,
        "claimed_by": None,
        "dry": False,
        "entry_url": ENTRY_URL,
        "source": SOURCE,
        "old_machine": _blank_leg() if scope == "all" else None,
        "accessories": _blank_leg(),
        "upload": False,
        "apply": False,
        "login": False,
    }
    row = build_insert_row(job)
    if db.enabled:
        db.insert(row)
        _cache_job(jobs_dir, job)
    else:
        db.note("insert", row)
        _write_job(path, job)
    return {"job_id": job_id}


def latest_payload(
    month, jobs_dir=None, *,
    db=None, env=None, transport=None, db_dry_run=None,
):
    """该月 created_at 最新的一条，包成 {"latest": ...}。没有记录时 {"latest": null}。

    有库凭据时只读表，不看文件。没有凭据时读缓存，且不创建目录。
    排队中的任务也算最近一次。
    """
    month = _require_month(month)
    db = _coerce_db(db, env, transport, db_dry_run)
    if db.enabled:
        return public_latest_from_row(db.latest(month))
    root = resolve_jobs_dir(jobs_dir)
    chosen = None
    for job in _load_all(root):
        if job.get("month") != month:
            continue
        if chosen is None or _recency_key(job) > _recency_key(chosen):
            chosen = job
    if chosen is None:
        return {"latest": None}
    return {"latest": _public_job(chosen)}


def claim_job(
    job_id, jobs_dir=None, now=None, claimer=None, *,
    db=None, env=None, transport=None, db_dry_run=None,
):
    """queued → running。有库时用 status=queued 的条件更新当锁。"""
    _guard_phase()
    job_id = _require_job_id(job_id)
    db = _coerce_db(db, env, transport, db_dry_run)
    who = _claimer_name(claimer)
    stamp = _format_time(_coerce_now(now))
    if db.enabled:
        return _claim_live(db, job_id, jobs_dir, who, stamp)
    root = resolve_jobs_dir(jobs_dir)
    path = _job_path(root, job_id)
    if not path.is_file():
        raise JobError("任务不存在")
    _acquire_lock(root, job_id, who)
    try:
        job = _read_job(path)
        _prepare_claim(job, who, stamp)
        _write_job(path, job)
    except Exception:
        _release_lock(root, job_id)
        raise
    db.note("update", build_claim_patch(job), job_id=job_id, match_status="queued")
    return _claim_payload(job_id)


def complete_job(
    job_id, results, jobs_dir=None, now=None, dry=False, *,
    db=None, env=None, transport=None, db_dry_run=None,
):
    """running → success 或 failed。有库时回写腿 jsonb 和 finished_at。

    results 的键是 old_machine / accessories。ok 或 status 表示成败，
    row_count（也认 rows / count）是行数。失败要带 error。
    范围内缺腿记 failed，不用 rows=0 冒充成功。范围以外的腿、凭据、
    UPLOAD / APPLY / login 都会拒绝，原记录保持不动。
    """
    _guard_phase()
    job_id = _require_job_id(job_id)
    if not isinstance(results, dict):
        raise JobError("回写结果格式不正确")
    _reject_forbidden(results)
    db = _coerce_db(db, env, transport, db_dry_run)
    if db.enabled:
        return _complete_live(db, job_id, results, jobs_dir, dry, now)
    root = resolve_jobs_dir(jobs_dir)
    path = _job_path(root, job_id)
    if not path.is_file():
        raise JobError("任务不存在")
    job = _read_job(path)
    _apply_results(job, results, dry, now)
    _write_job(path, job)
    _release_lock(root, job_id)
    db.note("update", build_complete_patch(job), job_id=job_id, match_status="running")
    return _complete_payload(job, dry)


def run_once(
    jobs_dir=None,
    job_id=None,
    dry=False,
    fixture=None,
    fail=False,
    fail_leg=None,
    error=None,
    now=None,
    claimer=None,
    *,
    db=None,
    env=None,
    transport=None,
    db_dry_run=None,
):
    """占位 runner。dry 为真时：认领最早的 queued，回写占位行数。

    有库凭据时从 salary_fetch_runs 选 queued，并把 dry 写成 true。
    不加 dry 时不认领、不改文件、不写库。返回 (payload, exit_code)。
    """
    _guard_phase()
    if not dry:
        return {
            "ok": False,
            "claimed": False,
            "login": False,
            "upload": False,
            "apply": False,
            "error": REFUSE_REAL,
        }, 2

    db = _coerce_db(db, env, transport, db_dry_run)
    root = resolve_jobs_dir(jobs_dir)
    if db.enabled:
        actionable, skipped, target, job_id = _pick_live(db, job_id)
    else:
        actionable, skipped = _split_queued(root)
        target = None
        if job_id:
            job_id = _require_job_id(job_id)
            path = _job_path(root, job_id)
            if not path.is_file():
                raise JobError("任务不存在")
            target = _read_job(path)
            if target.get("status") != "queued":
                raise JobError("任务不是 queued，不能认领")
            _reject_armed(target)
        elif not actionable:
            target = None
        else:
            target = actionable[0]
            job_id = target["job_id"]
    if target is None:
        payload = {
            "ok": True,
            "claimed": False,
            "login": False,
            "upload": False,
            "apply": False,
        }
        if skipped:
            payload["skipped_forbidden"] = skipped
        return payload, 0

    names = _leg_names(target)
    if not names:
        raise JobError("任务没有要回写的腿")
    if fail_leg and fail_leg not in names:
        raise JobError("失败腿不在本次范围")
    counts = _load_counts(fixture)
    fail_error = error.strip() if isinstance(error, str) and error.strip() else FAIL_MESSAGE
    results = {}
    for name in names:
        if _leg_should_fail(name, fail, fail_leg):
            results[name] = {"ok": False, "row_count": 0, "error": fail_error}
        else:
            results[name] = {"ok": True, "row_count": counts[name]}

    started = _coerce_now(now)
    claim_job(job_id, jobs_dir=root, now=started, claimer=claimer, db=db)
    done = complete_job(
        job_id,
        results,
        jobs_dir=root,
        now=started + timedelta(seconds=1),
        dry=True,
        db=db,
    )
    payload = {
        "ok": True,
        "claimed": True,
        "job_id": job_id,
        "status": done["status"],
        "dry": True,
        "login": False,
        "upload": False,
        "apply": False,
    }
    if skipped:
        payload["skipped_forbidden"] = skipped
    return payload, 0


def main(argv=None):
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except argparse.ArgumentError:
        print(json.dumps({"error": "参数不正确"}, ensure_ascii=False))
        return 2
    except SystemExit as exc:
        code = exc.code
        return 0 if code in (0, None) else int(code)
    try:
        payload, code = _execute(args)
    except JobError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return exc.exit_code
    print(json.dumps(payload, ensure_ascii=False))
    return code


def _execute(args):
    jobs_dir = args.jobs_dir
    if args.cmd == "run" and not args.dry:
        return run_once(
            jobs_dir=jobs_dir,
            job_id=args.job_id,
            dry=False,
            fixture=args.fixture,
            fail=args.fail,
            fail_leg=args.fail_leg,
            error=args.error,
        )
    db = open_db(db_dry_run=True if args.db_dry_run else None)
    if args.cmd == "create":
        payload, code = create_job(args.month, args.scope, jobs_dir=jobs_dir, db=db), 0
    elif args.cmd == "latest":
        payload, code = latest_payload(args.month, jobs_dir=jobs_dir, db=db), 0
    elif args.cmd == "claim":
        payload, code = claim_job(args.job_id, jobs_dir=jobs_dir, db=db), 0
    elif args.cmd == "complete":
        payload, code = complete_job(
            args.job_id, _parse_results(args.results), jobs_dir=jobs_dir, db=db,
        ), 0
    elif args.cmd == "run":
        payload, code = run_once(
            jobs_dir=jobs_dir,
            job_id=args.job_id,
            dry=args.dry,
            fixture=args.fixture,
            fail=args.fail,
            fail_leg=args.fail_leg,
            error=args.error,
            db=db,
        )
    else:
        raise JobError("参数不正确")
    _emit_preview(db)
    return payload, code


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="fetch_jobs.py",
        description="抓取任务。真源是 salary_fetch_runs。不登录、不 APPLY。契约见 docs/契约_fetch-jobs.md。",
        exit_on_error=False,
    )
    parser.add_argument("--jobs-dir", default=None, help="缓存目录，优先于 FETCH_JOBS_DIR")
    parser.add_argument(
        "--db-dry-run",
        action="store_true",
        help="不连库，把将写入 salary_fetch_runs 的内容打到标准错误",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="写入 queued 任务并打印 job_id", exit_on_error=False)
    create.add_argument("--month", required=True, help="YYYY-MM")
    create.add_argument("--scope", required=True, choices=SCOPES)

    latest = sub.add_parser("latest", help="打印该月最近一条", exit_on_error=False)
    latest.add_argument("--month", required=True, help="YYYY-MM")

    claim = sub.add_parser("claim", help="认领指定 queued 任务", exit_on_error=False)
    claim.add_argument("--job-id", required=True)

    complete = sub.add_parser("complete", help="回写 running 任务的行数", exit_on_error=False)
    complete.add_argument("--job-id", required=True)
    complete.add_argument("--results", required=True, help="腿结果 JSON")

    run = sub.add_parser("run", help="占位 runner。不加 --dry 则拒绝实抓", exit_on_error=False)
    run.add_argument("--dry", action="store_true", help="用占位行数走完 queued→running→success/failed")
    run.add_argument("--job-id", default=None, help="指定任务；缺省认领最早的 queued")
    run.add_argument("--fixture", default=None, help="假数据 JSON，提供 row_count")
    run.add_argument("--fail", action="store_true", help="占位失败")
    run.add_argument("--fail-leg", choices=LEG_NAMES, default=None, help="只让这一腿失败")
    run.add_argument("--error", default=None, help="失败原因")
    return parser


def _guard_phase():
    if ALLOW_UPLOAD or ALLOW_APPLY:
        raise JobError("禁止 UPLOAD / APPLY", exit_code=1)


def _require_month(value):
    if not isinstance(value, str) or not _MONTH_RE.match(value.strip()):
        raise JobError("月份或范围不正确")
    month = value.strip()
    mon = int(month[5:7])
    if not 1 <= mon <= 12:
        raise JobError("月份或范围不正确")
    return month


def _require_scope(value):
    if not isinstance(value, str) or value.strip() not in SCOPES:
        raise JobError("月份或范围不正确")
    return value.strip()


def _require_job_id(value):
    if not isinstance(value, str) or not _JOB_ID_RE.match(value):
        raise JobError("任务号不正确")
    return value


def _require_rows(value):
    if isinstance(value, bool) or isinstance(value, float):
        raise JobError("行数不正确")
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise JobError("行数不正确")
        value = int(text)
    if not isinstance(value, int) or value < 0:
        raise JobError("行数不正确")
    return value


def _coerce_now(now):
    if now is None:
        return datetime.now(TZ)
    if not isinstance(now, datetime):
        raise JobError("时间不正确", exit_code=1)
    if now.tzinfo is None:
        return now.replace(tzinfo=TZ)
    return now.astimezone(TZ)


def _format_time(now):
    return _coerce_now(now).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def _blank_leg():
    return {"status": "pending", "row_count": None, "error": None, "message": None}


def _running_leg():
    return {"status": "running", "row_count": None, "error": None, "message": None}


def _allocate_id(root, stamp):
    prefix = stamp.astimezone(TZ).strftime("%Y%m%dT%H%M%S")
    for _ in range(5):
        job_id = "fj_%s_%s" % (prefix, secrets.token_hex(3))
        path = _job_path(root, job_id)
        if not path.exists():
            return job_id, path
    raise JobError("任务号冲突", exit_code=1)


def _job_path(root, job_id):
    job_id = _require_job_id(job_id)
    base = Path(root).resolve()
    path = (base / ("%s.json" % job_id)).resolve()
    if path.parent != base:
        raise JobError("任务号不正确")
    return path


def _write_job(path, job):
    _guard_phase()
    job["upload"] = False
    job["apply"] = False
    job["login"] = False
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(job, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_job(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JobError("任务文件损坏") from exc
    if not isinstance(data, dict) or data.get("job_id") != path.stem:
        raise JobError("任务文件损坏")
    return data


def _load_all(root):
    base = Path(root)
    if not base.is_dir():
        return []
    found = []
    for path in base.glob("fj_*.json"):
        if not _JOB_ID_RE.match(path.stem):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("job_id") != path.stem:
            continue
        found.append(data)
    return found


def _recency_key(job):
    return (str(job.get("created_at") or ""), str(job.get("job_id") or ""))


def _leg_names(job):
    return [name for name in LEG_NAMES if isinstance(job.get(name), dict)]


def _public_leg(leg):
    if not isinstance(leg, dict):
        return None
    status = leg.get("status")
    if status not in LEG_STATUSES:
        return None
    out = {"status": status}
    rows = leg.get("row_count")
    if isinstance(rows, int) and not isinstance(rows, bool) and rows >= 0:
        out["row_count"] = rows
    err = leg.get("error")
    message = leg.get("message")
    if isinstance(err, str) and err.strip():
        out["error"] = err.strip()
    elif isinstance(message, str) and message.strip():
        out["message"] = message.strip()
    return out


def _public_job(job):
    out = {"job_id": job["job_id"]}
    updated = job.get("updated_at")
    finished = job.get("finished_at")
    if isinstance(updated, str) and updated.strip():
        out["updated_at"] = updated
    if isinstance(finished, str) and finished.strip():
        out["finished_at"] = finished
        out["at"] = finished
    elif isinstance(updated, str) and updated.strip():
        out["at"] = updated
    for name in LEG_NAMES:
        leg = _public_leg(job.get(name))
        if leg is not None:
            out[name] = leg
    return out


def _reject_armed(job):
    if job.get("upload") or job.get("apply") or job.get("login"):
        raise JobError("禁止 UPLOAD / APPLY")


def _reject_forbidden(value):
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).strip().lower()
            if name in _GATE_KEYS and item:
                raise JobError("禁止 UPLOAD / APPLY")
            if name in _SECRET_KEYS:
                raise JobError("回写不能带凭据")
            _reject_forbidden(item)
    elif isinstance(value, list):
        for item in value:
            _reject_forbidden(item)


def _result_leg(spec, dry):
    if not isinstance(spec, dict):
        raise JobError("回写结果格式不正确")
    _reject_forbidden(spec)
    ok = spec.get("ok")
    status = spec.get("status")
    success_signal = ok is True or status in ("ok", "success")
    fail_signal = ok is False or status in ("failed", "error")
    if success_signal and fail_signal:
        raise JobError("回写结果互相矛盾")
    if not success_signal and not fail_signal:
        raise JobError("回写缺少成功或失败")
    kind = "success" if success_signal else "failed"
    rows = _rows_from_spec(spec, kind)
    if kind == "failed":
        err = spec.get("error")
        if not isinstance(err, str) or not err.strip():
            err = spec.get("message")
        if not isinstance(err, str) or not err.strip():
            err = "失败"
        return {
            "status": "failed",
            "row_count": rows,
            "error": err.strip(),
            "message": None,
        }
    message = DRY_MESSAGE if dry else None
    if not dry and isinstance(spec.get("message"), str) and spec["message"].strip():
        message = spec["message"].strip()
    return {"status": "success", "row_count": rows, "error": None, "message": message}


def _rows_from_spec(spec, kind):
    for key in ("row_count", "rows", "count"):
        if key in spec and spec[key] is not None:
            return _require_rows(spec[key])
    if kind == "failed":
        return 0
    raise JobError("成功回写必须带行数")


def _load_counts(fixture):
    counts = dict(DRY_ROW_COUNTS)
    if not fixture:
        return counts
    path = Path(fixture).expanduser()
    if not path.is_file():
        raise JobError("假数据文件不存在")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise JobError("假数据格式不正确") from exc
    if not isinstance(data, dict):
        raise JobError("假数据格式不正确")
    _reject_forbidden(data)
    for name in LEG_NAMES:
        if name not in data:
            continue
        raw = data[name]
        if isinstance(raw, dict):
            if "row_count" in raw:
                counts[name] = _require_rows(raw["row_count"])
            elif "rows" in raw:
                counts[name] = _require_rows(raw["rows"])
            else:
                raise JobError("假数据格式不正确")
        else:
            counts[name] = _require_rows(raw)
    return counts


def _leg_should_fail(name, fail, fail_leg):
    if fail_leg:
        return name == fail_leg
    return bool(fail)


def _split_queued(root):
    actionable = []
    skipped = []
    for job in _load_all(root):
        if job.get("status") != "queued":
            continue
        if job.get("upload") or job.get("apply") or job.get("login"):
            skipped.append(job.get("job_id"))
            continue
        actionable.append(job)
    actionable.sort(key=_recency_key)
    return actionable, skipped


def _acquire_lock(root, job_id, claimer):
    lock = Path(root) / "locks" / ("%s.lock" % job_id)
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError as exc:
        raise JobError("任务已被认领") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(claimer + "\n")
    return lock


def _release_lock(root, job_id):
    lock = Path(root) / "locks" / ("%s.lock" % job_id)
    try:
        lock.unlink()
    except FileNotFoundError:
        return


def _claimer_name(claimer):
    if isinstance(claimer, str) and claimer.strip():
        return claimer.strip()
    try:
        name = socket.gethostname().strip()
    except OSError:
        name = ""
    return name or "mac"


def _parse_results(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise JobError("回写结果格式不正确") from exc
    if not isinstance(data, dict):
        raise JobError("回写结果格式不正确")
    return data


def _prepare_claim(job, who, stamp):
    _reject_armed(job)
    if job.get("status") != "queued":
        raise JobError("任务不是 queued，不能认领")
    scope = _require_scope(job.get("scope"))
    _require_month(job.get("month"))
    if scope == "accessories" and isinstance(job.get("old_machine"), dict):
        raise JobError("回写了本次范围以外的腿")
    job["status"] = "running"
    job["started_at"] = stamp
    job["updated_at"] = stamp
    job["claimed_by"] = who
    job["upload"] = False
    job["apply"] = False
    job["login"] = False
    for name in _leg_names(job):
        job[name] = _running_leg()


def _apply_results(job, results, dry, now):
    """把回写贴到任务上。范围内缺腿记 failed，不用 rows=0 记成成功。"""
    _reject_armed(job)
    if job.get("status") != "running":
        raise JobError("只有 running 的任务可以回写")
    scope = _require_scope(job.get("scope"))
    if scope == "accessories" and isinstance(job.get("old_machine"), dict):
        raise JobError("回写了本次范围以外的腿")
    names = _leg_names(job)
    if not names:
        raise JobError("任务没有要回写的腿")
    for key in LEG_NAMES:
        if key in results and key not in names:
            raise JobError("回写了本次范围以外的腿")
    for name in names:
        if name not in results:
            job[name] = {
                "status": "failed",
                "row_count": 0,
                "error": "未回写",
                "message": None,
            }
        else:
            job[name] = _result_leg(results[name], dry=dry)
        if job[name]["status"] == "success" and name not in results:
            raise JobError("缺腿不能记成成功")
    job["status"] = (
        "success" if all(job[name]["status"] == "success" for name in names) else "failed"
    )
    stamp = _format_time(_coerce_now(now))
    job["finished_at"] = stamp
    job["updated_at"] = stamp
    job["dry"] = bool(dry)
    job["upload"] = False
    job["apply"] = False
    job["login"] = False


def _claim_payload(job_id):
    return {
        "job_id": job_id,
        "status": "running",
        "login": False,
        "upload": False,
        "apply": False,
    }


def _complete_payload(job, dry):
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "dry": bool(dry),
        "login": False,
        "upload": False,
        "apply": False,
    }


def _claim_live(db, job_id, jobs_dir, who, stamp):
    row = db.get(job_id)
    if row is None:
        raise JobError("任务不存在")
    job = _job_from_row(row)
    _prepare_claim(job, who, stamp)
    updated = db.update(job_id, "queued", build_claim_patch(job))
    if updated is None:
        raise JobError("任务已被认领")
    _cache_job(jobs_dir, _job_from_row(updated))
    return _claim_payload(job_id)


def _complete_live(db, job_id, results, jobs_dir, dry, now):
    row = db.get(job_id)
    if row is None:
        raise JobError("任务不存在")
    job = _job_from_row(row)
    _apply_results(job, results, dry, now)
    updated = db.update(job_id, "running", build_complete_patch(job))
    if updated is None:
        raise JobError("只有 running 的任务可以回写")
    stored = _job_from_row(updated)
    _cache_job(jobs_dir, stored, release_lock=True)
    return _complete_payload(stored, dry)


def _pick_live(db, job_id):
    if job_id:
        job_id = _require_job_id(job_id)
        row = db.get(job_id)
        if row is None:
            raise JobError("任务不存在")
        if _row_armed(row):
            raise JobError("禁止 UPLOAD / APPLY")
        target = _job_from_row(row)
        if target.get("status") != "queued":
            raise JobError("任务不是 queued，不能认领")
        return [], [], target, job_id
    actionable, skipped = _split_queued_rows(db.queued())
    if not actionable:
        return actionable, skipped, None, None
    target = actionable[0]
    return actionable, skipped, target, target["job_id"]


def _split_queued_rows(rows):
    actionable = []
    skipped = []
    for row in rows:
        if row.get("status") != "queued":
            continue
        if _row_armed(row):
            skipped.append(row.get("job_id"))
            continue
        actionable.append(_job_from_row(row))
    actionable.sort(key=_recency_key)
    return actionable, skipped


def _row_armed(row):
    return bool(row.get("upload") or row.get("apply") or row.get("login"))


def _cache_job(jobs_dir, job, release_lock=False):
    """库已写成功之后的文件缓存。目录写不了时不推翻库里的结果。"""
    try:
        root = resolve_jobs_dir(jobs_dir)
        _write_job(_job_path(root, job["job_id"]), job)
    except OSError:
        return
    if release_lock:
        try:
            _release_lock(root, job["job_id"])
        except OSError:
            return


def _is_row_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _leg_for_db(leg):
    if leg is None:
        return None
    if not isinstance(leg, dict):
        raise JobError("回写结果格式不正确")
    status = leg.get("status")
    if status not in LEG_STATUSES:
        raise JobError("回写结果格式不正确")
    out = {"status": status}
    rows = leg.get("row_count", None)
    if rows is None:
        out["row_count"] = None
    elif _is_row_count(rows):
        out["row_count"] = rows
    else:
        out["row_count"] = _require_rows(rows)
    err = leg.get("error")
    message = leg.get("message")
    if isinstance(err, str) and err.strip():
        out["error"] = err.strip()
    if isinstance(message, str) and message.strip():
        out["message"] = message.strip()
    if status == "success" and not _is_row_count(out.get("row_count")):
        raise JobError("成功回写必须带行数")
    if status == "success" and out.get("error"):
        raise JobError("回写结果互相矛盾")
    return out


def _leg_from_db(leg):
    if leg is None:
        return None
    if isinstance(leg, str):
        try:
            leg = json.loads(leg)
        except json.JSONDecodeError as exc:
            raise JobError("写库响应不正确") from exc
    return _leg_for_db(leg)


def _assert_scope_legs(scope, old, accessories):
    if scope == "accessories":
        if old is not None:
            raise JobError("回写了本次范围以外的腿")
        if not isinstance(accessories, dict):
            raise JobError("任务没有要回写的腿")
        return
    if not isinstance(old, dict) or not isinstance(accessories, dict):
        raise JobError("任务没有要回写的腿")


def _require_gates_false(body):
    if not isinstance(body, dict):
        raise JobError("写库内容不正确", exit_code=1)
    for key in ("upload", "apply", "login"):
        if body.get(key) is not False:
            raise JobError("禁止 UPLOAD / APPLY")
    _reject_forbidden(body)


def build_insert_row(job):
    """拼一条 queued 插入行。upload / apply / login 只能是 false。"""
    if job.get("upload") or job.get("apply") or job.get("login"):
        raise JobError("禁止 UPLOAD / APPLY")
    scope = _require_scope(job.get("scope"))
    old = _leg_for_db(job.get("old_machine"))
    accessories = _leg_for_db(job.get("accessories"))
    _assert_scope_legs(scope, old, accessories)
    row = {
        "job_id": _require_job_id(job.get("job_id")),
        "month": _require_month(job.get("month")),
        "scope": scope,
        "status": "queued",
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "old_machine": old,
        "accessories": accessories,
        "claimed_by": job.get("claimed_by"),
        "dry": bool(job.get("dry")),
        "upload": False,
        "apply": False,
        "login": False,
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    _require_gates_false(row)
    return row


def build_claim_patch(job):
    """queued → running 的更新体。不改 dry，不带 created_at。"""
    if job.get("upload") or job.get("apply") or job.get("login"):
        raise JobError("禁止 UPLOAD / APPLY")
    if job.get("status") != "running":
        raise JobError("认领补丁要在状态改为 running 之后再拼", exit_code=1)
    scope = _require_scope(job.get("scope"))
    old = _leg_for_db(job.get("old_machine"))
    accessories = _leg_for_db(job.get("accessories"))
    _assert_scope_legs(scope, old, accessories)
    patch = {
        "status": "running",
        "started_at": job.get("started_at"),
        "updated_at": job.get("updated_at"),
        "claimed_by": job.get("claimed_by"),
        "old_machine": old,
        "accessories": accessories,
        "upload": False,
        "apply": False,
        "login": False,
    }
    _require_gates_false(patch)
    return patch


def build_complete_patch(job):
    """回写腿 jsonb、任务成败、finished_at、dry。"""
    if job.get("upload") or job.get("apply") or job.get("login"):
        raise JobError("禁止 UPLOAD / APPLY")
    if job.get("status") not in ("success", "failed"):
        raise JobError("只有 running 的任务可以回写")
    scope = _require_scope(job.get("scope"))
    old = _leg_for_db(job.get("old_machine"))
    accessories = _leg_for_db(job.get("accessories"))
    _assert_scope_legs(scope, old, accessories)
    for leg in (old, accessories):
        if isinstance(leg, dict) and leg.get("status") == "success" and leg.get("error"):
            raise JobError("回写结果互相矛盾")
    patch = {
        "status": job["status"],
        "finished_at": job.get("finished_at"),
        "updated_at": job.get("updated_at"),
        "dry": bool(job.get("dry")),
        "old_machine": old,
        "accessories": accessories,
        "upload": False,
        "apply": False,
        "login": False,
    }
    _require_gates_false(patch)
    return patch


def latest_rest_query(month):
    """同月按 created_at 倒序取一条。时间相同再比 job_id。"""
    month = _require_month(month)
    return "select=%s&month=eq.%s&order=created_at.desc,job_id.desc&limit=1" % (
        SELECT_COLUMNS, urllib.parse.quote(month, safe=""),
    )


def _job_from_row(row):
    if not isinstance(row, dict):
        raise JobError("写库响应不正确")
    if _row_armed(row):
        raise JobError("禁止 UPLOAD / APPLY")
    scope = _require_scope(row.get("scope"))
    old = _leg_from_db(row.get("old_machine"))
    accessories = _leg_from_db(row.get("accessories"))
    if scope == "accessories" and old is not None:
        raise JobError("写库响应不正确")
    if scope == "all" and (old is None or accessories is None):
        raise JobError("写库响应不正确")
    status = row.get("status")
    if status not in JOB_STATUSES:
        raise JobError("写库响应不正确")
    return {
        "job_id": _require_job_id(row.get("job_id")),
        "month": _require_month(row.get("month")),
        "scope": scope,
        "status": status,
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
        "started_at": row.get("started_at"),
        "finished_at": row.get("finished_at"),
        "claimed_by": row.get("claimed_by"),
        "dry": bool(row.get("dry")),
        "entry_url": ENTRY_URL,
        "source": SOURCE,
        "old_machine": old,
        "accessories": accessories,
        "upload": False,
        "apply": False,
        "login": False,
    }


def public_latest_from_row(row):
    """库行 → 与文件缓存相同的 {"latest": ...} 形状。row 为空时 latest 为 null。"""
    if row is None:
        return {"latest": None}
    return {"latest": _public_job(_job_from_row(row))}


def _env_flag(value):
    return str(value or "").strip().lower() in ("1", "true", "yes")


def open_db(env=None, transport=None, db_dry_run=None):
    """有成对凭据且未要求演练时才连库。缺凭据是演练，不成对则拒绝。"""
    if env is None:
        env = os.environ
    url = (env.get("SUPABASE_URL") or env.get("NEXT_PUBLIC_SUPABASE_URL") or "").strip().rstrip("/")
    key = (env.get("SUPABASE_SERVICE_ROLE_KEY") or env.get("SUPABASE_SERVICE_ROLE") or "").strip()
    if db_dry_run is None:
        preview = _env_flag(env.get("FETCH_JOBS_DB_DRY_RUN"))
    else:
        preview = bool(db_dry_run)
    if preview or (not url and not key):
        return FetchRunDb(enabled=False, url="", key="", transport=transport)
    if not url or not key:
        raise JobError("写库需要 SUPABASE_URL 与 SUPABASE_SERVICE_ROLE_KEY")
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/"):
        raise JobError("SUPABASE_URL 须为 https 的项目根地址")
    if parts.username or parts.password:
        raise JobError("SUPABASE_URL 不能带账号")
    return FetchRunDb(enabled=True, url=url, key=key, transport=transport)


def _coerce_db(db, env, transport, db_dry_run):
    if db is not None:
        return db
    return open_db(env=env, transport=transport, db_dry_run=db_dry_run)


def _emit_preview(db):
    if db is None or db.enabled or not db.previews:
        return
    print(json.dumps({
        "db_dry_run": True,
        "table": TABLE,
        "writes": db.previews,
    }, ensure_ascii=False), file=sys.stderr)


def _rest_url(base, query):
    url = base.rstrip("/") + "/rest/v1/" + TABLE
    if query:
        url = url + "?" + query
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.path != "/rest/v1/" + TABLE:
        raise JobError("只允许访问表 salary_fetch_runs")
    return url


class _NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def _exchange(method, url, key, body, prefer, transport):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "apikey": key,
        "Authorization": "Bearer " + key,
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    if prefer:
        headers["Prefer"] = prefer
    if transport is not None:
        try:
            status, raw = transport(method, url, headers, data)
        except JobError:
            raise
        except OSError as exc:
            raise JobError("写库连接失败") from exc
        return status, raw
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
    except urllib.error.HTTPError as exc:
        exc.read()
        raise JobError("写库失败 HTTP %s" % exc.code) from exc
    except urllib.error.URLError as exc:
        raise JobError("写库连接失败") from exc
    return status, raw


class FetchRunDb:
    def __init__(self, enabled, url, key, transport):
        self.enabled = enabled
        self.url = url
        self.key = key
        self.transport = transport
        self.previews = []

    def note(self, op, row, job_id=None, match_status=None):
        item = {"op": op, "row": json.loads(json.dumps(row))}
        if job_id is not None:
            item["job_id"] = job_id
        if match_status is not None:
            item["match_status"] = match_status
        self.previews.append(item)

    def insert(self, row):
        _require_gates_false(row)
        if not self.enabled:
            self.note("insert", row)
            return json.loads(json.dumps(row))
        rows = self._rows("POST", "", row, "return=representation")
        if len(rows) != 1:
            raise JobError("写库响应不正确")
        return rows[0]

    def update(self, job_id, match_status, patch):
        _require_gates_false(patch)
        if not self.enabled:
            self.note("update", patch, job_id=job_id, match_status=match_status)
            merged = json.loads(json.dumps(patch))
            return merged
        query = "job_id=eq.%s&status=eq.%s" % (
            urllib.parse.quote(str(job_id), safe=""),
            urllib.parse.quote(str(match_status), safe=""),
        )
        rows = self._rows("PATCH", query, patch, "return=representation")
        if not rows:
            return None
        if len(rows) != 1:
            raise JobError("写库响应不正确")
        return rows[0]

    def get(self, job_id):
        if not self.enabled:
            raise JobError("写库需要 SUPABASE_URL 与 SUPABASE_SERVICE_ROLE_KEY")
        query = "select=%s&job_id=eq.%s&limit=1" % (
            SELECT_COLUMNS, urllib.parse.quote(str(job_id), safe=""),
        )
        rows = self._rows("GET", query, None, None)
        if not rows:
            return None
        return rows[0]

    def latest(self, month):
        if not self.enabled:
            raise JobError("写库需要 SUPABASE_URL 与 SUPABASE_SERVICE_ROLE_KEY")
        rows = self._rows("GET", latest_rest_query(month), None, None)
        if not rows:
            return None
        return rows[0]

    def queued(self):
        if not self.enabled:
            raise JobError("写库需要 SUPABASE_URL 与 SUPABASE_SERVICE_ROLE_KEY")
        query = (
            "select=%s&status=eq.queued&order=created_at.asc,job_id.asc&limit=100"
            % SELECT_COLUMNS
        )
        rows = self._rows("GET", query, None, None)
        if len(rows) >= 100:
            raise JobError("排队任务达到读取上限，已中止")
        return rows

    def _rows(self, method, query, body, prefer):
        if body is not None:
            _require_gates_false(body)
        status, raw = _exchange(
            method, _rest_url(self.url, query), self.key, body, prefer, self.transport,
        )
        if status == 409:
            raise JobError("任务号冲突", exit_code=1)
        if status is None or status >= 400:
            raise JobError("写库失败 HTTP %s" % status)
        if status == 204 or raw in (None, b"", ""):
            if method == "GET":
                return []
            raise JobError("写库响应不正确")
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise JobError("写库响应不正确") from exc
        if not isinstance(data, list):
            raise JobError("写库响应不正确")
        return data


if __name__ == "__main__":
    sys.exit(main())
