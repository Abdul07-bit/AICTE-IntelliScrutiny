from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Application, Finding, ValidationRun

SEVERITY_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def add_finding(db: Session, app: Application, run: ValidationRun | None, *, agent: str, category: str,
                code: str, title: str, severity: str, reason: str, recommended_action: str,
                document_id: str | None = None, observed=None, expected=None, page: int | None = None,
                region=None, citations: list | None = None, evidence: list | None = None,
                comparison: dict | None = None, rule=None, explanation: str | None = None,
                explanation_method: str | None = None) -> Finding:
    f = Finding(
        application_id=app.id, run_id=run.id if run else None, document_id=document_id, agent=agent,
        category=category, code=code, title=title, severity=severity, reason=reason,
        observed_value=None if observed is None else str(observed),
        expected_value=None if expected is None else str(expected), page_no=page, region=region,
        regulatory_citations=citations or [], evidence_refs=evidence or [], comparison=comparison,
        recommended_action=recommended_action, explanation=explanation, explanation_method=explanation_method,
        rule_id=rule.id if rule else None, rule_code=rule.rule_code if rule else None,
        rule_version=rule.version if rule else None,
    )
    db.add(f)
    db.flush()
    return f


def doc_ref(doc, page: int | None = None, bbox=None) -> dict:
    return {"kind": "DOCUMENT", "document_id": doc.id, "filename": doc.filename, "doc_type": doc.doc_type,
            "page": page, "bbox": bbox, "sha256": doc.sha256}
