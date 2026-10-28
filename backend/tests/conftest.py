"""Test configuration: an isolated SQLite database and storage directory, migrations applied via
the real startup path, synthetic seed data loaded, and background jobs executed inline."""
from __future__ import annotations

import os
import shutil
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix="intelliscrutiny-test-")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_TMP}/test.db".replace("\\", "/"),
    "STORAGE_DIR": os.path.join(_TMP, "storage"),
    "APP_ENV": "demo",
    "SEED_DEMO_DATA": "true",
    "JWT_SECRET": "test-secret-" + "x" * 40,
    "RATE_LIMIT_PER_MINUTE": "100000",
    "LOGIN_RATE_LIMIT_PER_MINUTE": "100000",
    "ANTHROPIC_API_KEY": "",
})

from fastapi.testclient import TestClient  # noqa: E402

from app.services import jobs  # noqa: E402

jobs.SYNC_MODE = True

PASSWORD = "Demo@12345"


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
    shutil.rmtree(_TMP, ignore_errors=True)


def _login(client, email: str) -> dict:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    client.cookies.clear()  # tests authenticate with bearer tokens only
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="session")
def officer(client):
    return _login(client, "officer@scrutiny.example")


@pytest.fixture(scope="session")
def verifier(client):
    return _login(client, "verifier@scrutiny.example")


@pytest.fixture(scope="session")
def uploader_ece(client):
    return _login(client, "uploader@ece.example")


@pytest.fixture(scope="session")
def uploader_sits(client):
    return _login(client, "uploader@sits.example")


@pytest.fixture(scope="session")
def regadmin(client):
    return _login(client, "regadmin@scrutiny.example")


@pytest.fixture(scope="session")
def auditor(client):
    return _login(client, "auditor@scrutiny.example")


@pytest.fixture(scope="session")
def demo_app(client, officer):
    """A freshly processed flagship demo application (fixture OCR text for the scanned affidavit)."""
    r = client.post("/api/v1/demo/run", json={"use_fixtures": True}, headers=officer)
    assert r.status_code == 202, r.text
    app_id = r.json()["application_id"]
    job = client.get(f"/api/v1/jobs/{r.json()['job']['id']}", headers=officer).json()
    assert job["status"] == "SUCCEEDED", job
    return app_id


def findings_for(client, headers, app_id) -> list[dict]:
    return client.get("/api/v1/findings", params={"application_id": app_id, "page_size": 100}, headers=headers).json()["items"]
