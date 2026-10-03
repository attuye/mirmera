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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def project(client):
    return client.post("/projects", json={"name": "ShopFlow", "org_id": "addisverify"}).json()


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
