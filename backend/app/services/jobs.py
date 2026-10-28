"""Persisted background jobs executed by an in-process worker pool.

Job rows are the source of truth (status, steps, failure reason, attempts); the pool is just
an executor. On startup, jobs interrupted by a restart are marked FAILED with a reason and can
be retried. For multi-node deployments this module is the seam to swap in a queue (e.g. RQ,
Celery, Arq) without changing callers."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.session import session_scope
from app.db.types import utcnow
from app.models import Job, User

log = logging.getLogger("intelliscrutiny.jobs")
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="job")
HANDLERS: dict[str, Callable[[str], None]] = {}
SYNC_MODE = False  # tests run jobs inline for determinism


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def create_job(db: Session, kind: str, *, application_id: str | None = None, params: dict | None = None,
               user: User | None = None, idempotency_key: str | None = None, steps: list | None = None) -> Job:
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing:
            return existing
    if application_id and kind == "PROCESS_APPLICATION":
        active = db.scalar(select(Job).where(Job.application_id == application_id, Job.kind == kind,
                                             Job.status.in_(["QUEUED", "RUNNING"])))
        if active:
            raise AppError(409, "JOB_ALREADY_RUNNING", "A processing job is already active for this application",
                           {"job_id": active.id})
    job = Job(kind=kind, application_id=application_id, params=params or {}, steps=steps or [],
              created_by_id=user.id if user else None, idempotency_key=idempotency_key)
    db.add(job)
    db.flush()
    return job


def _execute(job_id: str) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        if job.status not in ("QUEUED",):
            return
        job.status, job.started_at, job.attempts = "RUNNING", utcnow(), job.attempts + 1
        kind = job.kind
    try:
        HANDLERS[kind](job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job.status == "RUNNING":
                job.status = "SUCCEEDED"
            job.finished_at = utcnow()
            job.progress = 1.0 if job.status != "CANCELLED" else job.progress
    except Exception as exc:
        log.exception("job %s failed", job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.status, job.error, job.finished_at = "FAILED", f"{type(exc).__name__}: {exc}"[:1000], utcnow()
            _on_failure(db, job)


def _on_failure(db: Session, job: Job) -> None:
    """Manual-review fallback: a failed processing job must not leave the application stuck."""
    if job.kind != "PROCESS_APPLICATION" or not job.application_id:
        return
    from app.models import Application
    from app.services import workflow

    app = db.get(Application, job.application_id)
    if app and app.status == "PROCESSING":
        workflow.transition(db, app, "SUBMITTED", None, note=f"Processing failed: {job.error}")


def enqueue(job_id: str) -> None:
    if SYNC_MODE:
        _execute(job_id)
    else:
        _pool.submit(_execute, job_id)


def recover_interrupted() -> int:
    with session_scope() as db:
        stale = db.scalars(select(Job).where(Job.status.in_(["RUNNING", "QUEUED"]))).all()
        for job in stale:
            job.status, job.error, job.finished_at = "FAILED", "Interrupted by service restart; retry to resume", utcnow()
            _on_failure(db, job)
        return len(stale)
