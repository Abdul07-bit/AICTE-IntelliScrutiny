from datetime import date

from sqlalchemy import select

from app.db.session import session_scope
from app.models import EvidenceChunk, Institution, RegulatoryChunk
from app.rag.chunking import chunk_regulatory
from app.rag.embeddings import HashingEmbedder, cosine
from app.rag.retrieval import search_evidence, search_regulatory


def test_embedder_is_deterministic_and_normalised():
    e = HashingEmbedder(384)
    a, b = e.embed(["fire safety certificate validity"])[0], e.embed(["fire safety certificate validity"])[0]
    assert a == b and abs(cosine(a, a) - 1) < 1e-9
    assert cosine(a, e.embed(["intake of programmes"])[0]) < cosine(a, e.embed(["validity of fire certificate"])[0])


def test_clause_aware_chunking_preserves_refs():
    chunks = chunk_regulatory([(1, "CHAPTER S-3: Safety\nS-3.1 Fire safety certificate\nMust be valid.\n"
                                   "S-3.2 Structural stability\nMust be submitted.")])
    assert [c.clause_ref for c in chunks] == ["S-3.1", "S-3.2"]
    assert chunks[0].heading.startswith("Safety") and chunks[0].page_no == 1


def test_indexes_are_physically_separate(client):
    with session_scope() as db:
        reg_cols = set(RegulatoryChunk.__table__.columns.keys())
        ev_cols = set(EvidenceChunk.__table__.columns.keys())
        assert RegulatoryChunk.__tablename__ != EvidenceChunk.__tablename__
        assert "institution_id" not in reg_cols and "institution_id" in ev_cols
        assert db.query(RegulatoryChunk).count() > 0 and db.query(EvidenceChunk).count() > 0


def test_current_retrieval_excludes_superseded_edition(client):
    with session_scope() as db:
        hits = search_regulatory(db, "fire safety certificate validity", k=5)
        assert hits and all(h["source_status"] == "ACTIVE" for h in hits)
        assert any(h["clause_ref"] == "S-3.1" and h["edition"].startswith("2027-28") for h in hits)
        assert all(h["is_synthetic"] for h in hits)  # demo corpus is labelled synthetic, never official


def test_historical_retrieval_uses_edition_in_force(client):
    with session_scope() as db:
        hits = search_regulatory(db, "fire safety certificate validity", as_of=date(2026, 7, 1), k=3)
        assert hits[0]["edition"].startswith("2026-27") and hits[0]["clause_ref"] == "S-3.1"


def test_evidence_retrieval_never_crosses_tenants(client, demo_app):
    with session_scope() as db:
        insts = {i.code: i.id for i in db.scalars(select(Institution))}
        for code, inst_id in insts.items():
            hits = search_evidence(db, "fire safety certificate approval intake programmes", user=None,
                                   institution_id=inst_id, k=20)
            assert all(h["institution_id"] == inst_id for h in hits), code


def test_dual_retrieval_reconciliation_is_persisted(client, officer, demo_app):
    data = client.get(f"/api/v1/applications/{demo_app}/compliance", headers=officer).json()
    assert data["run"]["as_of_date"] == "2027-07-01"
    fire = next(r for r in data["retrievals"] if r["rule_code"] == "SYN-EOA-FS-002")
    assert fire["rule_version"] == 2 and fire["result"] == "FAIL" and fire["source_located"]
    assert fire["regulatory_hits"][0]["clause_ref"] == "S-3.1"
    assert all(h["index"] == "REGULATORY_KNOWLEDGE" for h in fire["regulatory_hits"])
    assert all(h["index"] == "INSTITUTIONAL_EVIDENCE" for h in fire["evidence_hits"])
    assert fire["outcomes"] and fire["timestamp"]
