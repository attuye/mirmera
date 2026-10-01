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
def product(client):
    return client.post("/products", json={"name": "ShopFlow", "org_id": "addisverify"}).json()


@pytest.fixture
def charter(client, product):
    return client.post(
        f"/products/{product['id']}/charters",
        json={"mission_statement": "Explore checkout with coupons", "risk_areas": ["pricing"]},
    ).json()


@pytest.fixture
def session(client, charter):
    return client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()