from __future__ import annotations

import io
import statistics
import zipfile
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agents import verification as verification_agent
from app.api import serializers as S
from app.api.deps import Page, current_user, is_tenant_scoped, load_application, paginate, require
from app.connectors.adapters import ADAPTERS
from app.core.config import get_settings
from app.core.errors import AppError, bad_request, not_found
from app.core.permissions import P, ROLE_PERMISSIONS, Role
from app.core.security import hash_password
from app.db.session import get_db
from app.db.types import utcnow
from app.models import (
    Application,
    AuditEvent,
    Connector,
    Document,
    DocumentPage,
    EvaluationRun,
    EvidenceChunk,
    ExtractedField,
    Finding,
    Institution,
    Job,
    Notification,
    ReviewAction,
    Rule,
    SystemSetting,
    User,
    VerificationRequest,
)
from app.rag import indexer
from app.services import audit, extraction, jobs, storage
from app.services.audit import verify_chain

router = APIRouter(tags=["operations"])


def _scoped_apps(user: User):
    q = select(Application)
    return q.where(Application.institution_id == user.institution_id) if is_tenant_scoped(user) else q


def _durations(pairs) -> dict:
    vals = [(b - a).total_seconds() for a, b in pairs if a and b and b >= a]
    if not vals:
        return {"n": 0, "mean_seconds": None, "median_seconds": None}
    return {"n": len(vals), "mean_seconds": round(statistics.mean(vals), 1),
            "median_seconds": round(statistics.median(vals), 1)}


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard/metrics")
def dashboard(user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    apps = db.scalars(_scoped_apps(user)).all()
    app_ids = [a.id for a in apps]
    by_status: dict[str, int] = {}
    by_vstatus: dict[str, int] = {}
    for a in apps:
        by_status[a.status] = by_status.get(a.status, 0) + 1
        by_vstatus[a.verification_status] = by_vstatus.get(a.verification_status, 0) + 1
    findings = db.scalars(select(Finding).where(Finding.application_id.in_(app_ids))).all() if app_ids else []
    by_cat, by_sev, by_agent, open_by_sev = {}, {}, {}, {}
    trend: dict[str, dict[str, int]] = {}
    start = date.today() - timedelta(days=29)
    for i in range(30):
        trend[(start + timedelta(days=i)).isoformat()] = {}
    for f in findings:
        by_cat[f.category] = by_cat.get(f.category, 0) + 1
        by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        by_agent[f.agent] = by_agent.get(f.agent, 0) + 1
        if f.review_status == "OPEN":
            open_by_sev[f.severity] = open_by_sev.get(f.severity, 0) + 1
        day = f.created_at.date().isoformat()
        if day in trend:
            trend[day][f.category] = trend[day].get(f.category, 0) + 1
    vrs = db.scalars(select(VerificationRequest).where(VerificationRequest.application_id.in_(app_ids))).all() if app_ids else []
    v_out: dict[str, int] = {}
    for v in vrs:
        k = v.manual_outcome or v.status
        v_out[k] = v_out.get(k, 0) + 1
    pjobs = db.scalars(select(Job).where(Job.kind == "PROCESS_APPLICATION", Job.application_id.in_(app_ids))).all() if app_ids else []
    step_ms: dict[str, list[int]] = {}
    for j in pjobs:
        for s in j.steps or []:
            if s.get("duration_ms") is not None:
                step_ms.setdefault(s["key"], []).append(s["duration_ms"])
    recent = sorted(apps, key=lambda a: a.updated_at, reverse=True)[:6]
    return {
        "applications": {"total": len(apps), "by_status": by_status,
                         "pending_review": by_status.get("REVIEW_REQUIRED", 0) + by_status.get("REVIEW_IN_PROGRESS", 0),
                         "processing": by_status.get("PROCESSING", 0)},
        "verification": {"by_application_status": by_vstatus, "outcomes": v_out,
                         "manual_queue": sum(1 for v in vrs if v.connector_key == "manual" and not v.manual_outcome)},
        "findings": {"total": len(findings), "by_category": by_cat, "by_severity": by_sev, "by_agent": by_agent,
                     "open_by_severity": open_by_sev},
        "deficiency_trend": [{"date": d, **v} for d, v in trend.items()],
        "processing": {
            "jobs": len(pjobs),
            "succeeded": sum(1 for j in pjobs if j.status == "SUCCEEDED"),
            "partial": sum(1 for j in pjobs if j.status == "PARTIAL"),
            "failed": sum(1 for j in pjobs if j.status == "FAILED"),
            "duration": _durations([(j.started_at, j.finished_at) for j in pjobs]),
            "step_mean_ms": {k: round(statistics.mean(v)) for k, v in step_ms.items()},
        },
        "recent_applications": [{"id": a.id, "reference_no": a.reference_no, "institution": a.institution.name,
                                 "status": a.status, "verification_status": a.verification_status,
                                 "updated_at": S.iso(a.updated_at)} for a in recent],
        "index": indexer.index_status(db) if not is_tenant_scoped(user) else None,
        "data_label": "Synthetic demonstration data" if not get_settings().is_production else None,
    }


# ---------------------------------------------------------------- reports
@router.get("/reports/summary")
def report_summary(user: User = Depends(require(P.REPORT_READ)), db: Session = Depends(get_db)):
    apps = db.scalars(select(Application)).all()
    processed = [a for a in apps if a.validated_at]
    findings = db.scalars(select(Finding)).all()
    first_action: dict[str, object] = {}
    for a in db.scalars(select(ReviewAction).where(ReviewAction.finding_id.is_not(None)).order_by(ReviewAction.created_at)):
        first_action.setdefault(a.finding_id, a.created_at)
    anomalies = [f for f in findings if f.category == "SUSPECTED_ANOMALY"]
    anomaly_outcomes: dict[str, int] = {}
    for f in anomalies:
        anomaly_outcomes[f.review_status] = anomaly_outcomes.get(f.review_status, 0) + 1
    vrs = db.scalars(select(VerificationRequest)).all()
    per_conn: dict[str, dict[str, int]] = {}
    for v in vrs:
        d = per_conn.setdefault(v.connector_key, {})
        k = v.manual_outcome or v.status
        d[k] = d.get(k, 0) + 1
    answered = [v for v in vrs if v.connector_key != "manual"]
    success = sum(1 for v in answered if v.status not in ("SOURCE_UNAVAILABLE",))
    evidence_apps = {a.application_id for a in db.scalars(select(ReviewAction).where(
        ReviewAction.action == "REQUEST_EVIDENCE"))} | {a.id for a in apps if a.status == "RECTIFICATION_REQUESTED"}
    all_jobs = db.scalars(select(Job)).all()
    job_rates: dict[str, dict] = {}
    for j in all_jobs:
        d = job_rates.setdefault(j.kind, {"total": 0, "failed": 0, "partial": 0})
        d["total"] += 1
        d["failed"] += j.status == "FAILED"
        d["partial"] += j.status == "PARTIAL"
    for d in job_rates.values():
        d["failure_rate"] = round(d["failed"] / d["total"], 4) if d["total"] else None
    latest_eval = db.scalar(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(1))
    return {
        "label": "Prototype metrics on synthetic data — not evidence of improvements in real AICTE processing.",
        "applications": {"total": len(apps), "processed": len(processed)},
        "submission_to_preliminary_validation": _durations([(a.submitted_at, a.validated_at) for a in processed]),
        "manual_review_time": {**_durations([(f.created_at, first_action[f.id]) for f in findings if f.id in first_action]),
                               "definition": "finding creation → first human action on that finding"},
        "applications_requiring_additional_evidence": {
            "count": len(evidence_apps & {a.id for a in processed}), "denominator": len(processed),
            "rate": round(len(evidence_apps & {a.id for a in processed}) / len(processed), 4) if processed else None},
        "anomaly_flag_review_outcomes": {"denominator": len(anomalies), "by_review_status": anomaly_outcomes},
        "verification": {"requests": len(answered), "source_reachable": success,
                         "success_rate": round(success / len(answered), 4) if answered else None,
                         "by_connector": per_conn},
        "background_jobs": job_rates,
        "findings_by_category": {c: sum(1 for f in findings if f.category == c)
                                 for c in sorted({f.category for f in findings})},
        "latest_evaluation": {"id": latest_eval.id, "label": latest_eval.label, "metrics": latest_eval.metrics,
                              "sample_counts": latest_eval.sample_counts, "created_at": S.iso(latest_eval.created_at),
                              "pipeline_version": latest_eval.pipeline_version} if latest_eval else None,
    }


@router.get("/reports/evaluations")
def evaluations(user: User = Depends(require(P.REPORT_READ)), db: Session = Depends(get_db)):
    return [{"id": e.id, "label": e.label, "metrics": e.metrics, "sample_counts": e.sample_counts,
             "pipeline_version": e.pipeline_version, "created_at": S.iso(e.created_at), "is_synthetic": e.is_synthetic}
            for e in db.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()).limit(20))]


class EvalIn(BaseModel):
    samples: int = Field(24, ge=6, le=120)


@router.post("/reports/evaluations", status_code=202)
def run_eval(body: EvalIn, user: User = Depends(require(P.EVAL_RUN)), db: Session = Depends(get_db)):
    job = jobs.create_job(db, "EVALUATION", params={"samples": body.samples}, user=user)
    audit.record(db, "evaluation.requested", "job", job.id, actor=user, details={"samples": body.samples})
    db.commit()
    jobs.enqueue(job.id)
    return S.job(job)


# ---------------------------------------------------------------- audit
@router.get("/audit")
def audit_events(page: Page = Depends(), q: str | None = Query(None, max_length=100), action: str | None = None,
                 actor_email: str | None = None, entity_type: str | None = None, application_id: str | None = None,
                 since: date | None = None, until: date | None = None,
                 user: User = Depends(require(P.AUDIT_READ)), db: Session = Depends(get_db)):
    stmt = select(AuditEvent).order_by(AuditEvent.seq.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditEvent.action.ilike(like), AuditEvent.actor_email.ilike(like),
                              AuditEvent.entity_id.ilike(like), AuditEvent.request_id.ilike(like)))
    for col, val in ((AuditEvent.action, action), (AuditEvent.actor_email, actor_email),
                     (AuditEvent.entity_type, entity_type), (AuditEvent.application_id, application_id)):
        if val:
            stmt = stmt.where(col == val)
    if since:
        stmt = stmt.where(AuditEvent.ts >= since)
    if until:
        stmt = stmt.where(AuditEvent.ts < until + timedelta(days=1))
    return paginate(db, stmt, page, S.audit_event)


@router.get("/audit/facets")
def audit_facets(user: User = Depends(require(P.AUDIT_READ)), db: Session = Depends(get_db)):
    return {"actions": sorted(r for (r,) in db.execute(select(AuditEvent.action).distinct())),
            "entity_types": sorted(r for (r,) in db.execute(select(AuditEvent.entity_type).distinct()))}


@router.get("/audit/verify-chain")
def audit_verify(user: User = Depends(require(P.AUDIT_READ)), db: Session = Depends(get_db)):
    return verify_chain(db)


# ---------------------------------------------------------------- notifications & search
@router.get("/notifications")
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Notification).where(Notification.user_id == user.id)
                      .order_by(Notification.created_at.desc()).limit(50)).all()
    return {"unread": sum(1 for n in rows if not n.read),
            "items": [{"id": n.id, "title": n.title, "body": n.body, "link": n.link, "severity": n.severity,
                       "read": n.read, "created_at": S.iso(n.created_at)} for n in rows]}


@router.post("/notifications/read-all")
def read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read.is_(False))):
        n.read = True
    db.commit()
    return {"ok": True}


@router.get("/search")
def global_search(q: str = Query(min_length=2, max_length=100), user: User = Depends(require(P.APP_READ)),
                  db: Session = Depends(get_db)):
    like = f"%{q}%"
    apps = db.scalars(_scoped_apps(user).join(Institution).where(or_(
        Application.reference_no.ilike(like), Institution.name.ilike(like), Institution.code.ilike(like))).limit(6)).all()
    dq = select(Document).where(Document.filename.ilike(like))
    if is_tenant_scoped(user):
        dq = dq.where(Document.institution_id == user.institution_id)
    fq = select(Finding).join(Application).where(or_(Finding.title.ilike(like), Finding.code.ilike(like)))
    if is_tenant_scoped(user):
        fq = fq.where(Application.institution_id == user.institution_id)
    rules = [] if is_tenant_scoped(user) else db.scalars(select(Rule).where(or_(
        Rule.rule_code.ilike(like), Rule.title.ilike(like))).limit(5)).all()
    return {
        "applications": [{"id": a.id, "label": a.reference_no, "sub": a.institution.name, "href": f"/applications/{a.id}"}
                         for a in apps],
        "documents": [{"id": d.id, "label": d.filename, "sub": d.doc_type, "href": f"/document-intelligence?doc={d.id}"}
                      for d in db.scalars(dq.limit(6))],
        "findings": [{"id": f.id, "label": f.title, "sub": f.severity,
                      "href": f"/applications/{f.application_id}?finding={f.id}"} for f in db.scalars(fq.limit(6))],
        "rules": [{"id": r.id, "label": f"{r.rule_code} v{r.version}", "sub": r.title, "href": f"/regulatory-rules?rule={r.id}"}
                  for r in rules],
    }


# ---------------------------------------------------------------- jobs
@router.get("/jobs/{job_id}")
def get_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise not_found("Job")
    if job.application_id:
        load_application(db, job.application_id, user)
    elif is_tenant_scoped(user):
        raise not_found("Job")
    return S.job(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, user: User = Depends(require(P.APP_PROCESS)), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise not_found("Job")
    if job.status not in ("QUEUED", "RUNNING"):
        raise AppError(409, "NOT_CANCELLABLE", f"Job is {job.status}")
    job.cancel_requested = True
    audit.record(db, "job.cancel_requested", "job", job.id, actor=user, application_id=job.application_id)
    db.commit()
    return S.job(job)


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, user: User = Depends(require(P.APP_PROCESS)), db: Session = Depends(get_db)):
    old = db.get(Job, job_id)
    if old is None:
        raise not_found("Job")
    if old.status not in ("FAILED", "CANCELLED", "PARTIAL"):
        raise AppError(409, "NOT_RETRYABLE", f"Job is {old.status}")
    from app.agents.orchestrator import initial_steps

    job = jobs.create_job(db, old.kind, application_id=old.application_id, params={**old.params, "retry_of": old.id},
                          user=user, steps=initial_steps() if old.kind == "PROCESS_APPLICATION" else [])
    audit.record(db, "job.retried", "job", job.id, actor=user, application_id=old.application_id,
                 details={"retry_of": old.id})
    db.commit()
    jobs.enqueue(job.id)
    return S.job(job)


# ---------------------------------------------------------------- connectors
@router.get("/connectors")
def connectors(user: User = Depends(require(P.CONNECTOR_READ)), db: Session = Depends(get_db)):
    out = []
    for c in db.scalars(select(Connector).order_by(Connector.kind, Connector.name)):
        d = S.connector(c)
        d["request_count"] = db.scalar(select(func.count()).select_from(VerificationRequest)
                                       .where(VerificationRequest.connector_key == c.key))
        out.append(d)
    return out


class ConnectorPatch(BaseModel):
    enabled: bool | None = None
    timeout_ms: int | None = Field(None, ge=100, le=60_000)
    max_retries: int | None = Field(None, ge=0, le=5)
    simulate_outage: bool | None = None


@router.patch("/connectors/{connector_id}")
def update_connector(connector_id: str, body: ConnectorPatch, user: User = Depends(require(P.CONNECTOR_MANAGE)),
                     db: Session = Depends(get_db)):
    c = db.get(Connector, connector_id)
    if c is None:
        raise not_found("Connector")
    before = S.connector(c)
    if body.enabled and c.kind == "DIGILOCKER":
        raise AppError(403, "NOT_AUTHORIZED", "This connector requires official authorization and credentials")
    if body.simulate_outage is not None:
        if c.mode != "MOCK":
            raise bad_request("Outage simulation applies to mock connectors only")
        c.config = {**c.config, "simulate_outage": body.simulate_outage}
    for k in ("enabled", "timeout_ms", "max_retries"):
        if getattr(body, k) is not None:
            setattr(c, k, getattr(body, k))
    audit.record(db, "connector.updated", "connector", c.id, actor=user,
                 details={"before": {k: before[k] for k in ("enabled", "timeout_ms", "max_retries")},
                          "changes": body.model_dump(exclude_none=True)})
    db.commit()
    return S.connector(c)


@router.post("/connectors/{connector_id}/health-check")
def health_check(connector_id: str, user: User = Depends(require(P.CONNECTOR_READ)), db: Session = Depends(get_db)):
    c = db.get(Connector, connector_id)
    if c is None:
        raise not_found("Connector")
    adapter = ADAPTERS[c.config.get("adapter", "manual")](c.key, c.config, c.mode)
    status, err = adapter.health()
    c.health_status = status
    if err:
        c.last_error, c.last_error_at = err, utcnow()
    db.commit()
    return S.connector(c)


# ---------------------------------------------------------------- administration
@router.get("/admin/users")
def admin_users(user: User = Depends(require(P.USER_MANAGE)), db: Session = Depends(get_db)):
    return [S.user(u) for u in db.scalars(select(User).order_by(User.role, User.email))]


@router.get("/users/reviewers")
def reviewers(user: User = Depends(require(P.FINDING_REVIEW)), db: Session = Depends(get_db)):
    return [{"id": u.id, "full_name": u.full_name} for u in
            db.scalars(select(User).where(User.role == Role.SCRUTINY_OFFICER, User.is_active.is_(True)))]


class UserIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    full_name: str = Field(min_length=2, max_length=255)
    role: Role
    institution_id: str | None = None
    password: str = Field(min_length=10, max_length=200)


@router.post("/admin/users", status_code=201)
def create_user(body: UserIn, user: User = Depends(require(P.USER_MANAGE)), db: Session = Depends(get_db)):
    if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
        raise AppError(409, "EMAIL_EXISTS", "A user with this email already exists")
    if body.role == Role.INSTITUTION_UPLOADER and not body.institution_id:
        raise bad_request("Institution uploaders must be linked to an institution")
    u = User(email=body.email.lower(), full_name=body.full_name, role=body.role.value, password_hash=hash_password(body.password),
             institution_id=body.institution_id if body.role == Role.INSTITUTION_UPLOADER else None,
             is_synthetic=not get_settings().is_production)
    db.add(u)
    db.flush()
    audit.record(db, "user.created", "user", u.id, actor=user, details={"role": u.role})
    db.commit()
    return S.user(u)


class UserPatch(BaseModel):
    role: Role | None = None
    is_active: bool | None = None


@router.patch("/admin/users/{user_id}")
def update_user(user_id: str, body: UserPatch, user: User = Depends(require(P.USER_MANAGE)), db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise not_found("User")
    if u.id == user.id and (body.is_active is False or (body.role and body.role != Role.SYSTEM_ADMIN)):
        raise bad_request("You cannot deactivate or demote your own account")
    before = {"role": u.role, "is_active": u.is_active}
    if body.role is not None:
        u.role = body.role.value
    if body.is_active is not None:
        u.is_active = body.is_active
    audit.record(db, "user.updated", "user", u.id, actor=user, details={"before": before,
                                                                       "after": {"role": u.role, "is_active": u.is_active}})
    db.commit()
    return S.user(u)


@router.get("/admin/roles")
def roles(user: User = Depends(require(P.APP_READ))):
    return {"roles": {r.value: sorted(p.value for p in perms) for r, perms in ROLE_PERMISSIONS.items()},
            "permissions": sorted(p.value for p in P)}


@router.get("/admin/settings")
def get_settings_(user: User = Depends(require(P.SETTINGS_MANAGE)), db: Session = Depends(get_db)):
    return {s.key: s.value for s in db.scalars(select(SystemSetting))}


class SettingIn(BaseModel):
    value: dict


@router.put("/admin/settings/{key}")
def put_setting(key: str, body: SettingIn, user: User = Depends(require(P.SETTINGS_MANAGE)), db: Session = Depends(get_db)):
    if key not in ("retention", "processing", "environment"):
        raise bad_request("Unknown settings key")
    row = db.get(SystemSetting, key)
    before = row.value if row else None
    if row is None:
        row = SystemSetting(key=key, value=body.value)
        db.add(row)
    row.value, row.updated_by_id = body.value, user.id
    audit.record(db, "settings.updated", "setting", None, actor=user, details={"key": key, "before": before,
                                                                               "after": body.value})
    db.commit()
    return {key: row.value}


@router.get("/admin/system")
def system_info(user: User = Depends(require(P.SETTINGS_MANAGE)), db: Session = Depends(get_db)):
    from app.agents.explainer import get_explainer

    s = get_settings()
    return {"app_env": s.app_env, "database": db.bind.dialect.name, "vector_backend":
            "pgvector" if db.bind.dialect.name == "postgresql" else "portable JSON vectors (development only)",
            "embedding": indexer.index_status(db), "ocr": {"tesseract_available": extraction.tesseract_available(),
                                                           "pipeline_version": extraction.PIPELINE_VERSION},
            "explainer": get_explainer().name, "storage_encryption": bool(s.storage_encryption_key),
            "malware_scanner": "clamav" if s.clamav_host else "builtin EICAR signature only",
            "max_upload_mb": s.max_upload_mb, "signed_url_ttl_seconds": s.signed_url_ttl_seconds,
            "audit_chain": verify_chain(db)}


def _retention_candidates(db: Session) -> list[Document]:
    days = ((db.get(SystemSetting, "retention") or SystemSetting(value={})).value or {}).get("synthetic_demo_days", 30)
    cutoff = utcnow() - timedelta(days=days)
    return db.scalars(select(Document).join(Application).where(
        Document.is_synthetic.is_(True), Document.created_at < cutoff, Document.status != "PURGED",
        Application.legal_hold.is_(False))).all()


@router.get("/admin/retention/preview")
def retention_preview(user: User = Depends(require(P.SETTINGS_MANAGE)), db: Session = Depends(get_db)):
    docs = _retention_candidates(db)
    return {"candidates": len(docs), "bytes": sum(d.size_bytes for d in docs),
            "items": [{"id": d.id, "filename": d.filename, "created_at": S.iso(d.created_at)} for d in docs[:50]],
            "policy": "Purges file content, extracted text, fields and evidence embeddings of synthetic documents past "
                      "retention. Hashes, metadata and audit events are retained for provenance. Legal holds are respected."}


@router.post("/admin/retention/execute")
def retention_execute(user: User = Depends(require(P.SETTINGS_MANAGE)), db: Session = Depends(get_db)):
    docs = _retention_candidates(db)
    for d in docs:
        storage.delete(d.storage_key)
        db.query(DocumentPage).filter(DocumentPage.document_id == d.id).delete()
        db.query(ExtractedField).filter(ExtractedField.document_id == d.id).delete()
        db.query(EvidenceChunk).filter(EvidenceChunk.document_id == d.id).delete()
        d.status = "PURGED"
    audit.record(db, "retention.executed", "system", None, actor=user,
                 details={"purged_documents": len(docs), "ids": [d.id for d in docs][:200]})
    db.commit()
    return {"purged": len(docs)}


# ---------------------------------------------------------------- guided demo
DEMO_STEPS = [
    {"n": 1, "title": "Submit a synthetic EoA application", "href": "/applications/{id}",
     "what": "A fictional institution submits 10 files: application form, certificates, a scanned affidavit (twice), "
             "and facility photographs."},
    {"n": 2, "title": "Document intelligence", "href": "/document-intelligence?application={id}",
     "what": "Text layer / OCR extraction, classification and schema-constrained fields with page-level evidence."},
    {"n": 3, "title": "Regulatory Knowledge Index", "href": "/compliance?application={id}&tab=regulatory",
     "what": "Applicable clauses retrieved as of the session reference date; superseded editions excluded."},
    {"n": 4, "title": "Institutional Evidence Index", "href": "/compliance?application={id}&tab=evidence",
     "what": "Tenant-scoped retrieval of the institution's prior approvals, deficiencies and current documents."},
    {"n": 5, "title": "Exact checks & evidence-linked findings", "href": "/applications/{id}?tab=findings",
     "what": "Deterministic rule engine and relational comparisons produce findings with citations and evidence."},
    {"n": 6, "title": "Issuer verification", "href": "/verification?application={id}",
     "what": "Mock issuer APIs return EXPIRED, MISMATCH and SOURCE_UNAVAILABLE; QR and Ed25519 signatures checked."},
    {"n": 7, "title": "Human review & audit trail", "href": "/applications/{id}?tab=audit",
     "what": "Unresolved items are routed to a reviewer who records actions; every step is in the hash-chained audit log."},
]


@router.get("/demo/scenario")
def demo_scenario(user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    from app.demo import fixtures as fx

    docs = fx.demo_application_docs(date.today())
    return {"steps": DEMO_STEPS, "enabled": not get_settings().is_production,
            "documents": [{"filename": d.filename, "declared_type": d.declared_type, "scenario": d.scenario_tag,
                           "description": d.description, "has_fixture_text": bool(d.fixture_pages)} for d in docs],
            "notes": ["All institutions, people, issuers, certificates and regulatory text are fictional.",
                      "Fixture text is used only for the scanned affidavit when 'synthetic fixture OCR' is chosen, "
                      "and is labelled 'synthetic-fixture' wherever shown.",
                      "Issuer connectors are deterministic mocks answering from labelled synthetic registries."]}


class DemoRunIn(BaseModel):
    use_fixtures: bool = True


@router.post("/demo/run", status_code=202)
def demo_run(body: DemoRunIn, user: User = Depends(require(P.DEMO_RUN)), db: Session = Depends(get_db)):
    if get_settings().is_production:
        raise AppError(403, "DEMO_DISABLED", "The guided demo is disabled in production")
    from app.demo import fixtures as fx
    from app.demo.seed import create_application, submit_and_process

    inst = db.scalar(select(Institution).where(Institution.code == fx.INST1["code"]))
    uploader = db.scalar(select(User).where(User.institution_id == inst.id, User.is_active.is_(True)))
    reviewer = user if user.role == Role.SCRUTINY_OFFICER else None
    app = create_application(db, inst, uploader, fx.demo_application_docs(date.today()),
                             declared={"scenario": "guided-demo"}, reviewer=reviewer)
    job = submit_and_process(db, app, uploader if uploader else user, body.use_fixtures, trigger="INITIAL")
    audit.record(db, "demo.started", "application", app.id, actor=user, application_id=app.id,
                 details={"use_fixtures": body.use_fixtures})
    db.commit()
    jobs.enqueue(job.id)
    return {"application_id": app.id, "reference_no": app.reference_no, "job": S.job(job)}


@router.get("/demo/sample-pack")
def sample_pack(user: User = Depends(require(P.APP_READ))):
    from app.demo import fixtures as fx

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "SYNTHETIC DEMONSTRATION FILES - fictional institution, issuers and data.\n"
                                 "Upload them to a draft application with the declared types in manifest.csv.\n")
        z.writestr("manifest.csv", "filename,declared_type,scenario\n" + "\n".join(
            f"{d.filename},{d.declared_type},\"{d.scenario_tag}\"" for d in fx.demo_application_docs(date.today())))
        for d in fx.demo_application_docs(date.today()):
            z.writestr(d.filename, d.data)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition": 'attachment; filename="intelliscrutiny-synthetic-sample-pack.zip"'})
