"""Relational schema. Exact checks (identity, programme, intake, dates, payments) run on
these tables; the two vector tables exist only for contextual retrieval."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import get_settings
from app.db.session import Base
from app.db.types import EmbeddingType, UTCDateTime, new_id, utcnow

EMBED_DIM = get_settings().embedding_dim


def pk() -> Mapped[str]:
    return mapped_column(String(36), primary_key=True, default=new_id)


def created() -> Mapped[datetime]:
    return mapped_column(UTCDateTime(), default=utcnow, nullable=False)


# ---------------------------------------------------------------- tenants & users
class Institution(Base):
    __tablename__ = "institutions"
    id: Mapped[str] = pk()
    code: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    aliases: Mapped[list] = mapped_column(JSON, default=list)
    state: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(255))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = pk()
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(40), index=True)
    institution_id: Mapped[str | None] = mapped_column(ForeignKey("institutions.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = created()

    institution: Mapped[Institution | None] = relationship()


# ---------------------------------------------------------------- applications
class Application(Base):
    __tablename__ = "applications"
    id: Mapped[str] = pk()
    reference_no: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    category: Mapped[str] = mapped_column(String(40), index=True)  # EOA, ...
    academic_year: Mapped[str] = mapped_column(String(9))
    status: Mapped[str] = mapped_column(String(40), index=True, default="DRAFT")
    # Issuer verification progresses independently from the workflow status.
    verification_status: Mapped[str] = mapped_column(String(40), default="NOT_STARTED")
    declared_data: Mapped[dict] = mapped_column(JSON, default=dict)
    preliminary_summary: Mapped[dict | None] = mapped_column(JSON)
    review_outcome: Mapped[str | None] = mapped_column(String(60))
    assigned_reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    legal_hold: Mapped[bool] = mapped_column(Boolean, default=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    institution: Mapped[Institution] = relationship()
    assigned_reviewer: Mapped[User | None] = relationship(foreign_keys=[assigned_reviewer_id])


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[str] = pk()
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(255))
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    declared_type: Mapped[str | None] = mapped_column(String(60))
    doc_type: Mapped[str | None] = mapped_column(String(60), index=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), default="UPLOADED")
    malware_scan_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    malware_scan_engine: Mapped[str | None] = mapped_column(String(60))
    page_count: Mapped[int | None] = mapped_column(Integer)
    file_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    extraction_method: Mapped[str | None] = mapped_column(String(40))
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    processing_error: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(String(36))
    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()

    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_no"
    )
    fields: Mapped[list["ExtractedField"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    id: Mapped[str] = pk()
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, default="")
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{text, bbox:[x0,y0,x1,y1]}]
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(40))  # pdf-text-layer | tesseract-ocr | synthetic-fixture | none
    confidence: Mapped[float | None] = mapped_column(Float)

    document: Mapped[Document] = relationship(back_populates="pages")


class ExtractedField(Base):
    __tablename__ = "extracted_fields"
    id: Mapped[str] = pk()
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    value: Mapped[str | None] = mapped_column(Text)
    normalized_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    page_no: Mapped[int | None] = mapped_column(Integer)
    bbox: Mapped[list | None] = mapped_column(JSON)
    method: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = created()

    document: Mapped[Document] = relationship(back_populates="fields")


# ---------------------------------------------------------------- institutional records
class InstitutionalRecord(Base):
    """Authorized prior records (approvals, accreditation, payments, previous deficiencies)."""

    __tablename__ = "institutional_records"
    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    record_type: Mapped[str] = mapped_column(String(40), index=True)
    reference: Mapped[str] = mapped_column(String(80))
    academic_year: Mapped[str | None] = mapped_column(String(9))
    title: Mapped[str] = mapped_column(String(255))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(255))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, default=1)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- regulatory knowledge
class RegulatorySource(Base):
    __tablename__ = "regulatory_sources"
    id: Mapped[str] = pk()
    title: Mapped[str] = mapped_column(String(255))
    edition: Mapped[str] = mapped_column(String(60))
    source_type: Mapped[str] = mapped_column(String(40))  # HANDBOOK|AMENDMENT|CIRCULAR|CHECKLIST|INTERNAL_INTERPRETATION
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    publication_date: Mapped[date | None] = mapped_column(Date)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE|SUPERSEDED|WITHDRAWN
    superseded_by_id: Mapped[str | None] = mapped_column(String(36))
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(255))
    page_count: Mapped[int | None] = mapped_column(Integer)
    index_status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING|INDEXED|FAILED|STALE
    index_error: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    indexed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()


class RegulatoryChunk(Base):
    """INDEX 1 — Regulatory Knowledge Embedding Index."""

    __tablename__ = "regulatory_chunks"
    id: Mapped[str] = pk()
    source_id: Mapped[str] = mapped_column(ForeignKey("regulatory_sources.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    page_no: Mapped[int | None] = mapped_column(Integer)
    clause_ref: Mapped[str | None] = mapped_column(String(60))
    heading: Mapped[str | None] = mapped_column(String(255))
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list | None] = mapped_column(EmbeddingType(EMBED_DIM))
    embedding_model: Mapped[str] = mapped_column(String(80))
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    indexed_at: Mapped[datetime] = created()

    source: Mapped[RegulatorySource] = relationship()


class EvidenceChunk(Base):
    """INDEX 2 — Institutional Evidence Embedding Index. Always filtered by tenant in SQL."""

    __tablename__ = "evidence_chunks"
    id: Mapped[str] = pk()
    institution_id: Mapped[str] = mapped_column(ForeignKey("institutions.id"), index=True)
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    record_id: Mapped[str | None] = mapped_column(ForeignKey("institutional_records.id", ondelete="CASCADE"))
    source_kind: Mapped[str] = mapped_column(String(30))  # DOCUMENT | RECORD
    doc_type: Mapped[str | None] = mapped_column(String(60))
    page_no: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)  # PII-redacted
    content_hash: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list | None] = mapped_column(EmbeddingType(EMBED_DIM))
    embedding_model: Mapped[str] = mapped_column(String(80))
    allowed_roles: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    source_label: Mapped[str] = mapped_column(String(255))
    indexed_at: Mapped[datetime] = created()


# ---------------------------------------------------------------- rule registry
class Rule(Base):
    __tablename__ = "rules"
    __table_args__ = (UniqueConstraint("rule_code", "version", name="uq_rule_code_version"),)
    id: Mapped[str] = pk()
    rule_code: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str | None] = mapped_column(ForeignKey("regulatory_sources.id"))
    clause_ref: Mapped[str | None] = mapped_column(String(60))
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    required_documents: Mapped[list] = mapped_column(JSON, default=list)
    required_fields: Mapped[list] = mapped_column(JSON, default=list)
    condition: Mapped[dict] = mapped_column(JSON, default=dict)
    severity: Mapped[str] = mapped_column(String(20), default="MEDIUM")
    finding_category: Mapped[str] = mapped_column(String(40), default="UNMET_REQUIREMENT")
    reviewer_guidance: Mapped[str | None] = mapped_column(Text)
    retrieval_query: Mapped[str | None] = mapped_column(Text)
    test_cases: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")  # DRAFT|PENDING_APPROVAL|APPROVED|RETIRED
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    change_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()

    source: Mapped[RegulatorySource | None] = relationship()


# ---------------------------------------------------------------- processing
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = pk()
    kind: Mapped[str] = mapped_column(String(40))  # PROCESS_APPLICATION | INDEX_SOURCE | EVALUATION
    application_id: Mapped[str | None] = mapped_column(ForeignKey("applications.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="QUEUED")  # QUEUED|RUNNING|SUCCEEDED|PARTIAL|FAILED|CANCELLED
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True)
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ValidationRun(Base):
    """One evaluation of an application. Pins rule versions and pipeline/model versions so
    later registry edits never rewrite historical results."""

    __tablename__ = "validation_runs"
    id: Mapped[str] = pk()
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    trigger: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    rule_versions: Mapped[list] = mapped_column(JSON, default=list)
    pipeline_version: Mapped[str] = mapped_column(String(40))
    embedding_model: Mapped[str] = mapped_column(String(80))
    explainer: Mapped[str | None] = mapped_column(String(80))
    as_of_date: Mapped[date | None] = mapped_column(Date)
    summary: Mapped[dict | None] = mapped_column(JSON)
    retrievals: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = created()
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[str] = pk()
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("validation_runs.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    agent: Mapped[str] = mapped_column(String(30))  # VALIDATION|VERIFICATION|COMPLIANCE|INFRASTRUCTURE|ORCHESTRATOR
    category: Mapped[str] = mapped_column(String(40))  # UNMET_REQUIREMENT|UNRESOLVED_EVIDENCE|SUSPECTED_ANOMALY|MACHINE_OBSERVATION|INFORMATION
    code: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(20))  # INFO|LOW|MEDIUM|HIGH|CRITICAL
    reason: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_method: Mapped[str | None] = mapped_column(String(60))
    observed_value: Mapped[str | None] = mapped_column(Text)
    expected_value: Mapped[str | None] = mapped_column(Text)
    rule_id: Mapped[str | None] = mapped_column(ForeignKey("rules.id"))
    rule_code: Mapped[str | None] = mapped_column(String(60))
    rule_version: Mapped[int | None] = mapped_column(Integer)
    page_no: Mapped[int | None] = mapped_column(Integer)
    region: Mapped[list | None] = mapped_column(JSON)
    regulatory_citations: Mapped[list] = mapped_column(JSON, default=list)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    comparison: Mapped[dict | None] = mapped_column(JSON)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(30), default="OPEN")
    is_machine_generated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = created()

    document: Mapped[Document | None] = relationship()


class ReviewAction(Base):
    """Append-only human actions. Original findings are never edited in place."""

    __tablename__ = "review_actions"
    id: Mapped[str] = pk()
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    finding_id: Mapped[str | None] = mapped_column(ForeignKey("findings.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(Text)
    previous_status: Mapped[str | None] = mapped_column(String(40))
    new_status: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = created()

    actor: Mapped[User] = relationship()


# ---------------------------------------------------------------- verification
class Connector(Base):
    __tablename__ = "connectors"
    id: Mapped[str] = pk()
    key: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(30))  # ISSUER_API|QR|DIGITAL_SIGNATURE|PORTAL|DIGILOCKER|MANUAL
    mode: Mapped[str] = mapped_column(String(10), default="MOCK")  # MOCK|LIVE
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    issuer_name: Mapped[str | None] = mapped_column(String(255))
    doc_types: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)  # non-secret; secrets referenced by env var name
    timeout_ms: Mapped[int] = mapped_column(Integer, default=3000)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    health_status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    authorization_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created()


class VerificationRequest(Base):
    __tablename__ = "verification_requests"
    id: Mapped[str] = pk()
    application_id: Mapped[str] = mapped_column(ForeignKey("applications.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("documents.id", ondelete="SET NULL"))
    connector_key: Mapped[str] = mapped_column(String(60))
    request_ref: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    query: Mapped[dict] = mapped_column(JSON, default=dict)
    response_meta: Mapped[dict] = mapped_column(JSON, default=dict)
    mismatches: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    requested_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = created()
    responded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    manual_outcome: Mapped[str | None] = mapped_column(String(30))
    manual_note: Mapped[str | None] = mapped_column(Text)
    manual_reviewer_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    manual_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    document: Mapped[Document | None] = relationship()


# ---------------------------------------------------------------- governance
class AuditEvent(Base):
    """Append-only, hash-chained audit log."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[str] = pk()
    seq: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = created()
    actor_id: Mapped[str | None] = mapped_column(String(36))
    actor_email: Mapped[str | None] = mapped_column(String(255))
    actor_role: Mapped[str | None] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(60), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(36))
    application_id: Mapped[str | None] = mapped_column(String(36), index=True)
    institution_id: Mapped[str | None] = mapped_column(String(36))
    request_id: Mapped[str | None] = mapped_column(String(40))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    source_refs: Mapped[list] = mapped_column(JSON, default=list)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = pk()
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(20), default="INFO")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = created()


class SystemSetting(Base):
    __tablename__ = "system_settings"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[str] = pk()
    label: Mapped[str] = mapped_column(String(255))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    sample_counts: Mapped[dict] = mapped_column(JSON, default=dict)
    pipeline_version: Mapped[str] = mapped_column(String(40))
    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = created()
