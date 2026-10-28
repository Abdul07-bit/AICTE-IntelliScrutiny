import pytest

from app.core.errors import AppError
from app.models import Application, Rule
from app.rules import engine
from app.services import workflow
from tests.conftest import findings_for


def _app(status):
    a = Application(status=status, institution_id="x", reference_no="r", category="EOA", academic_year="2027-28")
    a.id = "test"
    return a


def test_no_approval_or_rejection_states_exist():
    assert not any(s in workflow.STATES for s in ("APPROVED", "REJECTED"))


def test_invalid_transition_rejected():
    from app.models import User

    officer = User(role="SCRUTINY_OFFICER", id="u")
    with pytest.raises(AppError) as e:
        workflow.transition(None, _app("DRAFT"), "READY_FOR_FURTHER_SCRUTINY", officer)
    assert e.value.code == "INVALID_TRANSITION"
    uploader = User(role="INSTITUTION_UPLOADER", id="u2")
    with pytest.raises(AppError) as e:
        workflow.transition(None, _app("REVIEW_IN_PROGRESS"), "READY_FOR_FURTHER_SCRUTINY", uploader)
    assert e.value.code == "TRANSITION_NOT_PERMITTED"
    assert workflow.allowed_next(_app("REVIEW_REQUIRED"), officer) == ["REVIEW_IN_PROGRESS"]


def test_rule_engine_operators_and_test_cases():
    r = Rule(rule_code="T", version=1, condition={"all": [
        {"op": "date_on_or_after", "doc_type": "FIRE_SAFETY_CERTIFICATE", "field": "valid_until",
         "ref": "application.reference_date"}]})
    ctx = {"application": {"reference_date": "2027-07-01"}, "documents": {"FIRE_SAFETY_CERTIFICATE": [
        {"id": "d", "fields": {"valid_until": {"value": "30-09-2026", "normalized": "2026-09-30", "page": 1}}}]}}
    res = engine.evaluate(r, ctx)
    assert res.result == "FAIL" and res.outcomes[0].page == 1 and res.outcomes[0].observed == "2026-09-30"
    ctx["documents"]["FIRE_SAFETY_CERTIFICATE"][0]["fields"]["valid_until"]["normalized"] = None
    assert engine.evaluate(r, ctx).result == "INDETERMINATE"
    assert engine.evaluate(r, {"application": {"reference_date": "2027-07-01"}, "documents": {}}).result == "NOT_APPLICABLE"
    assert engine.validate_condition({"op": "exec_python"})
    assert engine.validate_condition({"all": []})


def test_seeded_rule_test_cases_pass(client, regadmin):
    for r in client.get("/api/v1/rules", params={"status": "APPROVED"}, headers=regadmin).json():
        out = client.post(f"/api/v1/rules/{r['id']}/test", json={}, headers=regadmin).json()
        assert all(t["passed"] for t in out["tests"]), r["rule_code"]


def test_full_review_workflow(client, officer, verifier, uploader_sits, demo_app):
    app = client.get(f"/api/v1/applications/{demo_app}", headers=officer).json()
    assert app["status"] == "REVIEW_REQUIRED"
    assert app["verification_status"] == "AWAITING_MANUAL_CONFIRMATION"
    f = next(x for x in findings_for(client, officer, demo_app) if x["rule_code"] == "SYN-EOA-INTAKE-004")
    # Accept / dispute / note — the original finding text is never modified.
    assert client.post(f"/api/v1/findings/{f['id']}/actions", json={"action": "DISPUTE"}, headers=officer).status_code == 400
    r = client.post(f"/api/v1/findings/{f['id']}/actions", json={"action": "REQUEST_EVIDENCE",
                                                                  "note": "Provide intake increase approval"}, headers=officer)
    assert r.status_code == 201
    detail = client.get(f"/api/v1/findings/{f['id']}", headers=officer).json()
    assert detail["reason"] == f["reason"] and detail["review_status"] == "EVIDENCE_REQUESTED"
    assert [a["action"] for a in detail["review_history"]] == ["REQUEST_EVIDENCE"]
    assert client.get(f"/api/v1/applications/{demo_app}", headers=officer).json()["status"] == "REVIEW_IN_PROGRESS"

    # Manual issuer confirmation completes the verification track independently of the workflow.
    queue = client.get("/api/v1/verification/requests", params={"application_id": demo_app, "manual_queue": True},
                       headers=verifier).json()["items"]
    for vr in queue:
        r = client.post(f"/api/v1/verification/requests/{vr['id']}/manual-outcome",
                        json={"outcome": "VERIFIED", "note": "Confirmed with issuer (synthetic)"}, headers=verifier)
        assert r.status_code == 200
    assert client.get(f"/api/v1/applications/{demo_app}", headers=officer).json()["verification_status"] \
        == "COMPLETED_WITH_EXCEPTIONS"

    # Human outcome requires a note; it is a scrutiny-stage outcome, not an approval.
    assert client.post(f"/api/v1/applications/{demo_app}/review-outcome",
                       json={"outcome": "APPROVED", "note": "x"}, headers=officer).status_code == 422
    r = client.post(f"/api/v1/applications/{demo_app}/review-outcome",
                    json={"outcome": "RECTIFICATION_REQUESTED", "note": "Renew fire certificate; clarify intake"},
                    headers=officer)
    assert r.json()["status"] == "RECTIFICATION_REQUESTED"

    # Institution resubmits; validation re-runs and pins rule versions in a new run.
    assert client.post(f"/api/v1/applications/{demo_app}/submit", headers=uploader_sits).status_code == 200
    job = client.post(f"/api/v1/applications/{demo_app}/process", json={"use_fixtures": True}, headers=officer).json()
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=officer).json()["status"] == "SUCCEEDED"
    runs = client.get(f"/api/v1/applications/{demo_app}/runs", headers=officer).json()
    assert len(runs) == 2 and runs[0]["trigger"] == "RERUN_AFTER_RECTIFICATION"
    assert {"rule_code": "SYN-EOA-FS-002", "version": 2} in [
        {k: v[k] for k in ("rule_code", "version")} for v in runs[0]["rule_versions"]]


def test_rule_changes_do_not_rewrite_history(client, officer, regadmin, demo_app):
    before = {f["id"]: (f["rule_code"], f["rule_version"], f["reason"])
              for f in findings_for(client, officer, demo_app) if f["rule_code"]}
    rule = next(r for r in client.get("/api/v1/rules", params={"rule_code": "SYN-EOA-AFF-005"}, headers=regadmin).json())
    new = client.post("/api/v1/rules", headers=regadmin, json={
        "rule_code": rule["rule_code"], "title": rule["title"], "description": rule["description"],
        "source_id": rule["source_id"], "clause_ref": rule["clause_ref"], "severity": "LOW",
        "condition": {**rule["condition"], "max_days": 30}, "change_note": "test"}).json()
    assert new["version"] == rule["version"] + 1 and new["status"] == "DRAFT"
    assert client.post(f"/api/v1/rules/{new['id']}/approve", headers=regadmin).status_code == 409  # must submit first
    assert client.post(f"/api/v1/rules/{new['id']}/submit", headers=regadmin).status_code == 200
    approved = client.post(f"/api/v1/rules/{new['id']}/approve", headers=regadmin).json()
    assert approved["status"] == "APPROVED" and "waived in demo" in approved["change_note"]
    after = {f["id"]: (f["rule_code"], f["rule_version"], f["reason"])
             for f in findings_for(client, officer, demo_app) if f["rule_code"]}
    assert before == after


def test_audit_trail_records_actions_and_chain_is_intact(client, auditor, demo_app):
    events = client.get("/api/v1/audit", params={"application_id": demo_app, "page_size": 100}, headers=auditor).json()
    actions = {e["action"] for e in events["items"]}
    assert {"application.created", "document.uploaded", "validation.started", "validation.finished",
            "verification.completed", "application.status_changed"} <= actions
    assert client.get("/api/v1/audit/verify-chain", headers=auditor).json()["valid"] is True


def test_cancel_and_conflicting_jobs(client, officer, demo_app):
    from app.db.session import session_scope
    from app.models import Job
    from app.services import jobs

    with session_scope() as db:
        j = jobs.create_job(db, "PROCESS_APPLICATION", application_id=demo_app, params={})
        jid = j.id
    r = client.post(f"/api/v1/applications/{demo_app}/process", json={}, headers=officer)
    assert r.status_code in (409,)
    assert client.post(f"/api/v1/jobs/{jid}/cancel", headers=officer).status_code == 200
    with session_scope() as db:
        db.get(Job, jid).status = "CANCELLED"


def test_evaluation_reports_sample_sizes(client):
    from tests.conftest import _login

    h = _login(client, "sysadmin@scrutiny.example")
    job = client.post("/api/v1/reports/evaluations", json={"samples": 9}, headers=h).json()
    assert client.get(f"/api/v1/jobs/{job['id']}", headers=h).json()["status"] == "SUCCEEDED"
    latest = client.get("/api/v1/reports/summary", headers=h).json()["latest_evaluation"]
    assert latest["sample_counts"]["documents"] == 9 and "not evidence" in latest["metrics"]["caveat"]
