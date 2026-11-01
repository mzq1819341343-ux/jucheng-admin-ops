#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取任务的文件态队列。

工资页只下发任务、只读上次结果。本模块把任务写成 JSON 文件，供 Mac 上的
runner 认领和回写。这里不登录加盟后台，不发 HTTP，不写工资表，不把
UPLOAD / APPLY 打开。

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
JOB_ID_RE_TEXT = r"^fj_\d{8}T\d{6}_[0-9a-f]{6}$"

REFUSE_REAL = (
    "实抓须在 Mac 上用保险柜登录加盟后台 "
    + ENTRY_URL
    + " 。云端 IP 会被拦。本进程不认领、不回写、不登录，也不 APPLY。"
    "占位回写请加 --dry。"
)

_JOB_ID_RE = re.compile(JOB_ID_RE_TEXT)
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")

_APPLY_KEYS = {"apply", "upload"}
_SECRET_KEYS = {
    "password", "passwd", "cookie", "cookies", "set-cookie",
    "token", "authorization", "secret", "credential", "credentials",
}

__all__ = [
    "JobError",
    "resolve_jobs_dir",
    "create_job",
    "latest_payload",
    "claim_job",
    "complete_job",
    "run_once",
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


def create_job(month, scope, jobs_dir=None, now=None):
    """写入一条 queued 任务，返回 {"job_id": ...}。月份或范围不合法时抛 JobError。"""
    _guard_phase()
    month = _require_month(month)
    scope = _require_scope(scope)
    root = resolve_jobs_dir(jobs_dir)
    root.mkdir(parents=True, exist_ok=True)
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
    _write_job(path, job)
    return {"job_id": job_id}


def latest_payload(month, jobs_dir=None):
    """该月 created_at 最新的一条，包成 {"latest": ...}。没有记录时 {"latest": null}。

    不创建目录。排队中的任务也算最近一次。
    """
    month = _require_month(month)
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


def claim_job(job_id, jobs_dir=None, now=None, claimer=None):
    """queued → running，并留下锁。别的 runner 抢不到同一条。"""
    _guard_phase()
    job_id = _require_job_id(job_id)
    root = resolve_jobs_dir(jobs_dir)
    path = _job_path(root, job_id)
    if not path.is_file():
        raise JobError("任务不存在")
    who = _claimer_name(claimer)
    _acquire_lock(root, job_id, who)
    try:
        job = _read_job(path)
        _reject_armed(job)
        if job.get("status") != "queued":
            raise JobError("任务不是 queued，不能认领")
        _require_scope(job.get("scope"))
        _require_month(job.get("month"))
        stamp = _format_time(_coerce_now(now))
        job["status"] = "running"
        job["started_at"] = stamp
        job["updated_at"] = stamp
        job["claimed_by"] = who
        for name in _leg_names(job):
            job[name] = _running_leg()
        _write_job(path, job)
    except Exception:
        _release_lock(root, job_id)
        raise
    return {
        "job_id": job_id,
        "status": "running",
        "login": False,
        "upload": False,
        "apply": False,
    }


def complete_job(job_id, results, jobs_dir=None, now=None, dry=False):
    """running → success 或 failed。只改文件，不抓数、不登录。

    results 的键是 old_machine / accessories。ok 或 status 表示成败，
    row_count（也认 rows / count）是行数。失败要带 error。
    范围以外的腿、凭据、UPLOAD / APPLY 都会拒绝，文件保持原样。
    """
    _guard_phase()
    job_id = _require_job_id(job_id)
    if not isinstance(results, dict):
        raise JobError("回写结果格式不正确")
    _reject_forbidden(results)
    root = resolve_jobs_dir(jobs_dir)
    path = _job_path(root, job_id)
    if not path.is_file():
        raise JobError("任务不存在")
    job = _read_job(path)
    _reject_armed(job)
    if job.get("status") != "running":
        raise JobError("只有 running 的任务可以回写")
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
    job["status"] = (
        "success" if all(job[name]["status"] == "success" for name in names) else "failed"
    )
    stamp = _format_time(_coerce_now(now))
    job["finished_at"] = stamp
    job["updated_at"] = stamp
    job["dry"] = bool(dry)
    _write_job(path, job)
    _release_lock(root, job_id)
    return {
        "job_id": job_id,
        "status": job["status"],
        "dry": bool(dry),
        "login": False,
        "upload": False,
        "apply": False,
    }


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
):
    """占位 runner。dry 为真时：认领最早的 queued，回写占位行数。

    不加 dry 时不认领、不改文件。返回 (payload, exit_code)。
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

    root = resolve_jobs_dir(jobs_dir)
    actionable, skipped = _split_queued(root)
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
    else:
        target = actionable[0]
        job_id = target["job_id"]

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
    claim_job(job_id, jobs_dir=root, now=started, claimer=claimer)
    done = complete_job(
        job_id,
        results,
        jobs_dir=root,
        now=started + timedelta(seconds=1),
        dry=True,
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
    if args.cmd == "create":
        return create_job(args.month, args.scope, jobs_dir=jobs_dir), 0
    if args.cmd == "latest":
        return latest_payload(args.month, jobs_dir=jobs_dir), 0
    if args.cmd == "claim":
        return claim_job(args.job_id, jobs_dir=jobs_dir), 0
    if args.cmd == "complete":
        return complete_job(args.job_id, _parse_results(args.results), jobs_dir=jobs_dir), 0
    if args.cmd == "run":
        return run_once(
            jobs_dir=jobs_dir,
            job_id=args.job_id,
            dry=args.dry,
            fixture=args.fixture,
            fail=args.fail,
            fail_leg=args.fail_leg,
            error=args.error,
        )
    raise JobError("参数不正确")


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="fetch_jobs.py",
        description="抓取任务文件态。不登录、不 APPLY。契约见 docs/契约_fetch-jobs.md。",
        exit_on_error=False,
    )
    parser.add_argument("--jobs-dir", default=None, help="任务目录，优先于 FETCH_JOBS_DIR")
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
    if job.get("upload") or job.get("apply"):
        raise JobError("禁止 UPLOAD / APPLY")


def _reject_forbidden(value):
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).strip().lower()
            if name in _APPLY_KEYS and item:
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
        if job.get("upload") or job.get("apply"):
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


if __name__ == "__main__":
    sys.exit(main())
