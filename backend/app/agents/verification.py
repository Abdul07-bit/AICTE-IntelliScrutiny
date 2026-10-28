"""Verification Agent — runs configured issuer connectors with timeouts and retries.

Verification status is tracked on the application separately from the workflow status, so it
can continue (e.g. manual confirmations) after preliminary validation completes."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.common import add_finding, doc_ref
from app.agents.validation import reference_date
from app.connectors.adapters import ADAPTERS
from app.connectors.base import ConnectorResponse, SourceUnavailable, VerificationQuery, new_request_ref
from app.core.permissions import Role
from app.db.types import utcnow
from app.models import Application, Connector, Document, User, ValidationRun, VerificationRequest
from app.services import audit
from app.services.notifications import notify_roles

AGENT = "VERIFICATION"
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="connector")


def build_query(doc: Document, app: Application) -> VerificationQuery:
    return VerificationQuery(
        document_id=doc.id, doc_type=doc.doc_type,
        fields={f.name: f.normalized_value or f.value for f in doc.fields},
        qr_payloads=(doc.file_metadata or {}).get("qr") or [],
        reference_date=reference_date(app),
    )


def call_connector(conn: Connector, q: VerificationQuery) -> tuple[ConnectorResponse, int, str | None]:
    adapter = ADAPTERS[conn.config.get("adapter", "mock_issuer_api")](conn.key, conn.config, conn.mode)
    attempts, last_err = 0, None
    for attempt in range(conn.max_retries + 1):
        attempts += 1
        try:
            resp = _pool.submit(adapter.verify, q).result(timeout=conn.timeout_ms / 1000)
            conn.health_status, conn.last_success_at = "HEALTHY", utcnow()
            return resp, attempts, None
        except FutureTimeout:
            last_err = f"Timed out after {conn.timeout_ms} ms"
        except SourceUnavailable as exc:
            last_err = str(exc)
        except Exception as exc:  # adapter bug or unexpected response
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(min(0.05 * (2 ** attempt), 0.5))
    conn.health_status, conn.last_error, conn.last_error_at = "DOWN", last_err, utcnow()
    return (ConnectorResponse("SOURCE_UNAVAILABLE",
                              f"Source unavailable after {attempts} attempt(s): {last_err}. Source unavailability is "
                              f"not evidence of forgery; routed to manual confirmation.",
                              {"attempts": attempts}, is_simulated=conn.mode == "MOCK"), attempts, last_err)


def _finding_for(db, app, run, doc: Document, vr: VerificationRequest, conn: Connector) -> None:
    common = dict(agent=AGENT, document_id=doc.id, evidence=[doc_ref(doc), {"kind": "VERIFICATION", "request_id": vr.id,
                                                                          "connector": conn.key, "request_ref": vr.request_ref}])
    label = (doc.doc_type or "document").replace("_", " ").title()
    if vr.status == "MISMATCH_DETECTED":
        m = vr.mismatches or []
        add_finding(db, app, run, category="UNRESOLVED_EVIDENCE", code="ISSUER_MISMATCH",
                    title=f"{conn.name}: issuer data differs for {label}", severity="HIGH",
                    reason=vr.response_meta.get("summary", "") + " A mismatch requires confirmation; it does not by itself "
                           "establish that the document is not genuine.",
                    observed="; ".join(f"{x['field']}={x['document_value']}" for x in m) or None,
                    expected="; ".join(f"{x['field']}={x['issuer_value']}" for x in m) or None,
                    comparison={"method": "issuer_lookup", "mismatches": m},
                    recommended_action="Send for manual issuer confirmation and request clarification from the institution.",
                    **common)
    elif vr.status == "EXPIRED":
        add_finding(db, app, run, category="UNRESOLVED_EVIDENCE", code="ISSUER_REPORTS_EXPIRED",
                    title=f"{conn.name}: issuer confirms {label} expires before the session", severity="HIGH",
                    reason=vr.response_meta.get("summary", ""),
                    observed=(vr.response_meta.get("issuer_record") or {}).get("valid_until"),
                    expected=f">= {reference_date(app).isoformat()}",
                    recommended_action="Request a renewed certificate.", **common)
    elif vr.status in ("SOURCE_UNAVAILABLE", "MANUAL_REVIEW_REQUIRED"):
        add_finding(db, app, run, category="UNRESOLVED_EVIDENCE",
                    code="ISSUER_SOURCE_UNAVAILABLE" if vr.status == "SOURCE_UNAVAILABLE" else "ISSUER_MANUAL_REVIEW",
                    title=f"{conn.name}: {label} could not be confirmed automatically",
                    severity="MEDIUM", reason=vr.response_meta.get("summary", vr.error or ""),
                    recommended_action="A verification officer should confirm with the issuer (manual queue).", **common)


def create_manual_request(db: Session, app: Application, doc: Document | None, reason: str,
                          user: User | None = None, origin: str | None = None) -> VerificationRequest:
    vr = VerificationRequest(application_id=app.id, document_id=doc.id if doc else None, connector_key="manual",
                             request_ref=new_request_ref("MV"), status="PENDING", requested_by_id=user.id if user else None,
                             query={"reason": reason, "origin": origin}, is_simulated=False,
                             response_meta={"summary": "Awaiting manual issuer confirmation", "reason": reason})
    db.add(vr)
    db.flush()
    notify_roles(db, [Role.VERIFICATION_OFFICER], "Manual issuer confirmation requested",
                 f"{app.reference_no}: {reason}", f"/verification?request={vr.id}", "WARNING")
    audit.record(db, "verification.manual_requested", "verification_request", vr.id, actor=user,
                 application_id=app.id, institution_id=app.institution_id, details={"reason": reason, "origin": origin})
    return vr


def run_verification(db: Session, app: Application, run: ValidationRun | None,
                     user: User | None = None, document_ids: list[str] | None = None) -> dict:
    connectors = db.scalars(select(Connector).where(Connector.enabled.is_(True))).all()
    q = select(Document).where(Document.application_id == app.id, Document.status == "PROCESSED")
    if document_ids:
        q = q.where(Document.id.in_(document_ids))
    counts: dict[str, int] = {}
    for doc in db.scalars(q).all():
        query = build_query(doc, app)
        for conn in connectors:
            adapter_cls = ADAPTERS.get(conn.config.get("adapter", ""))
            if adapter_cls is None or conn.kind == "MANUAL":
                continue
            adapter = adapter_cls(conn.key, conn.config, conn.mode)
            if not adapter.applies_to(query, conn.doc_types or []):
                continue
            resp, attempts, err = call_connector(conn, query)
            vr = VerificationRequest(
                application_id=app.id, document_id=doc.id, connector_key=conn.key,
                request_ref=new_request_ref(), status=resp.status, attempts=attempts,
                query={"doc_type": query.doc_type, "certificate_number": query.fields.get("certificate_number"),
                       "reference_date": query.reference_date.isoformat() if query.reference_date else None},
                response_meta={**resp.response_meta, "summary": resp.summary, "mode": conn.mode},
                mismatches=resp.mismatches, error=err, is_simulated=resp.is_simulated,
                requested_by_id=user.id if user else None, responded_at=utcnow(),
            )
            db.add(vr)
            db.flush()
            counts[resp.status] = counts.get(resp.status, 0) + 1
            audit.record(db, "verification.completed", "verification_request", vr.id, actor=user,
                         application_id=app.id, institution_id=app.institution_id,
                         details={"connector": conn.key, "status": resp.status, "simulated": resp.is_simulated,
                                  "request_ref": vr.request_ref})
            if run is not None:  # findings belong to validation runs; ad-hoc re-verification only updates requests
                _finding_for(db, app, run, doc, vr, conn)
            if resp.status == "SOURCE_UNAVAILABLE":
                create_manual_request(db, app, doc, f"{conn.name} unavailable for {doc.filename}", user, origin=vr.id)
    refresh_status(db, app)
    return counts


def refresh_status(db: Session, app: Application) -> str:
    reqs = db.scalars(select(VerificationRequest).where(VerificationRequest.application_id == app.id)).all()
    statuses = [r.manual_outcome or r.status for r in reqs]
    if not reqs:
        status = "NOT_STARTED"
    elif "PENDING" in statuses:
        status = "AWAITING_MANUAL_CONFIRMATION"
    elif any(s in ("MISMATCH_DETECTED", "EXPIRED", "MANUAL_REVIEW_REQUIRED") for s in statuses):
        status = "COMPLETED_WITH_EXCEPTIONS"
    else:
        status = "COMPLETED"
    app.verification_status = status
    db.flush()
    return status
