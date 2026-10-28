"""Explicit response shaping (no ORM objects leak to clients)."""
from __future__ import annotations

from app.core.permissions import permissions_for
from app.models import (
    Application,
    AuditEvent,
    Connector,
    Document,
    Finding,
    Job,
    RegulatorySource,
    ReviewAction,
    Rule,
    User,
    VerificationRequest,
)
from app.services.workflow import STATE_LABELS


def iso(v):
    return v.isoformat() if v is not None else None


def user(u: User, with_perms: bool = False) -> dict:
    d = {"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role, "institution_id": u.institution_id,
         "institution_name": u.institution.name if u.institution else None, "is_active": u.is_active,
         "is_synthetic": u.is_synthetic, "last_login_at": iso(u.last_login_at)}
    if with_perms:
        d["permissions"] = sorted(p.value for p in permissions_for(u.role))
    return d


def application(a: Application, counts: dict | None = None) -> dict:
    return {
        "id": a.id, "reference_no": a.reference_no, "category": a.category, "academic_year": a.academic_year,
        "status": a.status, "status_label": STATE_LABELS.get(a.status, a.status.replace("_", " ").title()),
        "verification_status": a.verification_status,
        "institution": {"id": a.institution.id, "code": a.institution.code, "name": a.institution.name,
                        "is_synthetic": a.institution.is_synthetic},
        "assigned_reviewer": {"id": a.assigned_reviewer.id, "full_name": a.assigned_reviewer.full_name}
        if a.assigned_reviewer else None,
        "submitted_at": iso(a.submitted_at), "validated_at": iso(a.validated_at), "created_at": iso(a.created_at),
        "updated_at": iso(a.updated_at), "review_outcome": a.review_outcome, "is_synthetic": a.is_synthetic,
        "legal_hold": a.legal_hold, "preliminary_summary": a.preliminary_summary, **({"counts": counts} if counts else {}),
    }


def document(d: Document, detail: bool = False) -> dict:
    meta = d.file_metadata or {}
    out = {
        "id": d.id, "application_id": d.application_id, "filename": d.filename, "content_type": d.content_type,
        "size_bytes": d.size_bytes, "sha256": d.sha256, "declared_type": d.declared_type, "doc_type": d.doc_type,
        "classification_confidence": d.classification_confidence, "status": d.status,
        "malware_scan_status": d.malware_scan_status, "malware_scan_engine": d.malware_scan_engine,
        "page_count": d.page_count, "extraction_method": d.extraction_method,
        "extraction_confidence": d.extraction_confidence, "processing_error": d.processing_error,
        "version": d.version, "encrypted": d.encrypted, "is_synthetic": d.is_synthetic,
        "description": meta.get("description"), "created_at": iso(d.created_at),
        "has_fixture_text": bool(meta.get("fixture_pages")), "field_count": len(d.fields),
    }
    if detail:
        out["metadata"] = {k: v for k, v in meta.items() if k not in ("fixture_pages",)}
        out["pages"] = [{"page_no": p.page_no, "text": p.text, "lines": p.lines, "method": p.method,
                         "confidence": p.confidence, "width": p.width, "height": p.height} for p in d.pages]
        out["fields"] = [{"id": f.id, "name": f.name, "value": f.value, "normalized_value": f.normalized_value,
                          "confidence": f.confidence, "page_no": f.page_no, "bbox": f.bbox, "method": f.method}
                         for f in d.fields]
    return out


def finding(f: Finding, actions: list[ReviewAction] | None = None) -> dict:
    out = {
        "id": f.id, "application_id": f.application_id, "run_id": f.run_id, "document_id": f.document_id,
        "document_filename": f.document.filename if f.document else None, "agent": f.agent, "category": f.category,
        "code": f.code, "title": f.title, "severity": f.severity, "reason": f.reason, "explanation": f.explanation,
        "explanation_method": f.explanation_method, "observed_value": f.observed_value,
        "expected_value": f.expected_value, "rule_code": f.rule_code, "rule_version": f.rule_version,
        "rule_id": f.rule_id, "page_no": f.page_no, "region": f.region, "regulatory_citations": f.regulatory_citations,
        "evidence_refs": f.evidence_refs, "comparison": f.comparison, "recommended_action": f.recommended_action,
        "review_status": f.review_status, "is_machine_generated": f.is_machine_generated, "created_at": iso(f.created_at),
    }
    if actions is not None:
        out["review_history"] = [review_action(a) for a in actions]
    return out


def review_action(a: ReviewAction) -> dict:
    return {"id": a.id, "application_id": a.application_id, "finding_id": a.finding_id, "action": a.action,
            "note": a.note, "previous_status": a.previous_status, "new_status": a.new_status, "payload": a.payload,
            "actor": {"id": a.actor.id, "full_name": a.actor.full_name, "role": a.actor.role},
            "created_at": iso(a.created_at)}


def verification(v: VerificationRequest) -> dict:
    return {"id": v.id, "application_id": v.application_id, "document_id": v.document_id,
            "document_filename": v.document.filename if v.document else None,
            "doc_type": v.document.doc_type if v.document else None, "connector_key": v.connector_key,
            "request_ref": v.request_ref, "status": v.status, "attempts": v.attempts, "query": v.query,
            "response_meta": v.response_meta, "mismatches": v.mismatches, "error": v.error,
            "is_simulated": v.is_simulated, "requested_at": iso(v.requested_at), "responded_at": iso(v.responded_at),
            "manual_outcome": v.manual_outcome, "manual_note": v.manual_note, "manual_at": iso(v.manual_at),
            "effective_status": v.manual_outcome or v.status}


def connector(c: Connector) -> dict:
    cfg = dict(c.config or {})
    records = cfg.pop("records", None)
    keys = cfg.pop("issuer_public_keys", None)
    return {"id": c.id, "key": c.key, "name": c.name, "kind": c.kind, "mode": c.mode, "enabled": c.enabled,
            "issuer_name": c.issuer_name, "doc_types": c.doc_types, "timeout_ms": c.timeout_ms,
            "max_retries": c.max_retries, "health_status": c.health_status, "last_success_at": iso(c.last_success_at),
            "last_error": c.last_error, "last_error_at": iso(c.last_error_at), "authorization_note": c.authorization_note,
            "config": cfg, "synthetic_registry_size": len(records) if records is not None else None,
            "registered_issuer_keys": sorted(keys) if keys else None}


def rule(r: Rule) -> dict:
    return {"id": r.id, "rule_code": r.rule_code, "version": r.version, "title": r.title, "description": r.description,
            "category": r.category, "source_id": r.source_id,
            "source": {"title": r.source.title, "edition": r.source.edition, "status": r.source.status,
                       "is_synthetic": r.source.is_synthetic, "is_official": r.source.is_official} if r.source else None,
            "clause_ref": r.clause_ref, "effective_from": iso(r.effective_from), "effective_to": iso(r.effective_to),
            "required_documents": r.required_documents, "required_fields": r.required_fields, "condition": r.condition,
            "severity": r.severity, "finding_category": r.finding_category, "reviewer_guidance": r.reviewer_guidance,
            "retrieval_query": r.retrieval_query, "test_cases": r.test_cases, "status": r.status,
            "is_synthetic": r.is_synthetic, "created_by_id": r.created_by_id, "approved_by_id": r.approved_by_id,
            "approved_at": iso(r.approved_at), "change_note": r.change_note, "created_at": iso(r.created_at)}


def source(s: RegulatorySource) -> dict:
    return {"id": s.id, "title": s.title, "edition": s.edition, "source_type": s.source_type,
            "is_official": s.is_official, "is_synthetic": s.is_synthetic, "publication_date": iso(s.publication_date),
            "effective_from": iso(s.effective_from), "effective_to": iso(s.effective_to), "source_url": s.source_url,
            "status": s.status, "superseded_by_id": s.superseded_by_id, "sha256": s.sha256, "page_count": s.page_count,
            "index_status": s.index_status, "index_error": s.index_error, "embedding_model": s.embedding_model,
            "indexed_at": iso(s.indexed_at), "chunk_count": s.chunk_count, "created_at": iso(s.created_at)}


def audit_event(e: AuditEvent) -> dict:
    return {"id": e.id, "seq": e.seq, "ts": iso(e.ts), "actor_id": e.actor_id, "actor_email": e.actor_email,
            "actor_role": e.actor_role, "action": e.action, "entity_type": e.entity_type, "entity_id": e.entity_id,
            "application_id": e.application_id, "request_id": e.request_id, "details": e.details,
            "source_refs": e.source_refs, "hash": e.hash, "prev_hash": e.prev_hash}


def job(j: Job) -> dict:
    return {"id": j.id, "kind": j.kind, "application_id": j.application_id, "status": j.status, "progress": j.progress,
            "steps": j.steps, "params": j.params, "result": j.result, "error": j.error, "attempts": j.attempts,
            "cancel_requested": j.cancel_requested, "created_at": iso(j.created_at), "started_at": iso(j.started_at),
            "finished_at": iso(j.finished_at)}
