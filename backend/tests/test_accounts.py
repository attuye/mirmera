"""Accounts and workspaces: sign up and in, every route behind sign-in, workspaces can't
see each other, roles, API tokens, and the first account taking over older projects."""
import re

import pytest
from fastapi.testclient import TestClient

from app import models
from app.config import settings
from app.database import SessionLocal
from app.main import app
from tests.conftest import PASSWORD, sign_up

OPEN = {"/health", "/auth/config", "/auth/signup", "/auth/login", "/auth/logout"}


def test_every_route_needs_signing_in(anon):
    """Walks the API: a route added later without the guard fails here."""
    checked = 0
    for path, ops in app.openapi()["paths"].items():
        if path in OPEN:
            continue
        url = re.sub(r"{\w+}", "x", path)
        for method in ops:
            r = anon.request(method.upper(), url, json={})
            assert r.status_code == 401, f"{method.upper()} {path} answered {r.status_code} without signing in"
            checked += 1
    assert checked > 120
    assert anon.get("/health").status_code == 200


def test_sign_up_sign_in_sign_out(anon):
    assert anon.get("/auth/config").json() == {"allow_signups": True, "has_users": False, "unowned_projects": 0}
    weak = anon.post("/auth/signup", json={"name": "Attu", "email": "attu@x.et", "password": "short"})
    assert weak.status_code == 422 and "at least 10" in weak.json()["detail"]
    me = sign_up(anon, email="Attu@AddisVerify.et")
    assert me["user"]["email"] == "attu@addisverify.et"  # stored lower-case
    assert me["workspaces"] == [{"id": me["workspaces"][0]["id"], "name": "AddisVerify", "role": "owner"}]
    assert anon.get("/auth/me").json()["user"]["name"] == "Attu"
    assert anon.post("/auth/signup", json={"name": "A", "email": "attu@addisverify.et", "password": PASSWORD}).status_code == 409

    assert anon.post("/auth/logout").status_code == 204
    assert anon.get("/auth/me").status_code == 401
    assert anon.post("/auth/login", json={"email": "attu@addisverify.et", "password": "wrong password!"}).status_code == 401
    assert anon.post("/auth/login", json={"email": " ATTU@addisverify.et", "password": PASSWORD}).status_code == 200
    assert anon.get("/auth/me").status_code == 200
    with SessionLocal() as db:  # only a hash of the password and of the token are kept
        user = db.query(models.User).one()
        assert user.password_hash.startswith("scrypt$") and PASSWORD not in user.password_hash
        assert all(len(t.token_hash) == 64 for t in db.query(models.AuthToken))


def test_too_many_wrong_passwords(anon):
    sign_up(anon)
    for _ in range(10):
        anon.post("/auth/login", json={"email": "attu@addisverify.et", "password": "not the one, sorry"})
    r = anon.post("/auth/login", json={"email": "attu@addisverify.et", "password": PASSWORD})
    assert r.status_code == 429 and "15 minutes" in r.json()["detail"]


def test_the_first_account_takes_over_older_projects(anon):
    with SessionLocal() as db:
        db.add(models.Project(id="old", org_id="local", name="AddisVerify KYC"))
        db.commit()
    assert anon.get("/auth/config").json()["unowned_projects"] == 1
    sign_up(anon)
    assert [p["id"] for p in anon.get("/projects").json()] == ["old"]
    with TestClient(app) as other:  # the second account gets an empty workspace
        sign_up(other, name="Sara", email="sara@other.et", workspace="Other Co")
        assert other.get("/projects").json() == []


def test_workspaces_cannot_see_each_other(client, project):
    pid = project["id"]
    charter = client.post(f"/projects/{pid}/charters", json={"mission_statement": "Explore upload"}).json()
    session = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Upload fails"}).json()
    story = client.post(f"/projects/{pid}/stories", json={"title": "Upload a passport"}).json()
    with TestClient(app) as other:
        sign_up(other, name="Sara", email="sara@other.et", workspace="Other Co")
        assert other.get("/projects").json() == []
        assert other.get("/sessions/active").json() == []
        for url in (f"/projects/{pid}", f"/projects/{pid}/risks", f"/charters/{charter['id']}", f"/sessions/{session['id']}",
                    f"/bugs/{bug['id']}", f"/stories/{story['id']}", f"/projects/{pid}/report"):
            assert other.get(url).status_code == 404, url
        assert other.patch(f"/projects/{pid}", json={"name": "Mine now"}).status_code == 404
        assert other.post("/sessions", json={"charter_id": charter["id"], "tester_id": "sara"}).status_code == 404
        assert other.post(f"/projects/{pid}/charters", json={"mission_statement": "x"}).status_code == 404
    assert client.get(f"/projects/{pid}").json()["name"] == "ShopFlow"
    assert [s["id"] for s in client.get("/sessions/active").json()] == [session["id"]]


def add_member(workspace_id, email, role):
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == email).one()
        db.add(models.Membership(workspace_id=workspace_id, user_id=user.id, role=role))
        db.commit()


@pytest.mark.parametrize("role,can_test,can_admin", [("viewer", False, False), ("tester", True, False), ("admin", True, True)])
def test_roles(client, project, role, can_test, can_admin):
    pid, ws = project["id"], client.me["workspaces"][0]["id"]
    with TestClient(app) as other:
        sign_up(other, name="Sara", email="sara@other.et", workspace="Sara's")
        add_member(ws, "sara@other.et", role)
        assert other.get(f"/projects/{pid}").status_code == 200  # everyone can look
        r = other.post(f"/projects/{pid}/charters", json={"mission_statement": "Explore upload"})
        assert r.status_code == (200 if can_test else 403)
        if not can_test:
            assert "viewer" in r.json()["detail"]
        assert other.patch(f"/projects/{pid}", json={"description": "x"}).status_code == (200 if can_admin else 403)
        made = other.post("/projects", json={"name": "New", "org_id": ws})
        assert made.status_code == (200 if can_admin else 403)


def test_api_tokens_for_the_extension(client, project):
    created = client.post("/auth/tokens", json={"label": "Chrome on my laptop"}).json()
    token = created["token"]
    assert token.startswith("mm_")
    listed = client.get("/auth/tokens").json()
    assert [t["label"] for t in listed] == ["Chrome on my laptop"] and "token" not in listed[0]
    with TestClient(app) as ext:  # no cookie, just the header
        h = {"Authorization": f"Bearer {token}"}
        assert ext.get("/sessions/active", headers=h).status_code == 200
        assert ext.get(f"/projects/{project['id']}", headers=h).status_code == 200
        assert client.delete(f"/auth/tokens/{created['id']}").status_code == 204
        assert ext.get("/sessions/active", headers=h).status_code == 401


def test_changing_the_password_signs_out_other_browsers(client):
    with TestClient(app) as laptop:
        assert laptop.post("/auth/login", json={"email": "attu@addisverify.et", "password": PASSWORD}).status_code == 200
        bad = client.post("/auth/password", json={"current_password": "nope nope nope", "new_password": "a new long passphrase"})
        assert bad.status_code == 401
        ok = client.post("/auth/password", json={"current_password": PASSWORD, "new_password": "a new long passphrase"})
        assert ok.status_code == 204
        assert client.get("/auth/me").status_code == 200  # this browser stays signed in
        assert laptop.get("/auth/me").status_code == 401
        assert laptop.post("/auth/login", json={"email": "attu@addisverify.et", "password": "a new long passphrase"}).status_code == 200


def test_more_workspaces_and_which_one_a_project_goes_to(client):
    me = client.post("/workspaces", json={"name": "Client B"}).json()
    assert [w["name"] for w in me["workspaces"]] == ["AddisVerify", "Client B"]
    assert client.post("/projects", json={"name": "Which?"}).status_code == 422  # two workspaces: say which
    b = me["workspaces"][1]["id"]
    assert client.post("/projects", json={"name": "B's app", "org_id": b}).json()["org_id"] == b
    assert client.post("/projects", json={"name": "x", "org_id": "nope"}).status_code == 404
    renamed = client.patch(f"/workspaces/{b}", json={"name": "Client B (KYC)"}).json()
    assert renamed["workspaces"][1]["name"] == "Client B (KYC)"


def test_sign_ups_can_be_turned_off(anon, monkeypatch):
    monkeypatch.setattr(settings, "allow_signups", False)
    assert anon.get("/auth/config").json()["allow_signups"] is True  # the very first account is always allowed
    sign_up(anon)
    with TestClient(app) as other:
        r = other.post("/auth/signup", json={"name": "Sara", "email": "sara@other.et", "password": PASSWORD})
        assert r.status_code == 403
