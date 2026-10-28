"""Validation Agent — OCR/extraction, classification, completeness, exact field comparisons,
expiry checks, duplicate detection and document anomaly indicators.

Every flag carries: reason, source document, page/region where possible, observed value,
expected value or rule, and a recommended next step."""
from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.common import add_finding, doc_ref
from app.db.types import utcnow
from app.models import (
    Application,
    Document,
    DocumentPage,
    ExtractedField,
    InstitutionalRecord,
    ValidationRun,
)
from app.services import extraction, storage
from app.services.safety import detect_injection
from app.services.textutil import parse_date, text_similarity

AGENT = "VALIDATION"
EDITOR_HINTS = ("photo", "editor", "paint", "gimp", "photoshop", "pixel", "illustrator", "canva")
LOW_CONFIDENCE = 0.6


# ---------------------------------------------------------------- processing
def process_documents(db: Session, app: Application, use_fixtures: bool) -> dict:
    stats = {"processed": 0, "failed": 0, "methods": {}}
    docs = db.scalars(select(Document).where(Document.application_id == app.id,
                                             Document.status.notin_(["QUARANTINED", "SUPERSEDED"]))).all()
    for doc in docs:
        try:
            doc.status = "PROCESSING"
            db.flush()
            data = storage.get(doc.storage_key, doc.encrypted)
            fixture = (doc.file_metadata or {}).get("fixture_pages") if use_fixtures else None
            res = extraction.process(data, doc.content_type, doc.declared_type, fixture)
            for p in list(doc.pages):
                db.delete(p)
            for f in list(doc.fields):
                db.delete(f)
            db.flush()
            for p in res.pages:
                db.add(DocumentPage(document_id=doc.id, page_no=p.page_no, text=p.text,
                                    lines=[{"text": ln.text, "bbox": ln.bbox} for ln in p.lines],
                                    width=p.width, height=p.height, method=p.method, confidence=p.confidence))
            for f in res.fields:
                db.add(ExtractedField(document_id=doc.id, name=f.name, value=f.value, normalized_value=f.normalized,
                                      confidence=f.confidence, page_no=f.page_no, bbox=f.bbox, method=f.method))
            meta = dict(doc.file_metadata or {})
            meta.update({"extraction": res.metadata, "qr": res.qr_payloads, "schema_errors": res.schema_errors,
                         "warnings": res.warnings, "classification_scores": res.classification_scores,
                         "pipeline_version": extraction.PIPELINE_VERSION,
                         "ocr_engine_available": extraction.tesseract_available()})
            doc.file_metadata = meta
            doc.page_count = len(res.pages)
            doc.doc_type, doc.classification_confidence = res.doc_type, res.classification_confidence
            doc.extraction_method, doc.extraction_confidence = res.method, res.confidence
            doc.status, doc.processing_error = "PROCESSED", None
            stats["processed"] += 1
            stats["methods"][res.method] = stats["methods"].get(res.method, 0) + 1
        except Exception as exc:
            doc.status, doc.processing_error = "FAILED", f"{type(exc).__name__}: {exc}"[:500]
            stats["failed"] += 1
        db.flush()
        db.refresh(doc)
    return stats


# ---------------------------------------------------------------- rule context
def reference_date(app: Application) -> date:
    """Synthetic convention: the academic session starts on 1 July of the first year."""
    return date(int(app.academic_year[:4]), 7, 1)


def build_context(db: Session, app: Application) -> dict:
    docs: dict[str, list] = {}
    for d in db.scalars(select(Document).where(Document.application_id == app.id, Document.status == "PROCESSED")):
        docs.setdefault(d.doc_type or "UNKNOWN", []).append({
            "id": d.id, "doc_type": d.doc_type, "filename": d.filename,
            "fields": {f.name: {"value": f.value, "normalized": f.normalized_value, "page": f.page_no,
                                "bbox": f.bbox, "confidence": f.confidence} for f in d.fields},
        })
    records: dict[str, list] = {}
    for r in db.scalars(select(InstitutionalRecord).where(InstitutionalRecord.institution_id == app.institution_id,
                                                          InstitutionalRecord.superseded.is_(False))):
        records.setdefault(r.record_type, []).append({"id": r.id, "reference": r.reference,
                                                      "academic_year": r.academic_year, **(r.data or {})})
    submitted = (app.submitted_at or utcnow()).date()
    return {
        "application": {
            "id": app.id, "category": app.category, "academic_year": app.academic_year,
            "reference_date": reference_date(app).isoformat(), "submission_date": submitted.isoformat(),
            "institution_name": app.institution.name, "institution_code": app.institution.code,
            "institution_aliases": app.institution.aliases or [],
        },
        "documents": docs,
        "records": records,
    }


# ---------------------------------------------------------------- checks
def _f(doc: Document, name: str) -> ExtractedField | None:
    return next((f for f in doc.fields if f.name == name), None)


def validate(db: Session, app: Application, run: ValidationRun) -> int:
    n0 = len(app_findings(db, app, run))
    docs = db.scalars(select(Document).where(Document.application_id == app.id)).all()
    submitted = (app.submitted_at or utcnow()).date()

    for doc in docs:
        if doc.status == "FAILED":
            add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="PROCESSING_FAILED",
                        title=f"{doc.filename} could not be processed", severity="MEDIUM",
                        reason=doc.processing_error or "Processing failed", document_id=doc.id,
                        evidence=[doc_ref(doc)], recommended_action="Inspect the original file manually or request a re-upload.")
            continue
        if doc.status != "PROCESSED":
            continue
        meta = doc.file_metadata or {}
        _check_extraction(db, app, run, doc, meta)
        _check_identifiers(db, app, run, doc)
        _check_expiry(db, app, run, doc, submitted)
        _check_anomalies(db, app, run, doc, meta)

    _check_payment(db, app, run, docs)
    _check_duplicates(db, app, run, docs)
    return len(app_findings(db, app, run)) - n0


def app_findings(db, app, run):
    from app.models import Finding

    return db.scalars(select(Finding.id).where(Finding.run_id == run.id)).all()


def _check_extraction(db, app, run, doc: Document, meta: dict) -> None:
    for w in meta.get("warnings", []):
        if "OCR engine is unavailable" in w:
            add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="OCR_UNAVAILABLE",
                        title=f"Text could not be extracted from {doc.filename}", severity="MEDIUM",
                        reason=f"{w}. No field values were inferred for this document.", document_id=doc.id,
                        observed="no text layer", expected="machine-readable text or OCR output",
                        evidence=[doc_ref(doc)],
                        recommended_action="Enable the OCR engine (Tesseract) and re-run, or review the document manually.")
    has_text = any(p.text.strip() for p in doc.pages)
    if doc.doc_type in extraction.SCHEMAS and has_text:  # unreadable files are already flagged above
        for err in meta.get("schema_errors", []):
            add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="REQUIRED_FIELD_MISSING",
                        title=f"{doc.doc_type.replace('_', ' ').title()}: {err}", severity="MEDIUM",
                        reason=f"Schema-constrained extraction for {doc.doc_type} reported: {err}.",
                        document_id=doc.id, observed="not found", expected="field present and parseable",
                        evidence=[doc_ref(doc)],
                        recommended_action="Verify the field on the original document; request a clearer copy if illegible.")
    if doc.extraction_confidence is not None and 0 < doc.extraction_confidence < LOW_CONFIDENCE:
        add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="LOW_EXTRACTION_CONFIDENCE",
                    title=f"Low extraction confidence for {doc.filename}", severity="LOW",
                    reason=f"Mean extraction confidence {doc.extraction_confidence:.2f} is below {LOW_CONFIDENCE}.",
                    document_id=doc.id, observed=doc.extraction_confidence, expected=f">= {LOW_CONFIDENCE}",
                    evidence=[doc_ref(doc)], recommended_action="Manually confirm the extracted values.")
    if doc.declared_type and doc.doc_type and doc.declared_type != doc.doc_type and doc.doc_type != "UNKNOWN":
        add_finding(db, app, run, agent=AGENT, category="SUSPECTED_ANOMALY", code="DOCUMENT_TYPE_MISMATCH",
                    title=f"{doc.filename} declared as {doc.declared_type} but classified as {doc.doc_type}",
                    severity="LOW", reason="Content-based classification differs from the uploader's declared type.",
                    document_id=doc.id, observed=doc.doc_type, expected=doc.declared_type, evidence=[doc_ref(doc)],
                    recommended_action="Confirm the document type; it may have been uploaded under the wrong checklist item.")


def _check_identifiers(db, app, run, doc: Document) -> None:
    if doc.doc_type != "APPLICATION_FORM":
        return
    checks = [
        ("institution_code", app.institution.code, "Institution code"),
        ("academic_year", app.academic_year, "Academic year"),
    ]
    for field, expected, label in checks:
        f = _f(doc, field)
        if f and f.normalized_value and f.normalized_value.upper() != expected.upper():
            add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="IDENTIFIER_MISMATCH",
                        title=f"{label} on application form does not match the application record", severity="HIGH",
                        reason=f"Exact comparison of '{field}' against the relational application record failed.",
                        document_id=doc.id, observed=f.value, expected=expected, page=f.page_no, region=f.bbox,
                        evidence=[doc_ref(doc, f.page_no, f.bbox)],
                        comparison={"method": "exact", "field": field, "observed": f.value, "expected": expected},
                        recommended_action="Ask the institution to confirm the correct identifier.")


def _check_expiry(db, app, run, doc: Document, submitted: date) -> None:
    f = _f(doc, "valid_until")
    issue = _f(doc, "issue_date")
    vu = parse_date(f.normalized_value) if f else None
    iss = parse_date(issue.normalized_value) if issue else None
    if vu and vu < submitted:
        add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="DOCUMENT_EXPIRED_AT_SUBMISSION",
                    title=f"{(doc.doc_type or 'Document').replace('_', ' ').title()} expired before submission",
                    severity="HIGH",
                    reason=f"Extracted expiry date {vu.isoformat()} is {(submitted - vu).days} day(s) before the "
                           f"submission date {submitted.isoformat()}.",
                    document_id=doc.id, observed=vu.isoformat(), expected=f">= {submitted.isoformat()}",
                    page=f.page_no, region=f.bbox, evidence=[doc_ref(doc, f.page_no, f.bbox)],
                    comparison={"method": "date", "field": "valid_until", "observed": vu.isoformat(),
                                "expected": f">= {submitted.isoformat()}"},
                    recommended_action="Request a renewed certificate or evidence of renewal application.")
    if vu and iss and iss > vu:
        add_finding(db, app, run, agent=AGENT, category="SUSPECTED_ANOMALY", code="DATE_INCONSISTENCY",
                    title="Issue date is after expiry date", severity="MEDIUM",
                    reason=f"Issue date {iss.isoformat()} is later than expiry {vu.isoformat()} on the same document.",
                    document_id=doc.id, observed=f"issue {iss}, expiry {vu}", expected="issue date before expiry",
                    page=issue.page_no, evidence=[doc_ref(doc, issue.page_no, issue.bbox)],
                    recommended_action="Verify dates with the issuer.")
    period = _f(doc, "validity_period_years")
    if period and period.normalized_value and iss and vu:
        stated = int(period.normalized_value)
        actual = round((vu - iss).days / 365.25, 1)
        if abs(actual - stated) > 0.25:
            add_finding(db, app, run, agent=AGENT, category="SUSPECTED_ANOMALY", code="VISIBLE_INCONSISTENCY",
                        title="Stated validity period is inconsistent with printed dates", severity="MEDIUM",
                        reason=f"Document states validity of {stated} year(s) but the printed dates span {actual} years "
                               f"({iss.isoformat()} → {vu.isoformat()}).",
                        document_id=doc.id, observed=f"{actual} years", expected=f"{stated} years",
                        page=period.page_no, region=period.bbox, evidence=[doc_ref(doc, period.page_no, period.bbox)],
                        recommended_action="Confirm validity dates with the issuing body.")


def _parse_iso(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


def _check_anomalies(db, app, run, doc: Document, meta: dict) -> None:
    em = meta.get("extraction") or {}
    indicators = []
    producer = " ".join(filter(None, [em.get("producer"), em.get("creator")])).lower()
    if any(h in producer for h in EDITOR_HINTS):
        indicators.append(f"Producer/creator metadata indicates an image or document editor ('{em.get('producer') or em.get('creator')}')")
    created, modified = _parse_iso(em.get("creation_date")), _parse_iso(em.get("modification_date"))
    if created and modified and modified < created:
        indicators.append("Modification date precedes creation date")
    iss = _f(doc, "issue_date")
    iss_d = parse_date(iss.normalized_value) if iss else None
    if created and iss_d and created.date() < iss_d and (iss_d - created.date()).days > 30:
        indicators.append(f"File creation date {created.date()} precedes the printed issue date {iss_d} "
                          f"by {(iss_d - created.date()).days} days")
    if modified and iss_d and (modified.date() - iss_d).days > 180:
        indicators.append(f"File was modified {(modified.date() - iss_d).days} days after the printed issue date")
    if em.get("incremental_updates"):
        indicators.append(f"PDF contains {em['incremental_updates']} incremental update(s) (edited after creation)")
    if indicators:
        add_finding(db, app, run, agent=AGENT, category="SUSPECTED_ANOMALY", code="METADATA_ANOMALY",
                    title=f"Metadata inconsistencies in {doc.filename}", severity="MEDIUM",
                    reason="; ".join(indicators) + ". Metadata anomalies have innocent explanations (re-saving, "
                           "scanning software) and do not by themselves establish alteration.",
                    document_id=doc.id, observed=json.dumps({k: em.get(k) for k in ("producer", "creator", "creation_date",
                                                                                    "modification_date")}),
                    expected="metadata consistent with an issuer-generated document", evidence=[doc_ref(doc)],
                    recommended_action="Request issuer confirmation or the original issuer-generated file.")
    text = "\n".join(p.text for p in doc.pages)
    hits = detect_injection(text)
    if hits:
        add_finding(db, app, run, agent=AGENT, category="SUSPECTED_ANOMALY", code="INSTRUCTION_LIKE_TEXT",
                    title=f"Instruction-like text embedded in {doc.filename}", severity="HIGH",
                    reason="The document contains text addressed to an automated reviewer. It was treated strictly as "
                           "data and had no effect on any check: " + "; ".join(f'“{h}”' for h in hits[:3]),
                    document_id=doc.id, observed=hits[0], expected="no embedded instructions", evidence=[doc_ref(doc)],
                    recommended_action="Inspect the document for hidden or out-of-place text.")


def _check_payment(db, app, run, docs: list[Document]) -> None:
    form = next((d for d in docs if d.doc_type == "APPLICATION_FORM" and d.status == "PROCESSED"), None)
    if not form:
        return
    ref, amt = _f(form, "payment_reference"), _f(form, "payment_amount")
    if not ref or not ref.normalized_value:
        return
    rec = db.scalar(select(InstitutionalRecord).where(
        InstitutionalRecord.institution_id == app.institution_id, InstitutionalRecord.record_type == "PAYMENT_RECORD",
        InstitutionalRecord.reference == ref.normalized_value))
    if rec is None:
        add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="PAYMENT_NOT_FOUND",
                    title="Payment reference not found in payment records", severity="HIGH",
                    reason="Exact lookup of the payment reference in authorized payment records returned no match.",
                    document_id=form.id, observed=ref.value, expected="reference present in PAYMENT_RECORD",
                    page=ref.page_no, region=ref.bbox, evidence=[doc_ref(form, ref.page_no, ref.bbox)],
                    recommended_action="Ask the institution for the payment receipt; confirm with the payment gateway.")
    elif amt and amt.normalized_value and str(rec.data.get("amount")) != amt.normalized_value:
        add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="PAYMENT_AMOUNT_MISMATCH",
                    title="Declared fee amount differs from payment record", severity="MEDIUM",
                    reason=f"Payment record {rec.reference} shows {rec.data.get('amount')}.", document_id=form.id,
                    observed=amt.value, expected=rec.data.get("amount"), page=amt.page_no, region=amt.bbox,
                    evidence=[doc_ref(form, amt.page_no, amt.bbox),
                              {"kind": "RECORD", "record_id": rec.id, "label": rec.title}],
                    recommended_action="Reconcile the payment amount with the institution.")


def _check_duplicates(db, app, run, docs: list[Document]) -> None:
    by_hash: dict[str, list[Document]] = {}
    for d in docs:
        by_hash.setdefault(d.sha256, []).append(d)
    for h, group in by_hash.items():
        if len(group) > 1:
            names = ", ".join(d.filename for d in group)
            add_finding(db, app, run, agent=AGENT, category="SUSPECTED_ANOMALY", code="DUPLICATE_DOCUMENT",
                        title=f"{len(group)} identical files submitted", severity="LOW",
                        reason=f"Files {names} have the same SHA-256 hash ({h[:16]}…).", document_id=group[1].id,
                        observed=h, expected="unique files per checklist item",
                        evidence=[doc_ref(d) for d in group],
                        comparison={"method": "sha256", "hash": h, "documents": [d.id for d in group]},
                        recommended_action="Confirm whether a different document was intended for one of the items.")
    texts = [(d, "\n".join(p.text for p in d.pages)) for d in docs if d.status == "PROCESSED"]
    seen = set()
    for i, (a, ta) in enumerate(texts):
        for b, tb in texts[i + 1:]:
            if a.sha256 == b.sha256 or len(ta) < 80 or len(tb) < 80 or (a.id, b.id) in seen:
                continue
            sim = text_similarity(ta, tb)
            if sim >= 0.9:
                seen.add((a.id, b.id))
                add_finding(db, app, run, agent=AGENT, category="SUSPECTED_ANOMALY", code="NEAR_DUPLICATE_DOCUMENT",
                            title=f"{a.filename} and {b.filename} are near-duplicates", severity="LOW",
                            reason=f"Text shingle similarity {sim:.2f} ≥ 0.90 despite different file hashes.",
                            document_id=b.id, observed=f"similarity {sim:.2f}", expected="< 0.90",
                            evidence=[doc_ref(a), doc_ref(b)],
                            recommended_action="Check whether one document is a re-saved copy of the other.")
    # Same file previously submitted for an earlier academic year by the same institution.
    for d in docs:
        prior = db.scalar(select(Document).join(Application, Application.id == Document.application_id).where(
            Document.sha256 == d.sha256, Document.application_id != app.id,
            Document.institution_id == app.institution_id, Application.academic_year < app.academic_year).limit(1))
        if prior is not None and d.doc_type not in ("APPLICATION_FORM",):
            add_finding(db, app, run, agent=AGENT, category="INFORMATION", code="REUSED_PRIOR_DOCUMENT",
                        title=f"{d.filename} was also submitted with a previous application", severity="INFO",
                        reason="Identical file hash found in an earlier application of the same institution.",
                        document_id=d.id, observed=d.sha256[:16], expected="current-year document where required",
                        evidence=[doc_ref(d), {"kind": "DOCUMENT", "document_id": prior.id, "filename": prior.filename,
                                               "application_id": prior.application_id}],
                        recommended_action="Confirm the document is still current.")
