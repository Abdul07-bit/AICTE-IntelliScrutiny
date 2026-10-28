"""Hybrid retrieval (vector similarity + keyword scoring + metadata filters, fused with
reciprocal-rank fusion) over the two separate indexes.

Authorization for the institutional evidence index is applied as SQL WHERE clauses *before*
any similarity ranking, so vector similarity can never surface another tenant's evidence."""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date

from sqlalchemy import Float, and_, bindparam, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import forbidden
from app.core.permissions import TENANT_SCOPED_ROLES, Role
from app.models import EvidenceChunk, RegulatoryChunk, RegulatorySource, User
from app.rag.embeddings import cosine, get_embedder
from app.services.textutil import tokenize

RRF_K = 60


def _bm25(query: str, docs: list[str]) -> list[float]:
    q = set(tokenize(query))
    toks = [tokenize(d) for d in docs]
    n = len(docs) or 1
    avgdl = sum(len(t) for t in toks) / n or 1
    df = Counter(term for t in toks for term in set(t))
    out = []
    for t in toks:
        tf = Counter(t)
        s = 0.0
        for term in q:
            if term in tf:
                idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
                s += idf * tf[term] * 2.2 / (tf[term] + 1.2 * (0.25 + 0.75 * len(t) / avgdl))
        out.append(s)
    return out


def _tsquery(query: str) -> str:
    terms = [t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2][:24]
    return " | ".join(terms) or "none"


def _hybrid(db: Session, model, where: list, query: str, k: int, candidate_pool: int = 200):
    emb = get_embedder()
    qvec = emb.embed([query])[0]
    where = [*where, model.embedding_model == emb.model_id]
    if db.bind.dialect.name == "postgresql":
        dist = model.embedding.op("<=>", return_type=Float)(bindparam("qv", qvec, type_=model.embedding.type))
        vec_rows = db.execute(select(model, (1 - dist).label("s")).where(and_(*where)).order_by(dist).limit(k * 4)).all()
        tsv = func.to_tsvector("english", model.text)
        tsq = func.to_tsquery("english", _tsquery(query))
        kw_rows = db.execute(select(model, func.ts_rank_cd(tsv, tsq).label("s")).where(and_(*where), tsv.op("@@")(tsq))
                             .order_by(func.ts_rank_cd(tsv, tsq).desc()).limit(k * 4)).all()
        vec = [(r[0], float(r[1])) for r in vec_rows]
        kw = [(r[0], float(r[1])) for r in kw_rows]
    else:
        rows = db.scalars(select(model).where(and_(*where)).limit(5000)).all()
        vec = sorted(((r, cosine(qvec, r.embedding or [])) for r in rows), key=lambda x: -x[1])[: k * 4]
        bm = _bm25(query, [r.text for r in rows])
        kw = sorted(((r, s) for r, s in zip(rows, bm) if s > 0), key=lambda x: -x[1])[: k * 4]

    fused: dict[str, dict] = {}
    for rank, (row, score) in enumerate(vec):
        fused.setdefault(row.id, {"row": row, "rrf": 0.0, "vector_score": None, "keyword_score": None})
        fused[row.id]["rrf"] += 1 / (RRF_K + rank + 1)
        fused[row.id]["vector_score"] = round(score, 4)
    for rank, (row, score) in enumerate(kw):
        fused.setdefault(row.id, {"row": row, "rrf": 0.0, "vector_score": None, "keyword_score": None})
        fused[row.id]["rrf"] += 1 / (RRF_K + rank + 1)
        fused[row.id]["keyword_score"] = round(score, 4)
    return sorted(fused.values(), key=lambda x: -x["rrf"])[:k]


# ---------------------------------------------------------------- INDEX 1
def search_regulatory(db: Session, query: str, *, as_of: date | None = None, k: int = 5,
                      source_types: list[str] | None = None, include_superseded: bool = False) -> list[dict]:
    where: list = []
    if as_of is not None:
        # Requirements in force on the reference date (supports historical applications).
        where += [RegulatoryChunk.status != "WITHDRAWN",
                  or_(RegulatoryChunk.effective_from.is_(None), RegulatoryChunk.effective_from <= as_of),
                  or_(RegulatoryChunk.effective_to.is_(None), RegulatoryChunk.effective_to >= as_of)]
    elif not include_superseded:
        where.append(RegulatoryChunk.status == "ACTIVE")
    if source_types:
        where.append(RegulatoryChunk.source.has(RegulatorySource.source_type.in_(source_types)))
    hits = _hybrid(db, RegulatoryChunk, where, query, k)
    out = []
    for h in hits:
        c: RegulatoryChunk = h["row"]
        src = c.source
        out.append({
            "index": "REGULATORY_KNOWLEDGE",
            "chunk_id": c.id, "source_id": src.id, "title": src.title, "edition": src.edition,
            "source_type": src.source_type, "is_official": src.is_official, "is_synthetic": src.is_synthetic,
            "source_status": c.status, "page": c.page_no, "clause_ref": c.clause_ref, "heading": c.heading,
            "text": c.text, "source_url": src.source_url,
            "effective_from": c.effective_from.isoformat() if c.effective_from else None,
            "effective_to": c.effective_to.isoformat() if c.effective_to else None,
            "score": round(h["rrf"], 5), "vector_score": h["vector_score"], "keyword_score": h["keyword_score"],
            "embedding_model": c.embedding_model, "content_hash": c.content_hash,
        })
    return out


# ---------------------------------------------------------------- INDEX 2
def evidence_scope_for(user: User | None, institution_id: str) -> None:
    """Raise unless `user` may read evidence of `institution_id`. `None` = internal system call."""
    if user is None:
        return
    if Role(user.role) in TENANT_SCOPED_ROLES and user.institution_id != institution_id:
        raise forbidden("Evidence belongs to another institution")


def search_evidence(db: Session, query: str, *, user: User | None, institution_id: str,
                    application_id: str | None = None, include_prior_records: bool = True,
                    doc_types: list[str] | None = None, k: int = 6) -> list[dict]:
    evidence_scope_for(user, institution_id)
    where: list = [EvidenceChunk.institution_id == institution_id, EvidenceChunk.is_current.is_(True)]
    scope = []
    if application_id:
        scope.append(EvidenceChunk.application_id == application_id)
    if include_prior_records:
        scope.append(EvidenceChunk.source_kind == "RECORD")
    if scope:
        where.append(or_(*scope))
    if doc_types:
        where.append(EvidenceChunk.doc_type.in_(doc_types))
    hits = _hybrid(db, EvidenceChunk, where, query, k * 2)
    out = []
    for h in hits:
        c: EvidenceChunk = h["row"]
        if user is not None and user.role not in (c.allowed_roles or []):
            continue
        out.append({
            "index": "INSTITUTIONAL_EVIDENCE",
            "chunk_id": c.id, "institution_id": c.institution_id, "application_id": c.application_id,
            "document_id": c.document_id, "record_id": c.record_id, "source_kind": c.source_kind,
            "doc_type": c.doc_type, "page": c.page_no, "text": c.text, "source_label": c.source_label,
            "version": c.version, "score": round(h["rrf"], 5), "vector_score": h["vector_score"],
            "keyword_score": h["keyword_score"], "embedding_model": c.embedding_model,
            "content_hash": c.content_hash,
        })
    return out[:k]
