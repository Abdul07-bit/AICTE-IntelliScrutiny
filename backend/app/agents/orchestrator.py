"""Orchestrator Agent (LangGraph).

Graph:  intake → validation → evidence_indexing → verification → compliance → infrastructure
        → summarize → route

Each node runs in its own DB transaction with a timeout and bounded retries. A node that still
fails is recorded as a partial failure with a manual-review fallback finding, and the graph
continues. Cancellation is checked between nodes (safe points). Step progress is persisted to
`jobs.steps` so the UI can show a live timeline, and LangGraph's checkpointer holds graph state
for the job's thread."""
from __future__ import annotations

import logging
import operator
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Annotated, Callable, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select

from app.agents import compliance, infrastructure, validation, verification
from app.agents.common import SEVERITY_ORDER, add_finding
from app.core.config import get_settings
from app.core.permissions import Role
from app.db.session import session_scope
from app.db.types import utcnow
from app.models import Application, Document, Finding, Job, ValidationRun
from app.rag import indexer
from app.rag.embeddings import get_embedder
from app.services import audit, extraction, workflow
from app.services.notifications import notify_roles

log = logging.getLogger("intelliscrutiny.orchestrator")

STEPS = [
    ("intake", "Intake & malware-scan gate"),
    ("validation", "Validation Agent — OCR, classification, extraction, checks"),
    ("evidence_indexing", "Institutional Evidence Index — embed permitted evidence"),
    ("verification", "Verification Agent — issuer connectors, QR & signatures"),
    ("compliance", "Compliance Agent — dual-index RAG + rule engine"),
    ("infrastructure", "Infrastructure Evidence Assistant"),
    ("summarize", "Preliminary summary"),
    ("route", "Route unresolved items to reviewers"),
]


class PipelineState(TypedDict, total=False):
    job_id: str
    application_id: str
    run_id: str
    use_fixtures: bool
    completed: Annotated[list[str], operator.add]
    failed: Annotated[list[str], operator.add]
    skipped: Annotated[list[str], operator.add]
    results: dict
    cancelled: bool
    no_documents: bool


_node_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="graph-node")


def _set_step(job_id: str, key: str, **changes) -> None:
    with session_scope() as db:
        job = db.get(Job, job_id)
        steps = [dict(s) for s in job.steps]
        for s in steps:
            if s["key"] == key:
                s.update(changes)
        job.steps = steps
        done = sum(1 for s in steps if s["status"] in ("SUCCEEDED", "FAILED", "SKIPPED"))
        job.progress = round(done / len(steps), 3)


def _cancel_requested(job_id: str) -> bool:
    with session_scope() as db:
        return bool(db.get(Job, job_id).cancel_requested)


def _node(key: str, fn: Callable[[PipelineState], dict]) -> Callable[[PipelineState], dict]:
    s = get_settings()

    def run(state: PipelineState) -> dict:
        job_id = state["job_id"]
        if state.get("cancelled") or _cancel_requested(job_id):
            _set_step(job_id, key, status="SKIPPED", detail="Cancelled before this step")
            return {"cancelled": True, "skipped": [key]}
        _set_step(job_id, key, status="RUNNING", started_at=utcnow().isoformat())
        t0 = time.perf_counter()
        last_err = None
        for attempt in range(s.job_node_max_retries + 1):
            try:
                out = _node_pool.submit(fn, state).result(timeout=s.job_node_timeout_seconds) or {}
                detail = out.pop("_detail", None)
                _set_step(job_id, key, status="SUCCEEDED", attempts=attempt + 1, detail=detail,
                          duration_ms=int((time.perf_counter() - t0) * 1000), finished_at=utcnow().isoformat())
                results = dict(state.get("results") or {})
                results[key] = detail
                return {**out, "completed": [key], "results": results}
            except FutureTimeout:
                last_err = f"timed out after {s.job_node_timeout_seconds}s"
            except Exception as exc:
                log.exception("step %s failed (attempt %s)", key, attempt + 1)
                last_err = f"{type(exc).__name__}: {exc}"
        _set_step(job_id, key, status="FAILED", error=last_err, duration_ms=int((time.perf_counter() - t0) * 1000),
                  finished_at=utcnow().isoformat())
        with session_scope() as db:
            app = db.get(Application, state["application_id"])
            run_ = db.get(ValidationRun, state["run_id"])
            add_finding(db, app, run_, agent="ORCHESTRATOR", category="UNRESOLVED_EVIDENCE", code="PIPELINE_STEP_FAILED",
                        title=f"Automated step '{key}' did not complete", severity="MEDIUM",
                        reason=f"{last_err}. Results from this step are missing; other steps continued.",
                        observed=last_err, expected="step completes",
                        recommended_action="Perform this check manually or re-run validation once the issue is fixed.")
        return {"failed": [key]}

    return run


# ---------------------------------------------------------------- node bodies
def n_intake(state: PipelineState) -> dict:
    with session_scope() as db:
        docs = db.scalars(select(Document).where(Document.application_id == state["application_id"])).all()
        quarantined = [d.filename for d in docs if d.malware_scan_status == "INFECTED"]
        usable = [d for d in docs if d.malware_scan_status != "INFECTED"]
        return {"no_documents": not usable,
                "_detail": f"{len(usable)} document(s) accepted; {len(quarantined)} quarantined"}


def n_validation(state: PipelineState) -> dict:
    with session_scope() as db:
        app = db.get(Application, state["application_id"])
        stats = validation.process_documents(db, app, state.get("use_fixtures", False))
    with session_scope() as db:
        app = db.get(Application, state["application_id"])
        n = validation.validate(db, app, db.get(ValidationRun, state["run_id"]))
        methods = ", ".join(f"{k}×{v}" for k, v in stats["methods"].items()) or "none"
        return {"_detail": f"{stats['processed']} processed ({methods}), {stats['failed']} failed; {n} flag(s)"}


def n_evidence(state: PipelineState) -> dict:
    with session_scope() as db:
        docs = db.scalars(select(Document).where(Document.application_id == state["application_id"],
                                                 Document.status == "PROCESSED")).all()
        n = sum(indexer.index_document(db, d) for d in docs)
        return {"_detail": f"{n} evidence chunk(s) embedded with {get_embedder().model_id}"}


def n_verification(state: PipelineState) -> dict:
    with session_scope() as db:
        app = db.get(Application, state["application_id"])
        counts = verification.run_verification(db, app, db.get(ValidationRun, state["run_id"]))
        return {"_detail": ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) or "No applicable connectors"}


def n_compliance(state: PipelineState) -> dict:
    with session_scope() as db:
        app = db.get(Application, state["application_id"])
        run_ = db.get(ValidationRun, state["run_id"])
        out = compliance.run_compliance(db, app, run_)
        summary = dict(run_.summary or {})
        summary["checklist"] = out["checklist"]
        run_.summary = summary
        failed = sum(1 for c in out["checklist"] if c["result"] == "FAIL")
        return {"_detail": f"{out['rules_evaluated']} rule(s) evaluated; {failed} not met"}


def n_infrastructure(state: PipelineState) -> dict:
    with session_scope() as db:
        app = db.get(Application, state["application_id"])
        out = infrastructure.analyse(db, app, db.get(ValidationRun, state["run_id"]))
        return {"_detail": f"{out['images']} image(s); {out['observations']} with quality observations"}


def n_summarize(state: PipelineState) -> dict:
    with session_scope() as db:
        app = db.get(Application, state["application_id"])
        run_ = db.get(ValidationRun, state["run_id"])
        findings = db.scalars(select(Finding).where(Finding.run_id == run_.id)).all()
        by_cat: dict[str, int] = {}
        by_sev: dict[str, int] = {}
        for f in findings:
            by_cat[f.category] = by_cat.get(f.category, 0) + 1
            by_sev[f.severity] = by_sev.get(f.severity, 0) + 1
        top = sorted(findings, key=lambda f: -SEVERITY_ORDER.get(f.severity, 0))[:6]
        summary = dict(run_.summary or {})
        summary.update({
            "headline": (f"{by_cat.get('UNMET_REQUIREMENT', 0)} unmet requirement(s), "
                         f"{by_cat.get('UNRESOLVED_EVIDENCE', 0)} unresolved evidence item(s), "
                         f"{by_cat.get('SUSPECTED_ANOMALY', 0)} suspected anomal(ies), "
                         f"{by_cat.get('MACHINE_OBSERVATION', 0)} machine observation(s)."),
            "by_category": by_cat, "by_severity": by_sev,
            "top_items": [{"id": f.id, "title": f.title, "severity": f.severity, "category": f.category} for f in top],
            "verification_status": app.verification_status,
            "partial_failures": state.get("failed", []),
            "disclaimer": "Preliminary, machine-generated and advisory. Not an AICTE decision.",
            "generated_at": utcnow().isoformat(),
        })
        run_.summary = summary
        app.preliminary_summary = summary
        return {"_detail": summary["headline"]}


def n_route(state: PipelineState) -> dict:
    with session_scope() as db:
        app = db.get(Application, state["application_id"])
        run_ = db.get(ValidationRun, state["run_id"])
        workflow.transition(db, app, "VALIDATION_COMPLETE", None)
        app.validated_at = utcnow()
        needs = db.scalar(select(func.count()).select_from(Finding).where(
            Finding.run_id == run_.id, Finding.category != "INFORMATION",
            Finding.severity.in_(["MEDIUM", "HIGH", "CRITICAL"])))
        if needs:
            workflow.transition(db, app, "REVIEW_REQUIRED", None, note=f"{needs} item(s) need human review")
            if app.assigned_reviewer_id:
                from app.services.notifications import notify_user

                notify_user(db, app.assigned_reviewer_id, "Application ready for review",
                            f"{app.reference_no}: {needs} item(s) need review", f"/applications/{app.id}", "WARNING")
            else:
                notify_roles(db, [Role.SCRUTINY_OFFICER], "Application ready for review",
                             f"{app.reference_no}: {needs} item(s) need review", f"/applications/{app.id}", "WARNING")
        return {"_detail": f"Status {app.status}; {needs} item(s) routed for human review"}


def _after_intake(state: PipelineState) -> str:
    if state.get("cancelled"):
        return "summarize"
    return "summarize" if state.get("no_documents") else "validation"


def build_graph():
    g = StateGraph(PipelineState)
    bodies = {"intake": n_intake, "validation": n_validation, "evidence_indexing": n_evidence,
              "verification": n_verification, "compliance": n_compliance, "infrastructure": n_infrastructure,
              "summarize": n_summarize, "route": n_route}
    for key, fn in bodies.items():
        g.add_node(key, _node(key, fn))
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", _after_intake, {"validation": "validation", "summarize": "summarize"})
    g.add_edge("validation", "evidence_indexing")
    g.add_edge("evidence_indexing", "verification")
    g.add_edge("verification", "compliance")
    g.add_edge("compliance", "infrastructure")
    g.add_edge("infrastructure", "summarize")
    g.add_edge("summarize", "route")
    g.add_edge("route", END)
    return g.compile(checkpointer=InMemorySaver())


_graph = None


def graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def initial_steps() -> list[dict]:
    return [{"key": k, "label": label, "status": "PENDING"} for k, label in STEPS]


def run_pipeline(job_id: str) -> None:
    """Entry point used by the job runner."""
    with session_scope() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, job.application_id)
        if app.status != "PROCESSING":
            workflow.transition(db, app, "PROCESSING", None)
        run_ = ValidationRun(application_id=app.id, job_id=job.id, trigger=job.params.get("trigger", "INITIAL"),
                             pipeline_version=extraction.PIPELINE_VERSION, embedding_model=get_embedder().model_id)
        db.add(run_)
        db.flush()
        run_id = run_.id
        audit.record(db, "validation.started", "validation_run", run_id, application_id=app.id,
                     institution_id=app.institution_id, details={"job_id": job_id, "trigger": run_.trigger,
                                                                 "use_fixtures": job.params.get("use_fixtures", False)})
        app_id, use_fixtures = app.id, bool(job.params.get("use_fixtures"))

    final = graph().invoke(
        {"job_id": job_id, "application_id": app_id, "run_id": run_id, "use_fixtures": use_fixtures,
         "completed": [], "failed": [], "skipped": [], "results": {}},
        config={"configurable": {"thread_id": job_id}},
    )

    with session_scope() as db:
        job = db.get(Job, job_id)
        app = db.get(Application, app_id)
        run_ = db.get(ValidationRun, run_id)
        run_.completed_at = utcnow()
        if final.get("cancelled"):
            run_.status = job.status = "CANCELLED"
            if app.status == "PROCESSING":
                workflow.transition(db, app, "SUBMITTED", None, note="Processing cancelled")
        else:
            run_.status = "PARTIAL" if final.get("failed") else "COMPLETED"
            job.status = "PARTIAL" if final.get("failed") else "SUCCEEDED"
        job.result = {"run_id": run_id, "completed": final.get("completed"), "failed": final.get("failed"),
                      "skipped": final.get("skipped"), "results": final.get("results")}
        audit.record(db, "validation.finished", "validation_run", run_id, application_id=app_id,
                     institution_id=app.institution_id,
                     details={"status": run_.status, "failed_steps": final.get("failed"),
                              "rule_versions": run_.rule_versions, "embedding_model": run_.embedding_model,
                              "pipeline_version": run_.pipeline_version})
