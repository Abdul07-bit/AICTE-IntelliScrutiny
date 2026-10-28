"""Registers background job handlers."""
from __future__ import annotations

from app.agents.orchestrator import run_pipeline
from app.db.session import session_scope
from app.models import Job, RegulatorySource
from app.rag import indexer
from app.services import audit, jobs


@jobs.handler("PROCESS_APPLICATION")
def _process(job_id: str) -> None:
    run_pipeline(job_id)


@jobs.handler("INDEX_SOURCE")
def _index(job_id: str) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        src = db.get(RegulatorySource, job.params["source_id"])
        try:
            n = indexer.index_regulatory_source(db, src)
        except Exception:
            db.commit()  # persist FAILED index_status before the job is marked failed
            raise
        job.result = {"chunks": n}
        audit.record(db, "regulatory_source.indexed", "regulatory_source", src.id,
                     details={"chunks": n, "embedding_model": src.embedding_model})


@jobs.handler("EVALUATION")
def _evaluate(job_id: str) -> None:
    from app.services.evaluation import run_evaluation

    with session_scope() as db:
        job = db.get(Job, job_id)
        run = run_evaluation(db, job.created_by_id, int(job.params.get("samples", 24)))
        job.result = {"evaluation_run_id": run.id}
