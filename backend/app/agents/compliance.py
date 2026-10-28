"""Compliance Agent — dual-retrieval reconciliation.

For every applicable rule:
  1. retrieve applicable regulatory clauses from INDEX 1 (as of the application's reference date);
  2. retrieve permitted institutional evidence from INDEX 2 (tenant-scoped);
  3. run exact structured comparisons via the deterministic rule engine;
  4. explain the outcome using only the retrieved material;
  5. persist rule version, citations, extracted values, comparison result and timestamp.

The agent separates unmet requirements, unresolved evidence and suspected anomalies, and never
makes an approval or rejection decision."""
from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.common import add_finding
from app.agents.explainer import get_explainer
from app.agents.validation import build_context, reference_date
from app.db.types import utcnow
from app.models import Application, RegulatoryChunk, Rule, ValidationRun
from app.rag.retrieval import search_evidence, search_regulatory
from app.rules import engine

AGENT = "COMPLIANCE"
SEVERITY_FOR_INDET = {"CRITICAL": "HIGH", "HIGH": "MEDIUM", "MEDIUM": "MEDIUM", "LOW": "LOW", "INFO": "INFO"}


def _trim(hit: dict, n: int = 600) -> dict:
    return {**hit, "text": hit["text"][:n]}


def _anchor_citation(db: Session, rule: Rule, as_of) -> dict | None:
    """The clause a rule declares as its source, looked up exactly in INDEX 1."""
    if not rule.source_id or not rule.clause_ref:
        return None
    chunk = db.scalar(select(RegulatoryChunk).where(RegulatoryChunk.source_id == rule.source_id,
                                                    RegulatoryChunk.clause_ref == rule.clause_ref)
                      .order_by(RegulatoryChunk.chunk_index).limit(1))
    if chunk is None:
        return None
    s = chunk.source
    return {"index": "REGULATORY_KNOWLEDGE", "chunk_id": chunk.id, "source_id": s.id, "title": s.title,
            "edition": s.edition, "source_type": s.source_type, "is_official": s.is_official,
            "is_synthetic": s.is_synthetic, "source_status": chunk.status, "page": chunk.page_no,
            "clause_ref": chunk.clause_ref, "heading": chunk.heading, "text": chunk.text, "source_url": s.source_url,
            "effective_from": chunk.effective_from.isoformat() if chunk.effective_from else None,
            "effective_to": chunk.effective_to.isoformat() if chunk.effective_to else None,
            "match": "rule-declared source clause", "content_hash": chunk.content_hash}


def run_compliance(db: Session, app: Application, run: ValidationRun) -> dict:
    as_of = reference_date(app)
    rules = engine.applicable_rules(db, app.category, as_of)
    run.rule_versions = [{"rule_id": r.id, "rule_code": r.rule_code, "version": r.version,
                          "is_synthetic": r.is_synthetic} for r in rules]
    run.as_of_date = as_of
    explainer = get_explainer()
    run.explainer = explainer.name
    ctx = build_context(db, app)
    checklist, retrievals = [], []

    for rule in rules:
        query = rule.retrieval_query or f"{rule.title}. {rule.description}"
        reg_hits = search_regulatory(db, query, as_of=as_of, k=3)
        anchor = _anchor_citation(db, rule, as_of)
        ev_hits = search_evidence(db, query, user=None, institution_id=app.institution_id,
                                  application_id=app.id, k=3)
        citations = ([anchor] if anchor else []) + [h for h in reg_hits if not anchor or h["chunk_id"] != anchor["chunk_id"]]
        source_located = anchor is not None and anchor["source_status"] == "ACTIVE"

        result = engine.evaluate(rule, ctx)
        outcomes = [asdict(o) for o in result.outcomes]
        rule_info = {"rule_code": rule.rule_code, "version": rule.version, "title": rule.title,
                     "is_synthetic": rule.is_synthetic}
        retrievals.append({
            "rule_code": rule.rule_code, "rule_version": rule.version, "query": query, "as_of": as_of.isoformat(),
            "regulatory_hits": [_trim(h) for h in citations[:4]], "evidence_hits": [_trim(h) for h in ev_hits],
            "result": result.result, "outcomes": outcomes, "timestamp": utcnow().isoformat(),
            "source_located": source_located,
        })
        checklist.append({"rule_code": rule.rule_code, "version": rule.version, "title": rule.title,
                          "result": result.result, "severity": rule.severity, "clause_ref": rule.clause_ref,
                          "is_synthetic": rule.is_synthetic, "source_located": source_located})

        failing = [o for o in result.outcomes if o.result in (engine.FAIL, engine.INDET)]
        if not source_located:
            add_finding(db, app, run, agent=AGENT, category="UNRESOLVED_EVIDENCE", code="REGULATORY_SOURCE_UNVERIFIED",
                        title=f"Source clause for {rule.rule_code} not located in the regulatory index", severity="MEDIUM",
                        reason=f"Rule {rule.rule_code} v{rule.version} declares clause {rule.clause_ref or '—'}, but no "
                               f"active indexed text was found for it. The rule's basis needs authorized confirmation.",
                        observed="clause not found", expected=f"indexed clause {rule.clause_ref}", rule=rule,
                        citations=reg_hits[:2],
                        recommended_action="Regulatory administrator to load/verify the source and re-index.")
        for o in failing:
            category = rule.finding_category if o.result == engine.FAIL else "UNRESOLVED_EVIDENCE"
            severity = rule.severity if o.result == engine.FAIL else SEVERITY_FOR_INDET.get(rule.severity, "MEDIUM")
            title = f"{rule.title}: {'not met' if o.result == engine.FAIL else 'could not be determined'}"
            if len(failing) > 1:
                title += f" — {o.doc_type.replace('_', ' ').lower() if o.doc_type else o.op.replace('_', ' ')}"
            doc_evidence = [{"kind": "DOCUMENT", "document_id": o.document_id, "doc_type": o.doc_type,
                             "page": o.page, "bbox": o.bbox}] if o.document_id else []
            if o.extra.get("record_id"):
                doc_evidence.append({"kind": "RECORD", "record_id": o.extra["record_id"],
                                     "label": f"{o.extra.get('record_reference')} ({o.extra.get('record_year')})"})
            explanation = explainer.explain(title=title, rule=rule_info, outcomes=[asdict(o)],
                                            citations=citations, evidence=ev_hits)
            add_finding(db, app, run, agent=AGENT, category=category, code=f"RULE_{o.result}",
                        title=title, severity=severity, reason=o.reason, document_id=o.document_id,
                        observed=o.observed, expected=o.expected, page=o.page, region=o.bbox, rule=rule,
                        citations=[_trim(c, 900) for c in citations[:3]],
                        evidence=doc_evidence + [_trim(e, 400) for e in ev_hits[:3]],
                        comparison={"op": o.op, "result": o.result, "field": o.field, "observed": o.observed,
                                    "expected": o.expected, **o.extra},
                        explanation=explanation, explanation_method=explainer.name,
                        recommended_action=rule.reviewer_guidance or "Review against the cited requirement.")
    run.retrievals = retrievals
    db.flush()
    return {"rules_evaluated": len(rules), "checklist": checklist}
