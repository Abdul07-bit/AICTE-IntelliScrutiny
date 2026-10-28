"""Seeds clearly-labelled synthetic reference data and demonstration applications.

Refuses to run when APP_ENV=production. Background applications are processed through the
real pipeline (not inserted as pre-baked findings)."""
from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import Role
from app.core.security import hash_password
from app.db.session import session_scope
from app.db.types import utcnow
from app.demo import fixtures as fx
from app.models import (
    Application,
    Connector,
    Finding,
    Institution,
    InstitutionalRecord,
    RegulatorySource,
    ReviewAction,
    Rule,
    SystemSetting,
    User,
)
from app.rag import indexer
from app.services import audit, documents, jobs, storage, workflow

log = logging.getLogger("intelliscrutiny.seed")

USERS = [
    ("uploader@sits.example", "Synthetic Uploader (SITS)", Role.INSTITUTION_UPLOADER, "SYN-INST-0001"),
    ("uploader@ece.example", "Synthetic Uploader (ECE)", Role.INSTITUTION_UPLOADER, "SYN-INST-0002"),
    ("officer@scrutiny.example", "Synthetic Scrutiny Officer", Role.SCRUTINY_OFFICER, None),
    ("officer2@scrutiny.example", "Synthetic Scrutiny Officer II", Role.SCRUTINY_OFFICER, None),
    ("verifier@scrutiny.example", "Synthetic Verification Officer", Role.VERIFICATION_OFFICER, None),
    ("regadmin@scrutiny.example", "Synthetic Regulatory Administrator", Role.REGULATORY_ADMIN, None),
    ("sysadmin@scrutiny.example", "Synthetic System Administrator", Role.SYSTEM_ADMIN, None),
    ("auditor@scrutiny.example", "Synthetic Auditor (read-only)", Role.AUDITOR, None),
]

DEFAULT_SETTINGS = {
    "retention": {"documents_days": 2555, "audit_days": 3650, "synthetic_demo_days": 30, "evidence_index_days": 2555},
    "processing": {"use_fixture_ocr_by_default": True, "low_confidence_threshold": 0.6},
    "environment": {"label": "SYNTHETIC DEMONSTRATION", "data_classification": "synthetic"},
}


def _rule_defs(src: dict[str, RegulatorySource]) -> list[dict]:
    hb, old = src["handbook-2027-28"], src["handbook-2026-27"]
    ctx_ok = {"application": {"reference_date": "2027-07-01", "institution_name": "X Inst", "institution_aliases": []}}
    return [
        dict(rule_code="SYN-EOA-DOC-001", version=1, title="Mandatory EoA documents submitted",
             description="Application form, fire safety certificate, structural stability certificate and notarised "
                         "affidavit must be present.", source=hb, clause_ref="S-2.1", severity="HIGH",
             finding_category="UNMET_REQUIREMENT",
             condition={"op": "documents_present", "doc_types": ["APPLICATION_FORM", "FIRE_SAFETY_CERTIFICATE",
                                                                 "STRUCTURAL_STABILITY_CERTIFICATE", "AFFIDAVIT"]},
             required_documents=["APPLICATION_FORM", "FIRE_SAFETY_CERTIFICATE", "STRUCTURAL_STABILITY_CERTIFICATE", "AFFIDAVIT"],
             retrieval_query="mandatory documents extension of approval application form fire structural affidavit",
             reviewer_guidance="Request the missing document(s) from the institution.",
             test_cases=[{"name": "all present", "expected": "PASS", "context": {"documents": {
                 t: [{"id": t}] for t in ["APPLICATION_FORM", "FIRE_SAFETY_CERTIFICATE", "STRUCTURAL_STABILITY_CERTIFICATE", "AFFIDAVIT"]}}},
                         {"name": "affidavit missing", "expected": "FAIL", "context": {"documents": {
                             "APPLICATION_FORM": [{"id": "a"}]}}}]),
        dict(rule_code="SYN-EOA-FS-002", version=1, title="Fire safety certificate valid on submission date",
             description="(Superseded edition) Fire safety certificate valid on the date of submission.",
             source=old, clause_ref="S-3.1", severity="HIGH", finding_category="UNMET_REQUIREMENT",
             effective_from=date(2025, 10, 1), effective_to=date(2026, 9, 30),
             condition={"all": [{"op": "date_on_or_after", "doc_type": "FIRE_SAFETY_CERTIFICATE",
                                 "field": "valid_until", "ref": "application.submission_date"}]},
             required_documents=["FIRE_SAFETY_CERTIFICATE"], required_fields=["valid_until"],
             retrieval_query="fire safety certificate validity date of submission",
             reviewer_guidance="Request a currently valid fire safety certificate.", change_note="Initial version"),
        dict(rule_code="SYN-EOA-FS-002", version=2, title="Fire safety certificate valid on session start date",
             description="Fire safety certificate must remain valid on the reference date (1 July of the academic year).",
             source=hb, clause_ref="S-3.1", severity="HIGH", finding_category="UNMET_REQUIREMENT",
             effective_from=date(2026, 10, 1),
             condition={"all": [{"op": "field_present", "doc_type": "FIRE_SAFETY_CERTIFICATE", "field": "valid_until"},
                                {"op": "date_on_or_after", "doc_type": "FIRE_SAFETY_CERTIFICATE",
                                 "field": "valid_until", "ref": "application.reference_date"}]},
             required_documents=["FIRE_SAFETY_CERTIFICATE"], required_fields=["valid_until"],
             retrieval_query="fire safety certificate shall remain valid on the reference date academic session",
             reviewer_guidance="Request a renewed fire safety certificate valid through the session start date. "
                               "Evidence of a pending renewal may be considered (see IN-01) but is not a substitute.",
             change_note="Reference date changed from submission date to session start (2027-28 edition)",
             test_cases=[
                 {"name": "valid through session", "expected": "PASS", "context": {**ctx_ok, "documents": {
                     "FIRE_SAFETY_CERTIFICATE": [{"id": "f", "fields": {"valid_until": {"value": "31-12-2028",
                                                                                        "normalized": "2028-12-31"}}}]}}},
                 {"name": "expires before session", "expected": "FAIL", "context": {**ctx_ok, "documents": {
                     "FIRE_SAFETY_CERTIFICATE": [{"id": "f", "fields": {"valid_until": {"value": "30-09-2026",
                                                                                        "normalized": "2026-09-30"}}}]}}}]),
        dict(rule_code="SYN-EOA-NAME-003", version=1, title="Institution name consistent across documents",
             description="Institution name on certificates and affidavit must match the registered name.",
             source=hb, clause_ref="S-2.2", severity="HIGH", finding_category="UNRESOLVED_EVIDENCE",
             condition={"op": "name_matches", "doc_types": ["APPLICATION_FORM", "FIRE_SAFETY_CERTIFICATE",
                                                            "STRUCTURAL_STABILITY_CERTIFICATE", "OCCUPANCY_CERTIFICATE",
                                                            "ACCREDITATION_CERTIFICATE", "AFFIDAVIT"],
                        "field": "institution_name", "ref": "application.institution_name", "min_similarity": 0.93},
             retrieval_query="name of the institution on every certificate shall match the registered name",
             reviewer_guidance="Ask the institution to explain the variation (e.g. recorded change of name) or provide "
                               "a corrected certificate from the issuer.",
             test_cases=[{"name": "exact", "expected": "PASS", "context": {**ctx_ok, "documents": {"AFFIDAVIT": [
                 {"id": "a", "fields": {"institution_name": {"value": "X Inst"}}}]}}},
                         {"name": "different", "expected": "FAIL", "context": {**ctx_ok, "documents": {"AFFIDAVIT": [
                             {"id": "a", "fields": {"institution_name": {"value": "Another College"}}}]}}}]),
        dict(rule_code="SYN-EOA-INTAKE-004", version=1, title="EoA intake within previously approved intake",
             description="Declared intake per programme must not exceed the preceding year's approved intake.",
             source=hb, clause_ref="S-4.1", severity="HIGH", finding_category="UNMET_REQUIREMENT",
             condition={"op": "intake_within_approved", "record_type": "PRIOR_APPROVAL"},
             retrieval_query="intake for each programme shall not exceed intake approved for preceding academic year",
             reviewer_guidance="An intake increase needs a separate application; confirm the intended intake."),
        dict(rule_code="SYN-EOA-AFF-005", version=1, title="Affidavit notarised within 180 days",
             description="Affidavit must be notarised within 180 days before submission.", source=hb, clause_ref="S-2.3",
             severity="MEDIUM", finding_category="UNMET_REQUIREMENT",
             condition={"op": "date_within_days", "doc_type": "AFFIDAVIT", "field": "notarised_date",
                        "ref": "application.submission_date", "max_days": 180},
             retrieval_query="affidavit shall be notarised not earlier than 180 days before submission",
             reviewer_guidance="Request a freshly notarised affidavit."),
        dict(rule_code="SYN-EOA-INFRA-006", version=1, title="Infrastructure evidence: building plan and photographs",
             description="Approved building plan and at least four photographs of instructional spaces.",
             source=hb, clause_ref="S-5.1", severity="MEDIUM", finding_category="UNRESOLVED_EVIDENCE",
             condition={"all": [{"op": "documents_present", "doc_types": ["BUILDING_PLAN"]},
                                {"op": "min_count", "doc_type": "INFRASTRUCTURE_PHOTO", "min": 4}]},
             retrieval_query="approved building plan at least four photographs instructional spaces inspection",
             reviewer_guidance="Request the missing evidence or recommend a physical inspection; photographs alone do "
                               "not establish compliance."),
        dict(rule_code="SYN-EOA-FAC-008", version=1, title="Faculty list submitted (draft)",
             description="Draft rule awaiting approval — not applied to evaluations until approved.", source=hb,
             clause_ref=None, severity="LOW", status="PENDING_APPROVAL",
             condition={"op": "documents_present", "doc_types": ["FACULTY_LIST"]}),
    ]


def _connectors() -> list[dict]:
    return [
        dict(key="syn-fire-services", name="Synthetic State Fire Services API", kind="ISSUER_API",
             issuer_name="Synthetic State Fire and Emergency Services (Mock Issuer)", doc_types=["FIRE_SAFETY_CERTIFICATE"],
             config={"adapter": "mock_issuer_api", "issuer_name": "Synthetic State Fire Services",
                     "endpoint": "mock://syn-fire/certificates", "records": {
                         "SYN-FSC-2023-0457": {"holder": fx.INST1["name"], "issue_date": "2023-10-01",
                                               "valid_until": "2026-09-30", "status": "ACTIVE"},
                         "SYN-FSC-ECE-01": {"holder": fx.INST2["name"], "issue_date": "2026-04-01",
                                            "valid_until": "2029-03-31", "status": "ACTIVE"}}}),
        dict(key="syn-municipal-engineering", name="Synthetic Municipal Engineering Registry", kind="ISSUER_API",
             issuer_name="Synthetic Municipal Engineering Department (Mock Issuer)",
             doc_types=["STRUCTURAL_STABILITY_CERTIFICATE"], timeout_ms=1500, max_retries=2,
             config={"adapter": "mock_issuer_api", "simulate_outage": True, "endpoint": "mock://syn-ulb/stability",
                     "outage_message": "Registry endpoint returned no response (simulated maintenance window)",
                     "records": {}}),
        dict(key="syn-accreditation-council", name="Synthetic Accreditation Council Registry", kind="ISSUER_API",
             issuer_name="Synthetic National Accreditation Council (Mock Issuer)", doc_types=["ACCREDITATION_CERTIFICATE"],
             config={"adapter": "mock_issuer_api", "endpoint": "mock://syn-accreditation/lookup", "records": {
                 "SYN-ACC-2025-3301": {"holder": fx.INST1["name"], "issue_date": "2025-07-01",
                                       "valid_until": "2028-06-30", "status": "ACTIVE",
                                       "programmes": ["B.Tech CSE", "B.Tech ECE"]}}}),
        dict(key="qr-resolver", name="QR Code Resolver", kind="QR", issuer_name=None, doc_types=[],
             config={"adapter": "qr_resolver", "known_issuers": {"SYN-FIRE": "Synthetic State Fire Services",
                                                                 "SYN-ULB": "Synthetic Urban Local Body"}}),
        dict(key="digital-signature", name="Digital Signature Verifier (Ed25519)", kind="DIGITAL_SIGNATURE",
             issuer_name=None, doc_types=[],
             config={"adapter": "digital_signature", "issuer_public_keys": {
                 "SYN-FIRE": fx.issuer_public_b64("SYN-FIRE"), "SYN-ULB": fx.issuer_public_b64("SYN-ULB")}}),
        dict(key="digilocker", name="DigiLocker-compatible Connector", kind="DIGILOCKER", mode="LIVE", enabled=False,
             issuer_name=None, doc_types=[], health_status="NOT_CONFIGURED",
             authorization_note="Disabled. Requires official authorization, an approved integration agreement and "
                                "credentials supplied via environment variables. No calls are made.",
             config={"adapter": "digilocker", "client_id_env": "DIGILOCKER_CLIENT_ID",
                     "client_secret_env": "DIGILOCKER_CLIENT_SECRET"}),
        dict(key="manual", name="Manual Issuer Confirmation", kind="MANUAL", issuer_name=None, doc_types=[],
             config={"adapter": "manual"}, health_status="HEALTHY"),
    ]


def seed_reference(db: Session) -> dict:
    s = get_settings()
    insts = {}
    for spec in (fx.INST1, fx.INST2, fx.INST3):
        inst = Institution(code=spec["code"], name=spec["name"], aliases=spec["aliases"], state=spec["state"],
                           address=spec["address"], is_synthetic=True)
        db.add(inst)
        insts[spec["code"]] = inst
    db.flush()
    users = {}
    for email, name, role, inst_code in USERS:
        u = User(email=email, full_name=name, role=role.value, password_hash=hash_password(s.demo_password),
                 institution_id=insts[inst_code].id if inst_code else None, is_synthetic=True)
        db.add(u)
        users[email] = u
    for k, v in DEFAULT_SETTINGS.items():
        db.add(SystemSetting(key=k, value=v))
    for c in _connectors():
        db.add(Connector(**c))
    db.flush()

    # Regulatory Knowledge Index (INDEX 1)
    sources: dict[str, RegulatorySource] = {}
    regadmin = users["regadmin@scrutiny.example"]
    for spec in fx.regulatory_corpus():
        data = fx.make_pdf(spec["pages"], title=f"{spec['title']} {spec['edition']}", accent="#0f766e")
        key, _ = storage.put(data, "regulatory", ".pdf")
        src = RegulatorySource(title=spec["title"], edition=spec["edition"], source_type=spec["source_type"],
                               is_official=spec["is_official"], is_synthetic=True,
                               publication_date=spec["publication_date"], effective_from=spec["effective_from"],
                               effective_to=spec["effective_to"], sha256=storage.sha256(data), storage_key=key,
                               source_url=None, uploaded_by_id=regadmin.id)
        db.add(src)
        db.flush()
        indexer.index_regulatory_source(db, src)
        sources[spec["key"]] = src
        if spec.get("supersedes"):
            indexer.supersede(db, sources[spec["supersedes"]], src)
        audit.record(db, "regulatory_source.indexed", "regulatory_source", src.id, actor=regadmin,
                     details={"title": src.title, "edition": src.edition, "chunks": src.chunk_count, "synthetic": True})

    for d in _rule_defs(sources):
        src = d.pop("source")
        status = d.pop("status", "APPROVED")
        rule = Rule(**d, category="EOA", source_id=src.id, status=status, is_synthetic=True, created_by_id=regadmin.id,
                    approved_by_id=regadmin.id if status == "APPROVED" else None,
                    approved_at=utcnow() if status == "APPROVED" else None)
        db.add(rule)
    db.flush()

    # Authorized prior institutional records (exact-check tables + INDEX 2)
    i1, i2 = insts["SYN-INST-0001"], insts["SYN-INST-0002"]
    records = [
        InstitutionalRecord(institution_id=i1.id, record_type="PRIOR_APPROVAL", reference="SYN-EOA-2026-27-0001",
                            academic_year="2026-27", title="EoA approval letter 2026-27 (synthetic)",
                            source="Synthetic approvals register", valid_from=date(2026, 7, 1), valid_to=date(2027, 6, 30),
                            data={"programmes": [{"programme": "B.Tech Computer Science and Engineering", "intake": 120},
                                                 {"programme": "B.Tech Electronics and Communication Engineering", "intake": 120},
                                                 {"programme": "MBA", "intake": 60}]}),
        InstitutionalRecord(institution_id=i1.id, record_type="PRIOR_APPROVAL", reference="SYN-EOA-2025-26-0001",
                            academic_year="2025-26", title="EoA approval letter 2025-26 (synthetic)", superseded=True,
                            source="Synthetic approvals register",
                            data={"programmes": [{"programme": "B.Tech Computer Science and Engineering", "intake": 120},
                                                 {"programme": "MBA", "intake": 60}]}),
        InstitutionalRecord(institution_id=i1.id, record_type="PAYMENT_RECORD", reference="SYN-PAY-2026-88412",
                            academic_year="2027-28", title="Processing fee receipt (synthetic)",
                            source="Synthetic payment gateway ledger", data={"amount": 150000, "status": "SETTLED"}),
        InstitutionalRecord(institution_id=i1.id, record_type="PREVIOUS_DEFICIENCY", reference="SYN-DEF-2026-014",
                            academic_year="2026-27", title="Deficiency noted in 2026-27 scrutiny (synthetic)",
                            source="Synthetic scrutiny records",
                            data={"observation": "Fire safety certificate due for renewal before 30-09-2026",
                                  "rectification": "Institution undertook to submit renewed fire safety certificate "
                                                   "with the next application", "status": "OPEN"}),
        InstitutionalRecord(institution_id=i1.id, record_type="ACCREDITATION_RECORD", reference="SYN-ACC-2025-3301",
                            academic_year="2025-26", title="Accreditation register entry (synthetic)",
                            source="Synthetic accreditation register",
                            data={"programmes": ["B.Tech CSE", "B.Tech ECE"], "valid_until": "2028-06-30"}),
        InstitutionalRecord(institution_id=i2.id, record_type="PRIOR_APPROVAL", reference="SYN-EOA-2026-27-0002",
                            academic_year="2026-27", title="EoA approval letter 2026-27 (synthetic)",
                            source="Synthetic approvals register",
                            data={"programmes": [{"programme": "B.Tech Mechanical Engineering", "intake": 60},
                                                 {"programme": "B.Tech Civil Engineering", "intake": 60}]}),
        InstitutionalRecord(institution_id=i2.id, record_type="PAYMENT_RECORD", reference="SYN-PAY-2026-90001",
                            academic_year="2027-28", title="Processing fee receipt (synthetic)",
                            source="Synthetic payment gateway ledger", data={"amount": 150000, "status": "SETTLED"}),
    ]
    for r in records:
        db.add(r)
    db.flush()
    for r in records:
        indexer.index_record(db, r)
    audit.record(db, "system.seeded", "system", None, details={"synthetic": True, "institutions": 3, "users": len(USERS)})
    return {"institutions": insts, "users": users}


def next_reference(db: Session, year: str = "2027-28") -> str:
    n = db.scalar(select(func.count()).select_from(Application)) + 1
    return f"SYN-EOA-{year[:4]}-{n:04d}-{secrets.token_hex(2).upper()}"


def create_application(db: Session, inst: Institution, user: User | None, docs: list[fx.FixtureDoc],
                       declared: dict | None = None, reviewer: User | None = None) -> Application:
    app = Application(reference_no=next_reference(db), institution_id=inst.id, category="EOA", academic_year="2027-28",
                      status="DRAFT", declared_data=declared or {}, created_by_id=user.id if user else None,
                      assigned_reviewer_id=reviewer.id if reviewer else None, is_synthetic=True)
    db.add(app)
    db.flush()
    audit.record(db, "application.created", "application", app.id, actor=user, application_id=app.id,
                 institution_id=inst.id, details={"reference_no": app.reference_no, "synthetic": True})
    for d in docs:
        documents.ingest(db, app, user, d.filename, d.content_type, d.data, d.declared_type, d.description,
                         d.fixture_pages)
    return app


def submit_and_process(db: Session, app: Application, user: User | None, use_fixtures: bool,
                       trigger: str = "INITIAL"):
    from app.agents.orchestrator import initial_steps

    if app.status in ("DRAFT", "RECTIFICATION_REQUESTED"):
        workflow.transition(db, app, "SUBMITTED", user, action="SUBMIT")
        app.submitted_at = utcnow()
    job = jobs.create_job(db, "PROCESS_APPLICATION", application_id=app.id, user=user,
                          params={"use_fixtures": use_fixtures, "trigger": trigger}, steps=initial_steps())
    return job


def seed_applications(db_factory=session_scope) -> None:
    today = date.today()
    with db_factory() as db:
        inst = {i.code: i for i in db.scalars(select(Institution))}
        users = {u.email: u for u in db.scalars(select(User))}
        officer, officer2 = users["officer@scrutiny.example"], users["officer2@scrutiny.example"]
        # Background applications, processed by the real pipeline.
        a2 = create_application(db, inst["SYN-INST-0002"], users["uploader@ece.example"],
                                fx.clean_application_docs(fx.INST2, "ece", today - timedelta(days=9),
                                                          [("B.Tech Mechanical Engineering", 60),
                                                           ("B.Tech Civil Engineering", 60)], "SYN-PAY-2026-90001"),
                                reviewer=officer2)
        a3 = create_application(db, inst["SYN-INST-0003"], None,
                                fx.clean_application_docs(fx.INST3, "spd", today - timedelta(days=4),
                                                          [("Diploma Mechanical Engineering", 60)], "SYN-PAY-2026-77777",
                                                          include_plan=False, include_fire=False))
        pending = [(a2, users["uploader@ece.example"]), (a3, officer)]
        job_ids = [submit_and_process(db, a, submitter, True).id for a, submitter in pending]
        # The flagship demo application waits in DRAFT for the guided demo / manual walkthrough.
        demo = create_application(db, inst["SYN-INST-0001"], users["uploader@sits.example"],
                                  fx.demo_application_docs(today), reviewer=officer)
        demo.declared_data = {"scenario": "flagship-synthetic-demo"}
        a2_id, demo_id = a2.id, demo.id
    for jid in job_ids:
        jobs._execute(jid)  # run inline so seeding completes before the API serves requests
    # A human review on the ECE application so review-time metrics have a measured sample.
    with db_factory() as db:
        a2 = db.get(Application, a2_id)
        officer2 = db.scalar(select(User).where(User.email == "officer2@scrutiny.example"))
        if a2.status in ("REVIEW_REQUIRED", "VALIDATION_COMPLETE"):
            workflow.transition(db, a2, "REVIEW_IN_PROGRESS", officer2, note="Started review (synthetic seed)")
            for f in db.scalars(select(Finding).where(Finding.application_id == a2.id,
                                                      Finding.category == "SUSPECTED_ANOMALY")):
                db.add(ReviewAction(application_id=a2.id, finding_id=f.id, actor_id=officer2.id, action="ACCEPT",
                                    note="Seeded synthetic review", previous_status=f.review_status, new_status="ACCEPTED"))
                f.review_status = "ACCEPTED"
    log.info("Seeded demo applications (flagship demo application %s left in DRAFT)", demo_id)


def is_seeded(db: Session) -> bool:
    return db.scalar(select(func.count()).select_from(User)) > 0


def run_seed() -> bool:
    s = get_settings()
    if s.is_production:
        raise RuntimeError("Refusing to seed synthetic data in production")
    import app.services.handlers  # noqa: F401 - registers job handlers

    with session_scope() as db:
        if is_seeded(db):
            return False
        seed_reference(db)
    seed_applications()
    return True
