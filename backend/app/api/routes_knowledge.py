from __future__ import annotations

from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.validation import build_context
from app.api import serializers as S
from app.api.deps import is_tenant_scoped, load_application, require
from app.core.config import get_settings
from app.core.errors import AppError, bad_request, conflict, not_found
from app.core.permissions import P
from app.db.session import get_db
from app.db.types import utcnow
from app.models import RegulatoryChunk, RegulatorySource, Rule, User
from app.rag import indexer, retrieval
from app.rules import engine
from app.services import audit, jobs, storage

router = APIRouter(tags=["knowledge"])


# ---------------------------------------------------------------- dual-index search
@router.get("/rag/status")
def rag_status(user: User = Depends(require(P.SEARCH_REGULATORY)), db: Session = Depends(get_db)):
    return indexer.index_status(db)


class RegSearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    as_of: date | None = None
    k: int = Field(5, ge=1, le=20)
    source_types: list[str] | None = None
    include_superseded: bool = False


@router.post("/rag/search/regulatory")
def search_regulatory(body: RegSearchIn, user: User = Depends(require(P.SEARCH_REGULATORY)),
                      db: Session = Depends(get_db)):
    hits = retrieval.search_regulatory(db, body.query, as_of=body.as_of, k=body.k, source_types=body.source_types,
                                       include_superseded=body.include_superseded)
    return {"index": "REGULATORY_KNOWLEDGE", "query": body.query, "as_of": S.iso(body.as_of), "hits": hits}


class EvSearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    institution_id: str | None = None
    application_id: str | None = None
    include_prior_records: bool = True
    k: int = Field(6, ge=1, le=20)


@router.post("/rag/search/evidence")
def search_evidence(body: EvSearchIn, user: User = Depends(require(P.SEARCH_EVIDENCE)), db: Session = Depends(get_db)):
    inst_id = body.institution_id
    if body.application_id:
        app = load_application(db, body.application_id, user)
        inst_id = app.institution_id
    if is_tenant_scoped(user):
        inst_id = user.institution_id  # cannot be widened by the client
    if not inst_id:
        raise bad_request("institution_id or application_id is required")
    hits = retrieval.search_evidence(db, body.query, user=user, institution_id=inst_id,
                                     application_id=body.application_id,
                                     include_prior_records=body.include_prior_records, k=body.k)
    audit.record(db, "evidence.searched", "institution", inst_id, actor=user, application_id=body.application_id,
                 details={"query": body.query[:200], "hits": len(hits)})
    db.commit()
    return {"index": "INSTITUTIONAL_EVIDENCE", "query": body.query, "institution_id": inst_id, "hits": hits}


# ---------------------------------------------------------------- regulatory sources
@router.get("/regulatory/sources")
def list_sources(user: User = Depends(require(P.RULE_READ)), db: Session = Depends(get_db)):
    return [S.source(s) for s in db.scalars(select(RegulatorySource).order_by(RegulatorySource.created_at))]


@router.get("/regulatory/sources/{source_id}/chunks")
def source_chunks(source_id: str, user: User = Depends(require(P.RULE_READ)), db: Session = Depends(get_db)):
    src = db.get(RegulatorySource, source_id)
    if src is None:
        raise not_found("Source")
    chunks = db.scalars(select(RegulatoryChunk).where(RegulatoryChunk.source_id == src.id)
                        .order_by(RegulatoryChunk.chunk_index)).all()
    return {"source": S.source(src), "chunks": [
        {"id": c.id, "chunk_index": c.chunk_index, "page": c.page_no, "clause_ref": c.clause_ref, "heading": c.heading,
         "text": c.text, "status": c.status, "embedding_model": c.embedding_model, "content_hash": c.content_hash,
         "indexed_at": S.iso(c.indexed_at)} for c in chunks]}


@router.post("/regulatory/sources", status_code=202)
async def upload_source(file: UploadFile = File(...), title: str = Form(..., max_length=255),
                        edition: str = Form(..., max_length=60),
                        source_type: str = Form(..., pattern="^(HANDBOOK|AMENDMENT|CIRCULAR|CHECKLIST|INTERNAL_INTERPRETATION)$"),
                        is_official: bool = Form(False), effective_from: date | None = Form(None),
                        effective_to: date | None = Form(None), publication_date: date | None = Form(None),
                        source_url: str | None = Form(None, max_length=500),
                        user: User = Depends(require(P.SOURCE_MANAGE)), db: Session = Depends(get_db)):
    """Import an authorized source document into the Regulatory Knowledge Index. Marking a
    source as official is an explicit administrator assertion and is audited."""
    data = await file.read(get_settings().max_upload_mb * 1024 * 1024 + 1)
    if storage.validate_upload(data, file.content_type, file.filename or "") != "application/pdf":
        raise AppError(415, "UNSUPPORTED_TYPE", "Regulatory sources must be PDF")
    if source_type == "INTERNAL_INTERPRETATION" and is_official:
        raise bad_request("Internal interpretations cannot be marked as official legal text")
    key, _ = storage.put(data, "regulatory", ".pdf")
    src = RegulatorySource(title=title, edition=edition, source_type=source_type, is_official=is_official,
                           is_synthetic=not is_official, effective_from=effective_from, effective_to=effective_to,
                           publication_date=publication_date, source_url=source_url, sha256=storage.sha256(data),
                           storage_key=key, uploaded_by_id=user.id)
    db.add(src)
    db.flush()
    job = jobs.create_job(db, "INDEX_SOURCE", params={"source_id": src.id}, user=user)
    audit.record(db, "regulatory_source.uploaded", "regulatory_source", src.id, actor=user,
                 details={"title": title, "edition": edition, "is_official": is_official, "sha256": src.sha256})
    db.commit()
    jobs.enqueue(job.id)
    return {"source": S.source(src), "job": S.job(job)}


@router.post("/regulatory/sources/{source_id}/reindex", status_code=202)
def reindex(source_id: str, user: User = Depends(require(P.SOURCE_MANAGE)), db: Session = Depends(get_db)):
    src = db.get(RegulatorySource, source_id)
    if src is None:
        raise not_found("Source")
    src.index_status = "PENDING"
    job = jobs.create_job(db, "INDEX_SOURCE", params={"source_id": src.id}, user=user)
    audit.record(db, "regulatory_source.reindex_requested", "regulatory_source", src.id, actor=user)
    db.commit()
    jobs.enqueue(job.id)
    return S.job(job)


class SupersedeIn(BaseModel):
    superseded_by_id: str


@router.post("/regulatory/sources/{source_id}/supersede")
def supersede(source_id: str, body: SupersedeIn, user: User = Depends(require(P.SOURCE_MANAGE)),
              db: Session = Depends(get_db)):
    old, new = db.get(RegulatorySource, source_id), db.get(RegulatorySource, body.superseded_by_id)
    if old is None or new is None or old.id == new.id:
        raise bad_request("Both sources must exist and differ")
    indexer.supersede(db, old, new)
    audit.record(db, "regulatory_source.superseded", "regulatory_source", old.id, actor=user,
                 details={"superseded_by": new.id, "effective_to": S.iso(old.effective_to)})
    db.commit()
    return S.source(old)


# ---------------------------------------------------------------- rule registry
@router.get("/rules")
def list_rules(status: str | None = None, rule_code: str | None = None,
               user: User = Depends(require(P.RULE_READ)), db: Session = Depends(get_db)):
    q = select(Rule).order_by(Rule.rule_code, Rule.version.desc())
    if status:
        q = q.where(Rule.status == status)
    if rule_code:
        q = q.where(Rule.rule_code == rule_code)
    return [S.rule(r) for r in db.scalars(q)]


@router.get("/rules/operators")
def operators(user: User = Depends(require(P.RULE_READ))):
    return {"operators": sorted(engine.OPS), "combinators": ["all", "any"],
            "context_paths": ["application.reference_date", "application.submission_date",
                              "application.institution_name", "application.institution_code"]}


@router.get("/rules/{rule_id}")
def get_rule(rule_id: str, user: User = Depends(require(P.RULE_READ)), db: Session = Depends(get_db)):
    r = db.get(Rule, rule_id)
    if r is None:
        raise not_found("Rule")
    out = S.rule(r)
    out["versions"] = [{"id": v.id, "version": v.version, "status": v.status, "created_at": S.iso(v.created_at),
                        "change_note": v.change_note}
                       for v in db.scalars(select(Rule).where(Rule.rule_code == r.rule_code).order_by(Rule.version.desc()))]
    return out


class RuleIn(BaseModel):
    rule_code: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,59}$")
    title: str = Field(min_length=3, max_length=255)
    description: str = Field(min_length=3, max_length=4000)
    category: str = "EOA"
    source_id: str | None = None
    clause_ref: str | None = Field(None, max_length=60)
    effective_from: date | None = None
    effective_to: date | None = None
    required_documents: list[str] = []
    required_fields: list[str] = []
    condition: dict
    severity: str = Field("MEDIUM", pattern="^(INFO|LOW|MEDIUM|HIGH|CRITICAL)$")
    finding_category: str = Field("UNMET_REQUIREMENT", pattern="^(UNMET_REQUIREMENT|UNRESOLVED_EVIDENCE|SUSPECTED_ANOMALY)$")
    reviewer_guidance: str | None = None
    retrieval_query: str | None = None
    test_cases: list[dict] = []
    change_note: str | None = Field(None, max_length=1000)


@router.post("/rules", status_code=201)
def create_rule(body: RuleIn, user: User = Depends(require(P.RULE_EDIT)), db: Session = Depends(get_db)):
    """Creates a new DRAFT version. Existing versions are never edited, so historical
    evaluations stay reproducible."""
    errors = engine.validate_condition(body.condition)
    if errors:
        raise bad_request("Invalid rule condition", {"errors": errors})
    if body.source_id and db.get(RegulatorySource, body.source_id) is None:
        raise bad_request("Unknown source_id")
    latest = db.scalar(select(func.max(Rule.version)).where(Rule.rule_code == body.rule_code)) or 0
    src = db.get(RegulatorySource, body.source_id) if body.source_id else None
    r = Rule(**body.model_dump(), version=latest + 1, status="DRAFT", created_by_id=user.id,
             is_synthetic=bool(src is None or src.is_synthetic))
    db.add(r)
    db.flush()
    audit.record(db, "rule.created", "rule", r.id, actor=user,
                 details={"rule_code": r.rule_code, "version": r.version, "condition": r.condition})
    db.commit()
    return S.rule(r)


def _rule(db: Session, rule_id: str) -> Rule:
    r = db.get(Rule, rule_id)
    if r is None:
        raise not_found("Rule")
    return r


def _set_status(db, r: Rule, user: User, to: str, allowed_from: tuple[str, ...]) -> Rule:
    if r.status not in allowed_from:
        raise conflict(f"Rule is {r.status}; expected one of {allowed_from}")
    prev = r.status
    r.status = to
    audit.record(db, f"rule.{to.lower()}", "rule", r.id, actor=user,
                 details={"rule_code": r.rule_code, "version": r.version, "from": prev, "to": to})
    return r


@router.post("/rules/{rule_id}/submit")
def submit_rule(rule_id: str, user: User = Depends(require(P.RULE_EDIT)), db: Session = Depends(get_db)):
    r = _rule(db, rule_id)
    failing = [t for t in engine.run_tests(r) if not t["passed"]]
    if failing:
        raise bad_request("Rule test cases must pass before submission", {"failing": [t["name"] for t in failing]})
    _set_status(db, r, user, "PENDING_APPROVAL", ("DRAFT",))
    db.commit()
    return S.rule(r)


@router.post("/rules/{rule_id}/approve")
def approve_rule(rule_id: str, user: User = Depends(require(P.RULE_APPROVE)), db: Session = Depends(get_db)):
    r = _rule(db, rule_id)
    sod_waived = False
    if r.created_by_id == user.id:
        if get_settings().is_production:
            raise AppError(403, "SEPARATION_OF_DUTIES", "A rule must be approved by someone other than its author")
        sod_waived = True
    _set_status(db, r, user, "APPROVED", ("PENDING_APPROVAL",))
    r.approved_by_id, r.approved_at = user.id, utcnow()
    if sod_waived:
        r.change_note = (r.change_note or "") + " [separation-of-duties waived in demo environment]"
    db.commit()
    return S.rule(r)


@router.post("/rules/{rule_id}/retire")
def retire_rule(rule_id: str, user: User = Depends(require(P.RULE_APPROVE)), db: Session = Depends(get_db)):
    r = _rule(db, rule_id)
    _set_status(db, r, user, "RETIRED", ("APPROVED", "DRAFT", "PENDING_APPROVAL"))
    db.commit()
    return S.rule(r)


class RuleTestIn(BaseModel):
    application_id: str | None = None
    condition: dict | None = None


@router.post("/rules/{rule_id}/test")
def test_rule(rule_id: str, body: RuleTestIn, user: User = Depends(require(P.RULE_READ)), db: Session = Depends(get_db)):
    """Runs the rule's labelled test cases and, optionally, a non-persisted preview against an
    application (useful to see the effect of a draft condition before approval)."""
    r = _rule(db, rule_id)
    probe = r
    if body.condition is not None:
        errors = engine.validate_condition(body.condition)
        if errors:
            raise bad_request("Invalid rule condition", {"errors": errors})
        probe = Rule(rule_code=r.rule_code, version=r.version, condition=body.condition, test_cases=r.test_cases)
    out = {"tests": engine.run_tests(probe), "preview": None}
    if body.application_id:
        app = load_application(db, body.application_id, user)
        res = engine.evaluate(probe, build_context(db, app))
        out["preview"] = {"application_id": app.id, "reference_no": app.reference_no, "result": res.result,
                          "outcomes": [asdict(o) for o in res.outcomes], "persisted": False}
    return out
