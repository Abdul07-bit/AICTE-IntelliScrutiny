from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.agents import verification as verification_agent
from app.agents.orchestrator import initial_steps
from app.api import serializers as S
from app.api.deps import Page, current_user, is_tenant_scoped, load_application, load_document, paginate, require
from app.core.config import get_settings
from app.core.errors import AppError, bad_request, not_found
from app.core.permissions import P, Role
from app.core.security import sign_file_token, verify_file_token
from app.db.session import get_db
from app.db.types import utcnow
from app.models import (
    Application,
    AuditEvent,
    Document,
    Finding,
    Institution,
    Job,
    ReviewAction,
    User,
    ValidationRun,
    VerificationRequest,
)
from app.services import audit, documents, jobs, storage, workflow
from app.services.notifications import notify_roles

router = APIRouter(tags=["applications"])

CATEGORIES = ["EOA", "NEW_INSTITUTION", "INCREASE_IN_INTAKE", "CHANGE_OF_NAME", "CLOSURE"]


# ---------------------------------------------------------------- applications
@router.get("/institutions")
def list_institutions(user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    q = select(Institution).order_by(Institution.code)
    if is_tenant_scoped(user):
        q = q.where(Institution.id == user.institution_id)
    return [{"id": i.id, "code": i.code, "name": i.name, "is_synthetic": i.is_synthetic} for i in db.scalars(q)]


@router.get("/applications")
def list_applications(page: Page = Depends(), q: str | None = Query(None, max_length=100),
                      status: str | None = None, category: str | None = None, institution_id: str | None = None,
                      reviewer_id: str | None = None, verification_status: str | None = None,
                      sort: str = Query("-updated_at", pattern="^-?(updated_at|submitted_at|reference_no|status)$"),
                      user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    stmt = select(Application).join(Institution)
    if is_tenant_scoped(user):
        stmt = stmt.where(Application.institution_id == user.institution_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Application.reference_no.ilike(like), Institution.name.ilike(like),
                              Institution.code.ilike(like)))
    for col, val in ((Application.status, status), (Application.category, category),
                     (Application.institution_id, institution_id), (Application.assigned_reviewer_id, reviewer_id),
                     (Application.verification_status, verification_status)):
        if val:
            stmt = stmt.where(col == val)
    col = getattr(Application, sort.lstrip("-"))
    stmt = stmt.order_by(col.desc() if sort.startswith("-") else col.asc())

    def ser(a):
        open_n = db.scalar(select(func.count()).select_from(Finding).where(
            Finding.application_id == a.id, Finding.review_status == "OPEN", Finding.severity.in_(["MEDIUM", "HIGH", "CRITICAL"])))
        docs_n = db.scalar(select(func.count()).select_from(Document).where(Document.application_id == a.id))
        out = S.application(a, {"open_findings": open_n, "documents": docs_n})
        out.pop("preliminary_summary", None)
        return out
    return paginate(db, stmt, page, ser)


class ApplicationIn(BaseModel):
    institution_id: str | None = None
    category: str = Field("EOA")
    academic_year: str = Field("2027-28", pattern=r"^\d{4}-\d{2}$")


@router.post("/applications", status_code=201)
def create_application(body: ApplicationIn, user: User = Depends(require(P.APP_CREATE)), db: Session = Depends(get_db)):
    if body.category not in CATEGORIES:
        raise bad_request("Unknown application category", {"allowed": CATEGORIES})
    inst_id = user.institution_id if is_tenant_scoped(user) else body.institution_id
    inst = db.get(Institution, inst_id) if inst_id else None
    if inst is None:
        raise bad_request("A valid institution is required")
    from app.demo.seed import next_reference

    app = Application(reference_no=next_reference(db, body.academic_year), institution_id=inst.id,
                      category=body.category, academic_year=body.academic_year, status="DRAFT",
                      created_by_id=user.id, is_synthetic=inst.is_synthetic)
    db.add(app)
    db.flush()
    audit.record(db, "application.created", "application", app.id, actor=user, application_id=app.id,
                 institution_id=inst.id, details={"reference_no": app.reference_no, "category": app.category})
    db.commit()
    return S.application(app)


@router.get("/applications/{app_id}")
def get_application(app_id: str, user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    by_cat = dict(db.execute(select(Finding.category, func.count()).where(Finding.application_id == app.id)
                             .group_by(Finding.category)).all())
    by_status = dict(db.execute(select(Finding.review_status, func.count()).where(Finding.application_id == app.id)
                                .group_by(Finding.review_status)).all())
    latest_run = db.scalar(select(ValidationRun).where(ValidationRun.application_id == app.id)
                           .order_by(ValidationRun.started_at.desc()).limit(1))
    active_job = db.scalar(select(Job).where(Job.application_id == app.id).order_by(Job.created_at.desc()).limit(1))
    out = S.application(app, {"findings_by_category": by_cat, "findings_by_review_status": by_status})
    out["allowed_transitions"] = workflow.allowed_next(app, user)
    out["latest_run"] = {"id": latest_run.id, "status": latest_run.status, "trigger": latest_run.trigger,
                         "rule_versions": latest_run.rule_versions, "pipeline_version": latest_run.pipeline_version,
                         "embedding_model": latest_run.embedding_model, "explainer": latest_run.explainer,
                         "as_of_date": S.iso(latest_run.as_of_date), "started_at": S.iso(latest_run.started_at),
                         "completed_at": S.iso(latest_run.completed_at)} if latest_run else None
    out["latest_job"] = S.job(active_job) if active_job else None
    out["declared_data"] = app.declared_data
    return out


class TransitionIn(BaseModel):
    to: str
    note: str | None = Field(None, max_length=2000)


@router.post("/applications/{app_id}/submit")
def submit(app_id: str, user: User = Depends(require(P.APP_SUBMIT)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    if not db.scalar(select(func.count()).select_from(Document).where(Document.application_id == app.id)):
        raise bad_request("Upload at least one document before submitting")
    workflow.transition(db, app, "SUBMITTED", user, action="SUBMIT")
    app.submitted_at = utcnow()
    db.commit()
    return S.application(app)


class ProcessIn(BaseModel):
    use_fixtures: bool = True


@router.post("/applications/{app_id}/process", status_code=202)
def process(app_id: str, body: ProcessIn, idempotency_key: str | None = Header(None, max_length=80),
            user: User = Depends(require(P.APP_PROCESS)), db: Session = Depends(get_db)):
    """Start (or re-run after rectification) the orchestrated validation pipeline as a background job."""
    app = load_application(db, app_id, user)
    trigger = "INITIAL"
    if app.status in ("VALIDATION_COMPLETE", "REVIEW_REQUIRED", "REVIEW_IN_PROGRESS", "READY_FOR_FURTHER_SCRUTINY"):
        trigger = "RERUN"
    elif app.status == "DRAFT":
        raise AppError(409, "NOT_SUBMITTED", "Submit the application before processing")
    elif app.status not in ("SUBMITTED",):
        raise AppError(409, "INVALID_STATE", f"Cannot process an application in {app.status}")
    if db.scalar(select(ValidationRun.id).where(ValidationRun.application_id == app.id).limit(1)) and trigger == "INITIAL":
        trigger = "RERUN_AFTER_RECTIFICATION"
    job = jobs.create_job(db, "PROCESS_APPLICATION", application_id=app.id, user=user,
                          params={"use_fixtures": body.use_fixtures, "trigger": trigger}, steps=initial_steps(),
                          idempotency_key=f"{user.id}:{idempotency_key}" if idempotency_key else None)
    if job.status == "QUEUED" and trigger.startswith("RERUN"):
        db.add(ReviewAction(application_id=app.id, actor_id=user.id, action="RERUN_VALIDATION",
                            note=f"Validation re-run ({trigger})", previous_status=app.status, new_status="PROCESSING"))
    audit.record(db, "validation.requested", "job", job.id, actor=user, application_id=app.id,
                 institution_id=app.institution_id, details={"trigger": trigger, "use_fixtures": body.use_fixtures})
    db.commit()
    if job.status == "QUEUED" and job.attempts == 0:
        jobs.enqueue(job.id)
    return S.job(job)


@router.post("/applications/{app_id}/transition")
def transition(app_id: str, body: TransitionIn, user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    if body.to in ("RECTIFICATION_REQUESTED", "READY_FOR_FURTHER_SCRUTINY") and not body.note:
        raise bad_request("A note explaining the review outcome is required")
    workflow.transition(db, app, body.to, user, note=body.note)
    if body.to == "RECTIFICATION_REQUESTED":
        app.review_outcome = "RECTIFICATION_REQUESTED"
        for u in db.scalars(select(User).where(User.institution_id == app.institution_id)):
            from app.services.notifications import notify_user

            notify_user(db, u.id, "Rectification requested", f"{app.reference_no}: {body.note}", f"/applications/{app.id}",
                        "WARNING")
    elif body.to == "READY_FOR_FURTHER_SCRUTINY":
        app.review_outcome = "READY_FOR_FURTHER_SCRUTINY"
    db.commit()
    return S.application(app)


class AssignIn(BaseModel):
    reviewer_id: str


@router.post("/applications/{app_id}/assign")
def assign(app_id: str, body: AssignIn, user: User = Depends(require(P.FINDING_REVIEW)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    reviewer = db.get(User, body.reviewer_id)
    if reviewer is None or reviewer.role != Role.SCRUTINY_OFFICER:
        raise bad_request("Reviewer must be an active scrutiny officer")
    app.assigned_reviewer_id = reviewer.id
    db.add(ReviewAction(application_id=app.id, actor_id=user.id, action="ASSIGN", payload={"reviewer_id": reviewer.id},
                        note=f"Assigned to {reviewer.full_name}"))
    audit.record(db, "application.assigned", "application", app.id, actor=user, application_id=app.id,
                 details={"reviewer_id": reviewer.id})
    db.commit()
    return S.application(app)


class OutcomeIn(BaseModel):
    outcome: str = Field(pattern="^(RECTIFICATION_REQUESTED|READY_FOR_FURTHER_SCRUTINY)$")
    note: str = Field(min_length=3, max_length=4000)


@router.post("/applications/{app_id}/review-outcome")
def review_outcome(app_id: str, body: OutcomeIn, user: User = Depends(require(P.REVIEW_OUTCOME)),
                   db: Session = Depends(get_db)):
    """Record the human reviewer's outcome. This is a scrutiny-stage outcome, not an approval decision."""
    app = load_application(db, app_id, user)
    if app.status in ("VALIDATION_COMPLETE", "REVIEW_REQUIRED"):
        workflow.transition(db, app, "REVIEW_IN_PROGRESS", user, note="Review started")
    workflow.transition(db, app, body.outcome, user, note=body.note, action="RECORD_OUTCOME")
    app.review_outcome = body.outcome
    db.commit()
    return S.application(app)


class CommentIn(BaseModel):
    note: str = Field(min_length=1, max_length=4000)


@router.post("/applications/{app_id}/comments", status_code=201)
def comment(app_id: str, body: CommentIn, user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    if user.role == Role.AUDITOR:
        raise AppError(403, "READ_ONLY", "Auditors have read-only access")
    a = ReviewAction(application_id=app.id, actor_id=user.id, action="COMMENT", note=body.note)
    db.add(a)
    db.flush()
    audit.record(db, "application.commented", "review_action", a.id, actor=user, application_id=app.id)
    db.commit()
    return S.review_action(a)


@router.get("/applications/{app_id}/activity")
def activity(app_id: str, user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    actions = db.scalars(select(ReviewAction).where(ReviewAction.application_id == app.id)
                         .order_by(ReviewAction.created_at.desc())).all()
    events = db.scalars(select(AuditEvent).where(AuditEvent.application_id == app.id)
                        .order_by(AuditEvent.seq.desc()).limit(300)).all() if not is_tenant_scoped(user) else []
    return {"review_actions": [S.review_action(a) for a in actions], "audit_events": [S.audit_event(e) for e in events]}


@router.get("/applications/{app_id}/runs")
def runs(app_id: str, user: User = Depends(require(P.FINDING_READ)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    rows = db.scalars(select(ValidationRun).where(ValidationRun.application_id == app.id)
                      .order_by(ValidationRun.started_at.desc())).all()
    return [{"id": r.id, "status": r.status, "trigger": r.trigger, "rule_versions": r.rule_versions,
             "pipeline_version": r.pipeline_version, "embedding_model": r.embedding_model, "explainer": r.explainer,
             "as_of_date": S.iso(r.as_of_date), "summary": r.summary, "started_at": S.iso(r.started_at),
             "completed_at": S.iso(r.completed_at),
             "finding_count": db.scalar(select(func.count()).select_from(Finding).where(Finding.run_id == r.id))}
            for r in rows]


@router.get("/applications/{app_id}/compliance")
def compliance_view(app_id: str, run_id: str | None = None, user: User = Depends(require(P.FINDING_READ)),
                    db: Session = Depends(get_db)):
    """Checklist plus the stored dual-retrieval reconciliation record for each rule."""
    app = load_application(db, app_id, user)
    q = select(ValidationRun).where(ValidationRun.application_id == app.id)
    q = q.where(ValidationRun.id == run_id) if run_id else q.order_by(ValidationRun.started_at.desc()).limit(1)
    run = db.scalar(q)
    if run is None:
        return {"run": None, "checklist": [], "retrievals": []}
    retrievals = run.retrievals or []
    if is_tenant_scoped(user):
        retrievals = [{**r, "regulatory_hits": r["regulatory_hits"]} for r in retrievals]
    return {"run": {"id": run.id, "as_of_date": S.iso(run.as_of_date), "rule_versions": run.rule_versions,
                    "embedding_model": run.embedding_model, "explainer": run.explainer,
                    "completed_at": S.iso(run.completed_at)},
            "checklist": (run.summary or {}).get("checklist", []), "retrievals": retrievals}


# ---------------------------------------------------------------- documents
@router.post("/applications/{app_id}/documents", status_code=201)
async def upload_document(app_id: str, file: UploadFile = File(...), declared_type: str | None = Form(None),
                          description: str | None = Form(None, max_length=200),
                          user: User = Depends(require(P.DOC_UPLOAD)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(413, "PAYLOAD_TOO_LARGE", f"File exceeds {get_settings().max_upload_mb} MB limit")
    doc = documents.ingest(db, app, user, file.filename or "upload", file.content_type, data, declared_type, description)
    db.commit()
    return S.document(doc)


@router.get("/applications/{app_id}/documents")
def app_documents(app_id: str, user: User = Depends(require(P.DOC_READ)), db: Session = Depends(get_db)):
    app = load_application(db, app_id, user)
    return [S.document(d) for d in db.scalars(select(Document).where(Document.application_id == app.id)
                                              .order_by(Document.created_at))]


@router.get("/documents")
def document_queue(page: Page = Depends(), status: str | None = None, doc_type: str | None = None,
                   method: str | None = None, application_id: str | None = None,
                   user: User = Depends(require(P.DOC_READ)), db: Session = Depends(get_db)):
    stmt = select(Document).order_by(Document.created_at.desc())
    if is_tenant_scoped(user):
        stmt = stmt.where(Document.institution_id == user.institution_id)
    for col, val in ((Document.status, status), (Document.doc_type, doc_type), (Document.extraction_method, method),
                     (Document.application_id, application_id)):
        if val:
            stmt = stmt.where(col == val)

    def ser(d):
        out = S.document(d)
        out["application_reference"] = db.scalar(select(Application.reference_no).where(Application.id == d.application_id))
        out["anomaly_count"] = db.scalar(select(func.count()).select_from(Finding).where(
            Finding.document_id == d.id, Finding.category == "SUSPECTED_ANOMALY"))
        return out
    return paginate(db, stmt, page, ser)


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, user: User = Depends(require(P.DOC_READ)), db: Session = Depends(get_db)):
    doc = load_document(db, doc_id, user)
    audit.record(db, "document.viewed", "document", doc.id, actor=user, application_id=doc.application_id)
    db.commit()
    out = S.document(doc, detail=True)
    out["findings"] = [S.finding(f) for f in db.scalars(select(Finding).where(Finding.document_id == doc.id))]
    out["verifications"] = [S.verification(v) for v in db.scalars(select(VerificationRequest)
                                                                  .where(VerificationRequest.document_id == doc.id))]
    return out


@router.get("/documents/{doc_id}/url")
def document_url(doc_id: str, user: User = Depends(require(P.DOC_READ)), db: Session = Depends(get_db)):
    doc = load_document(db, doc_id, user)
    token = sign_file_token(doc.id, user.id)
    return {"url": f"{get_settings().api_prefix}/files/{token}", "expires_in": get_settings().signed_url_ttl_seconds}


@router.get("/files/{token}")
def download(token: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from fastapi.responses import Response as RawResponse

    parsed = verify_file_token(token)
    if parsed is None or parsed[2] != user.id:
        raise AppError(403, "INVALID_FILE_TOKEN", "File link is invalid or has expired")
    doc = load_document(db, parsed[1], user)
    if doc.status == "QUARANTINED":
        raise AppError(403, "QUARANTINED", "File is quarantined by the malware scanner")
    data = storage.get(doc.storage_key, doc.encrypted)
    audit.record(db, "document.downloaded", "document", doc.id, actor=user, application_id=doc.application_id)
    db.commit()
    return RawResponse(data, media_type=doc.content_type, headers={
        "Content-Disposition": f'inline; filename="{doc.filename}"', "Cache-Control": "private, no-store",
        "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; plugin-types application/pdf",
        "X-Frame-Options": "SAMEORIGIN"})


# ---------------------------------------------------------------- findings & review
@router.get("/findings")
def list_findings(page: Page = Depends(), application_id: str | None = None, agent: str | None = None,
                  category: str | None = None, severity: str | None = None, review_status: str | None = None,
                  document_id: str | None = None, latest_only: bool = True,
                  user: User = Depends(require(P.FINDING_READ)), db: Session = Depends(get_db)):
    stmt = select(Finding).join(Application)
    if is_tenant_scoped(user):
        stmt = stmt.where(Application.institution_id == user.institution_id)
    for col, val in ((Finding.application_id, application_id), (Finding.agent, agent), (Finding.category, category),
                     (Finding.severity, severity), (Finding.review_status, review_status),
                     (Finding.document_id, document_id)):
        if val:
            stmt = stmt.where(col == val)
    if latest_only:
        latest = select(ValidationRun.id).where(ValidationRun.application_id == Finding.application_id) \
            .order_by(ValidationRun.started_at.desc()).limit(1).correlate(Finding).scalar_subquery()
        stmt = stmt.where(or_(Finding.run_id == latest, Finding.run_id.is_(None)))
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    from sqlalchemy import case

    stmt = stmt.order_by(case(order, value=Finding.severity, else_=5), Finding.created_at)
    return paginate(db, stmt, page, S.finding)


@router.get("/findings/{finding_id}")
def get_finding(finding_id: str, user: User = Depends(require(P.FINDING_READ)), db: Session = Depends(get_db)):
    f = db.get(Finding, finding_id)
    if f is None:
        raise not_found("Finding")
    load_application(db, f.application_id, user)
    actions = db.scalars(select(ReviewAction).where(ReviewAction.finding_id == f.id).order_by(ReviewAction.created_at)).all()
    return S.finding(f, actions)


FINDING_ACTIONS = {
    "ACCEPT": "ACCEPTED", "DISPUTE": "DISPUTED", "NOTE": None, "REQUEST_EVIDENCE": "EVIDENCE_REQUESTED",
    "MANUAL_ISSUER_CONFIRMATION": "ISSUER_CONFIRMATION_REQUESTED", "RECOMMEND_INSPECTION": "INSPECTION_RECOMMENDED",
    "RESOLVE": "RESOLVED", "REOPEN": "OPEN",
}


class FindingActionIn(BaseModel):
    action: str
    note: str | None = Field(None, max_length=4000)


@router.post("/findings/{finding_id}/actions", status_code=201)
def finding_action(finding_id: str, body: FindingActionIn, user: User = Depends(require(P.FINDING_REVIEW)),
                   db: Session = Depends(get_db)):
    """Human review of an automated observation. The original finding text is never modified;
    each action is appended to the review history."""
    f = db.get(Finding, finding_id)
    if f is None:
        raise not_found("Finding")
    app = load_application(db, f.application_id, user)
    if body.action not in FINDING_ACTIONS:
        raise bad_request("Unknown action", {"allowed": list(FINDING_ACTIONS)})
    if body.action in ("DISPUTE", "RESOLVE", "REQUEST_EVIDENCE", "RECOMMEND_INSPECTION") and not body.note:
        raise bad_request("A note is required for this action")
    prev = f.review_status
    new = FINDING_ACTIONS[body.action] or prev
    payload = {}
    if body.action == "MANUAL_ISSUER_CONFIRMATION":
        doc = db.get(Document, f.document_id) if f.document_id else None
        vr = verification_agent.create_manual_request(db, app, doc, body.note or f.title, user, origin=f.id)
        payload["verification_request_id"] = vr.id
        verification_agent.refresh_status(db, app)
    if body.action == "REQUEST_EVIDENCE":
        for u in db.scalars(select(User).where(User.institution_id == app.institution_id)):
            from app.services.notifications import notify_user

            notify_user(db, u.id, "Additional evidence requested", f"{app.reference_no}: {body.note}",
                        f"/applications/{app.id}", "WARNING")
    if body.action == "RECOMMEND_INSPECTION":
        notify_roles(db, [Role.SCRUTINY_OFFICER], "Physical inspection recommended", f"{app.reference_no}: {body.note}",
                     f"/infrastructure?application={app.id}")
    f.review_status = new
    a = ReviewAction(application_id=app.id, finding_id=f.id, actor_id=user.id, action=body.action, note=body.note,
                     previous_status=prev, new_status=new, payload=payload)
    db.add(a)
    if app.status in ("REVIEW_REQUIRED", "VALIDATION_COMPLETE") and user.role == Role.SCRUTINY_OFFICER:
        workflow.transition(db, app, "REVIEW_IN_PROGRESS", user, note="Review started")
    db.flush()
    audit.record(db, f"finding.{body.action.lower()}", "finding", f.id, actor=user, application_id=app.id,
                 institution_id=app.institution_id,
                 details={"from": prev, "to": new, "note": body.note, "code": f.code, "rule": f.rule_code,
                          "rule_version": f.rule_version},
                 source_refs=[{"chunk_id": c.get("chunk_id"), "clause_ref": c.get("clause_ref")}
                              for c in (f.regulatory_citations or [])[:3]])
    db.commit()
    return S.review_action(a)


# ---------------------------------------------------------------- verification
@router.get("/verification/requests")
def list_verifications(page: Page = Depends(), status: str | None = None, connector_key: str | None = None,
                       application_id: str | None = None, manual_queue: bool = False,
                       user: User = Depends(require(P.APP_READ)), db: Session = Depends(get_db)):
    stmt = select(VerificationRequest).join(Application).order_by(VerificationRequest.requested_at.desc())
    if is_tenant_scoped(user):
        stmt = stmt.where(Application.institution_id == user.institution_id)
    if manual_queue:
        stmt = stmt.where(VerificationRequest.connector_key == "manual", VerificationRequest.manual_outcome.is_(None))
    for col, val in ((VerificationRequest.status, status), (VerificationRequest.connector_key, connector_key),
                     (VerificationRequest.application_id, application_id)):
        if val:
            stmt = stmt.where(col == val)

    def ser(v):
        out = S.verification(v)
        out["application_reference"] = db.scalar(select(Application.reference_no).where(Application.id == v.application_id))
        return out
    return paginate(db, stmt, page, ser)


class VerifyIn(BaseModel):
    document_ids: list[str] | None = None


@router.post("/applications/{app_id}/verify")
def run_verification(app_id: str, body: VerifyIn, user: User = Depends(require(P.VERIFY_REQUEST)),
                     db: Session = Depends(get_db)):
    """Re-run issuer verification. Outcomes always come from connectors, never from the client."""
    app = load_application(db, app_id, user)
    counts = verification_agent.run_verification(db, app, None, user, body.document_ids)
    db.commit()
    return {"results": counts, "verification_status": app.verification_status}


class ManualOutcomeIn(BaseModel):
    outcome: str = Field(pattern="^(VERIFIED|MISMATCH_DETECTED|EXPIRED|MANUAL_REVIEW_REQUIRED|NOT_APPLICABLE)$")
    note: str = Field(min_length=5, max_length=4000)
    issuer_contact_reference: str | None = Field(None, max_length=200)


@router.post("/verification/requests/{req_id}/manual-outcome")
def manual_outcome(req_id: str, body: ManualOutcomeIn, user: User = Depends(require(P.VERIFY_CONFIRM)),
                   db: Session = Depends(get_db)):
    """Record an authorized officer's manual issuer confirmation (with the evidence reference used)."""
    vr = db.get(VerificationRequest, req_id)
    if vr is None:
        raise not_found("Verification request")
    app = load_application(db, vr.application_id, user)
    if vr.manual_outcome:
        raise AppError(409, "ALREADY_RECORDED", "A manual outcome has already been recorded")
    vr.manual_outcome, vr.manual_note, vr.manual_reviewer_id, vr.manual_at = body.outcome, body.note, user.id, utcnow()
    vr.response_meta = {**(vr.response_meta or {}), "issuer_contact_reference": body.issuer_contact_reference}
    db.add(ReviewAction(application_id=app.id, actor_id=user.id, action="MANUAL_VERIFICATION_OUTCOME", note=body.note,
                        new_status=body.outcome, payload={"verification_request_id": vr.id}))
    audit.record(db, "verification.manual_outcome", "verification_request", vr.id, actor=user, application_id=app.id,
                 details={"outcome": body.outcome, "issuer_contact_reference": body.issuer_contact_reference})
    verification_agent.refresh_status(db, app)
    db.commit()
    return S.verification(vr)


@router.post("/verification/requests/{req_id}/retry")
def retry_verification(req_id: str, user: User = Depends(require(P.VERIFY_REQUEST)), db: Session = Depends(get_db)):
    vr = db.get(VerificationRequest, req_id)
    if vr is None or not vr.document_id:
        raise not_found("Verification request")
    app = load_application(db, vr.application_id, user)
    counts = verification_agent.run_verification(db, app, None, user, [vr.document_id])
    db.commit()
    return {"results": counts, "verification_status": app.verification_status}
