"""Shared test setup.

The app creates its database engine when it is first imported, so the test
settings must be in place before that happens. Every test gets empty tables
and the mock AI provider, so tests never touch your real mirmera.db or need
an API key.
"""
import os
import tempfile

_TEST_DB = os.path.join(tempfile.mkdtemp(prefix="mirmera-tests-"), "test.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ["DEFAULT_AI_PROVIDER"] = "mock"
os.environ["MIRMERA_SECRET_KEY"] = "test-only key for workspace secrets"  # never writes secret.key

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


PASSWORD = "correct horse battery"


def sign_up(c, name="Attu", email="attu@addisverify.et", workspace="AddisVerify") -> dict:
    """A new account and workspace; the client keeps the sign-in cookie."""
    r = c.post("/auth/signup", json={"name": name, "email": email, "password": PASSWORD, "workspace_name": workspace})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(autouse=True)
def fresh_attempts():
    from app import auth
    auth.reset_attempts()


@pytest.fixture
def anon():
    """Not signed in."""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client():
    """Signed in as the owner of a workspace."""
    with TestClient(app) as c:
        c.me = sign_up(c)
        yield c


@pytest.fixture
def project(client):
    return client.post("/projects", json={"name": "ShopFlow"}).json()


@pytest.fixture
def charter(client, project):
    return client.post(
        f"/projects/{project['id']}/charters",
        json={"mission_statement": "Explore checkout with coupons", "risk_areas": ["pricing"]},
    ).json()


@pytest.fixture
def session(client, charter):
    return client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()

def pytest_runtest_logreport(report):
    """On GitHub Actions, show each failure as an annotation on the PR, so it
    can be read without opening the raw log."""
    if not (os.environ.get("GITHUB_ACTIONS") and report.failed):
        return
    path, line, _ = report.location
    lines = (report.longreprtext or "failed").strip().splitlines()
    errors = [l for l in lines if l.startswith("E ")][-12:] or lines[-3:]
    message = "%0A".join(l.replace("%", "%25").replace("\r", "") for l in errors)
    print(f"\n::error file=backend/{path},line={(line or 0) + 1},title={report.nodeid} ({report.when})::{message}")


def end_and_draft(client, session_id: str) -> dict:
    """Ends a session and returns its debrief once the AI has written it (the test client
    returns after the background drafting has run)."""
    r = client.post(f"/sessions/{session_id}/end")
    assert r.status_code == 200, r.text
    assert r.json()["drafting"] is True  # the answer comes before the AI's draft
    return client.get(f"/sessions/{session_id}/debrief").json()
