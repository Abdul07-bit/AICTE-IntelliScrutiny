"""Indexing jobs for both embedding indexes.

INDEX 1 (regulatory_chunks) and INDEX 2 (evidence_chunks) are separate tables with separate
code paths; nothing in this module ever writes institutional text into the regulatory index or
vice-versa."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.core.permissions import Role
from app.db.types import utcnow
from app.models import Document, EvidenceChunk, InstitutionalRecord, RegulatoryChunk, RegulatorySource
from app.rag.chunking import chunk_evidence, chunk_regulatory
from app.rag.embeddings import get_embedder
from app.services import extraction, storage
from app.services.safety import minimize_for_index

# Roles permitted to retrieve evidence chunks (tenant scoping is enforced separately).
EVIDENCE_ROLES = [r.value for r in Role]


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------- INDEX 1
def index_regulatory_source(db: Session, source: RegulatorySource) -> int:
    emb = get_embedder()
    try:
        data = storage.get(source.storage_key, encrypted=False) if source.storage_key else None
        if data is None:
            raise ValueError("Source file is missing")
        if storage.sha256(data) != source.sha256:
            raise ValueError("Source file hash does not match the recorded hash")
        pages, _ = extraction._extract_pdf(data)
        warnings: list[str] = []
        extraction._apply_ocr(pages, warnings)
        chunks = chunk_regulatory([(p.page_no, p.text) for p in pages])
        if not chunks:
            raise ValueError("No indexable text found" + (f" ({'; '.join(warnings)})" if warnings else ""))
        vectors = emb.embed([f"{c.heading or ''}\n{c.text}" for c in chunks])
        db.execute(delete(RegulatoryChunk).where(RegulatoryChunk.source_id == source.id))
        for i, (c, v) in enumerate(zip(chunks, vectors)):
            db.add(RegulatoryChunk(
                source_id=source.id, chunk_index=i, page_no=c.page_no, clause_ref=c.clause_ref,
                heading=c.heading, text=c.text, content_hash=_h(c.text), embedding=v,
                embedding_model=emb.model_id, effective_from=source.effective_from,
                effective_to=source.effective_to, status=source.status,
            ))
        source.page_count = len(pages)
        source.chunk_count = len(chunks)
        source.index_status, source.index_error = "INDEXED", None
        source.embedding_model, source.indexed_at = emb.model_id, utcnow()
        db.flush()
        return len(chunks)
    except Exception as exc:
        source.index_status, source.index_error = "FAILED", str(exc)[:500]
        db.flush()
        raise


def supersede(db: Session, old: RegulatorySource, new: RegulatorySource) -> None:
    """Mark an edition superseded. Its chunks stay indexed for historical applications but are
    excluded from current-rule retrieval."""
    old.status = "SUPERSEDED"
    old.superseded_by_id = new.id
    if new.effective_from and (old.effective_to is None or old.effective_to >= new.effective_from):
        old.effective_to = new.effective_from - timedelta(days=1)
    db.execute(update(RegulatoryChunk).where(RegulatoryChunk.source_id == old.id)
               .values(status="SUPERSEDED", effective_to=old.effective_to))
    db.flush()


# ---------------------------------------------------------------- INDEX 2
def index_document(db: Session, doc: Document) -> int:
    emb = get_embedder()
    texts = [(p.page_no, minimize_for_index(p.text)) for p in doc.pages if p.text.strip()]
    chunks = chunk_evidence(texts)
    db.execute(delete(EvidenceChunk).where(EvidenceChunk.document_id == doc.id))
    if not chunks:
        return 0
    label = f"{doc.doc_type or 'DOCUMENT'} · {doc.filename} (v{doc.version})"
    vectors = emb.embed([c.text for c in chunks])
    for c, v in zip(chunks, vectors):
        db.add(EvidenceChunk(
            institution_id=doc.institution_id, application_id=doc.application_id, document_id=doc.id,
            source_kind="DOCUMENT", doc_type=doc.doc_type, page_no=c.page_no, text=c.text,
            content_hash=_h(c.text), embedding=v, embedding_model=emb.model_id,
            allowed_roles=EVIDENCE_ROLES, version=doc.version, source_label=label,
        ))
    db.flush()
    return len(chunks)


def record_text(rec: InstitutionalRecord) -> str:
    lines = [rec.title, f"Record type: {rec.record_type}", f"Reference: {rec.reference}"]
    if rec.academic_year:
        lines.append(f"Academic year: {rec.academic_year}")
    for k, v in (rec.data or {}).items():
        lines.append(f"{k.replace('_', ' ')}: {json.dumps(v) if isinstance(v, (list, dict)) else v}")
    return "\n".join(lines)


def index_record(db: Session, rec: InstitutionalRecord) -> int:
    emb = get_embedder()
    text = minimize_for_index(record_text(rec))
    db.execute(delete(EvidenceChunk).where(EvidenceChunk.record_id == rec.id))
    db.add(EvidenceChunk(
        institution_id=rec.institution_id, application_id=None, record_id=rec.id, source_kind="RECORD",
        doc_type=rec.record_type, page_no=None, text=text, content_hash=_h(text),
        embedding=emb.embed([text])[0], embedding_model=emb.model_id, allowed_roles=EVIDENCE_ROLES,
        version=rec.version, is_current=not rec.superseded, source_label=f"{rec.title} [{rec.source}]",
    ))
    db.flush()
    return 1


def index_status(db: Session) -> dict:
    emb = get_embedder()
    reg_total = db.query(RegulatoryChunk).count()
    reg_stale = db.query(RegulatoryChunk).filter(RegulatoryChunk.embedding_model != emb.model_id).count()
    ev_total = db.query(EvidenceChunk).count()
    ev_stale = db.query(EvidenceChunk).filter(EvidenceChunk.embedding_model != emb.model_id).count()
    sources = db.scalars(select(RegulatorySource)).all()
    return {
        "embedding_model": emb.model_id,
        "embedding_kind": emb.kind,
        "embedding_dim": emb.dim,
        "regulatory_index": {"table": "regulatory_chunks", "chunks": reg_total, "stale_chunks": reg_stale,
                             "sources": len(sources),
                             "sources_indexed": sum(1 for s in sources if s.index_status == "INDEXED")},
        "evidence_index": {"table": "evidence_chunks", "chunks": ev_total, "stale_chunks": ev_stale},
    }
