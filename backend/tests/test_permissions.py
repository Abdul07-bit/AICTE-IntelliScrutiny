from app.core.permissions import P, Role, permissions_for


def test_role_matrix_separates_duties():
    assert P.RULE_APPROVE in permissions_for(Role.REGULATORY_ADMIN)
    assert P.RULE_APPROVE not in permissions_for(Role.SCRUTINY_OFFICER)
    assert P.VERIFY_CONFIRM in permissions_for(Role.VERIFICATION_OFFICER)
    assert P.VERIFY_CONFIRM not in permissions_for(Role.INSTITUTION_UPLOADER)
    assert P.AUDIT_READ in permissions_for(Role.AUDITOR)
    assert P.FINDING_REVIEW not in permissions_for(Role.AUDITOR)
    assert P.USER_MANAGE in permissions_for(Role.SYSTEM_ADMIN)
    assert permissions_for("NOT_A_ROLE") == set()


def test_unauthenticated_requests_are_rejected(client):
    r = client.get("/api/v1/applications")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"
    assert r.json()["error"]["request_id"]


def test_bad_credentials(client):
    r = client.post("/api/v1/auth/login", json={"email": "officer@scrutiny.example", "password": "wrong"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "INVALID_CREDENTIALS"


def test_uploader_sees_only_own_institution(client, uploader_ece, demo_app):
    items = client.get("/api/v1/applications", headers=uploader_ece).json()["items"]
    assert items and all(a["institution"]["code"] == "SYN-INST-0002" for a in items)
    # Another tenant's application is indistinguishable from a missing one.
    assert client.get(f"/api/v1/applications/{demo_app}", headers=uploader_ece).status_code == 404
    assert client.get("/api/v1/findings", params={"application_id": demo_app}, headers=uploader_ece).json()["total"] == 0


def test_uploader_cannot_widen_evidence_search(client, uploader_ece, officer, demo_app):
    other_inst = client.get(f"/api/v1/applications/{demo_app}", headers=officer).json()["institution"]["id"]
    r = client.post("/api/v1/rag/search/evidence", json={"query": "fire safety certificate", "institution_id": other_inst},
                    headers=uploader_ece)
    assert r.status_code == 200
    assert all(h["institution_id"] != other_inst for h in r.json()["hits"])
    # Asking via the other tenant's application id is rejected outright.
    r = client.post("/api/v1/rag/search/evidence", json={"query": "fire", "application_id": demo_app}, headers=uploader_ece)
    assert r.status_code == 404


def test_uploader_cannot_edit_rules_or_confirm_verification(client, uploader_sits):
    r = client.post("/api/v1/rules", headers=uploader_sits, json={
        "rule_code": "X-TEST-1", "title": "Test", "description": "Test",
        "condition": {"op": "min_count", "doc_type": "AFFIDAVIT", "min": 1}})
    assert r.status_code == 403
    r = client.post("/api/v1/verification/requests/nonexistent/manual-outcome", headers=uploader_sits,
                    json={"outcome": "VERIFIED", "note": "should not be allowed"})
    assert r.status_code == 403


def test_auditor_is_read_only(client, auditor, demo_app):
    assert client.get("/api/v1/audit", headers=auditor).status_code == 200
    f = client.get("/api/v1/findings", params={"application_id": demo_app}, headers=auditor).json()["items"][0]
    assert client.post(f"/api/v1/findings/{f['id']}/actions", json={"action": "ACCEPT"}, headers=auditor).status_code == 403
    assert client.post(f"/api/v1/applications/{demo_app}/comments", json={"note": "x"}, headers=auditor).status_code == 403


def test_scrutiny_officer_cannot_read_audit_or_manage_connectors(client, officer):
    assert client.get("/api/v1/audit", headers=officer).status_code == 403
    conns = client.get("/api/v1/connectors", headers=officer).json()
    assert client.patch(f"/api/v1/connectors/{conns[0]['id']}", json={"enabled": False}, headers=officer).status_code == 403


def test_cookie_session_requires_csrf_header(client):
    r = client.post("/api/v1/auth/login", json={"email": "officer@scrutiny.example", "password": "Demo@12345"})
    assert r.status_code == 200
    cookies = {"is_session": r.cookies.get("is_session")}
    client.cookies.clear()
    no_header = client.post("/api/v1/notifications/read-all", cookies=cookies)
    assert no_header.status_code == 403 and no_header.json()["error"]["code"] == "CSRF_REJECTED"
    ok = client.post("/api/v1/notifications/read-all", cookies=cookies, headers={"X-Requested-With": "IntelliScrutiny"})
    assert ok.status_code == 200


def test_signed_file_url_is_bound_to_user(client, officer, verifier, demo_app):
    doc = client.get(f"/api/v1/applications/{demo_app}/documents", headers=officer).json()[0]
    url = client.get(f"/api/v1/documents/{doc['id']}/url", headers=officer).json()["url"]
    assert client.get(url, headers=officer).status_code == 200
    assert client.get(url, headers=verifier).status_code == 403
    assert client.get(url[:-4] + "AAAA", headers=officer).status_code == 403


def test_digilocker_cannot_be_enabled_without_authorization(client):
    from tests.conftest import _login

    h = _login(client, "sysadmin@scrutiny.example")
    dl = next(c for c in client.get("/api/v1/connectors", headers=h).json() if c["kind"] == "DIGILOCKER")
    assert client.patch(f"/api/v1/connectors/{dl['id']}", json={"enabled": True}, headers=h).status_code == 403
