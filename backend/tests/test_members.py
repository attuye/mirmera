"""Members: invitations (emailed or shown), joining with or without an account, roles,
removing and leaving, and password reset."""
import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import mailer, models
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.timeutil import utcnow
from tests.conftest import PASSWORD, sign_up


@pytest.fixture
def emails(monkeypatch):
    monkeypatch.setattr(settings, "mock_email", True)
    mailer.SENT.clear()
    return mailer.SENT


def ws_of(client) -> str:
    return client.me["workspaces"][0]["id"]


def token_of(link: str) -> str:
    return link.rsplit("/", 1)[1]


def test_invite_a_new_person_who_signs_up_from_the_link(client, project, emails, monkeypatch):
    monkeypatch.setattr(settings, "allow_signups", False)  # invitations still work
    ws = ws_of(client)
    r = client.post(f"/workspaces/{ws}/invitations", json={"email": "Sara@Other.et", "role": "tester"},
                    headers={"Origin": "http://localhost:5173"})
    inv = r.json()
    assert inv["emailed"] is True and inv["link"].startswith("http://localhost:5173/invite/")
    assert emails[0]["to"] == "sara@other.et" and inv["link"] in emails[0]["text"]
    assert "Attu invited you to AddisVerify" in emails[0]["subject"]
    token = token_of(inv["link"])

    with TestClient(app) as sara:
        view = sara.get(f"/invitations/{token}").json()
        assert view == {"workspace_name": "AddisVerify", "email": "sara@other.et", "role": "tester", "invited_by": "Attu", "has_account": False}
        assert sara.post(f"/invitations/{token}/signup", json={"name": "Sara", "password": "short"}).status_code == 422
        me = sara.post(f"/invitations/{token}/signup", json={"name": "Sara", "password": "another long passphrase"}).json()
        assert me["workspaces"] == [{"id": ws, "name": "AddisVerify", "role": "tester"}]  # no workspace of her own
        assert sara.get(f"/projects/{project['id']}").status_code == 200
        assert sara.get(f"/invitations/{token}").status_code == 404  # used
    members = client.get(f"/workspaces/{ws}/members").json()
    assert [(m["name"], m["role"]) for m in members["members"]] == [("Attu", "owner"), ("Sara", "tester")]
    assert members["invitations"] == []


def test_an_existing_account_accepts_only_with_the_invited_email(client, emails):
    ws = ws_of(client)
    with TestClient(app) as sara, TestClient(app) as eve:
        sign_up(sara, name="Sara", email="sara@other.et", workspace="Sara's")
        sign_up(eve, name="Eve", email="eve@other.et", workspace="Eve's")
        token = token_of(client.post(f"/workspaces/{ws}/invitations", json={"email": "sara@other.et", "role": "viewer"}).json()["link"])
        assert sara.get(f"/invitations/{token}").json()["has_account"] is True
        forwarded = eve.post(f"/invitations/{token}/accept")
        assert forwarded.status_code == 403 and "sara@other.et" in forwarded.json()["detail"]
        assert sara.post(f"/invitations/{token}/signup", json={"name": "S", "password": PASSWORD}).status_code == 409
        me = sara.post(f"/invitations/{token}/accept").json()
        assert sorted(w["name"] for w in me["workspaces"]) == ["AddisVerify", "Sara's"]
    assert client.post(f"/workspaces/{ws}/invitations", json={"email": "sara@other.et"}).status_code == 409  # already in


def test_without_email_the_link_is_shown_and_invites_expire(client):
    ws = ws_of(client)
    inv = client.post(f"/workspaces/{ws}/invitations", json={"email": "sara@other.et", "role": "admin"}).json()
    assert inv["emailed"] is False and "/invite/" in inv["link"]
    again = client.post(f"/workspaces/{ws}/invitations", json={"email": "sara@other.et", "role": "tester"}).json()
    pending = client.get(f"/workspaces/{ws}/members").json()
    assert [(i["email"], i["role"]) for i in pending["invitations"]] == [("sara@other.et", "tester")]  # replaced
    assert pending["email_configured"] is False
    assert client.get(f"/invitations/{token_of(inv['link'])}").status_code == 404  # the old link is dead
    with SessionLocal() as db:
        db.query(models.Invitation).update({"expires_at": utcnow() - timedelta(minutes=1)})
        db.commit()
    with TestClient(app) as anon:
        assert anon.get(f"/invitations/{token_of(again['link'])}").status_code == 410
    assert client.get(f"/workspaces/{ws}/members").json()["invitations"][0]["expired"] is True
    assert client.delete(f"/workspaces/{ws}/invitations/{again['id']}").status_code == 204


def join(client, ws, name, email, role):
    other = TestClient(app).__enter__()
    sign_up(other, name=name, email=email, workspace=f"{name}'s")
    with SessionLocal() as db:
        user = db.query(models.User).filter(models.User.email == email).one()
        db.add(models.Membership(workspace_id=ws, user_id=user.id, role=role))
        db.commit()
        return other, user.id


def test_roles_and_the_last_owner(client):
    ws = ws_of(client)
    admin, admin_id = join(client, ws, "Abebe", "abebe@x.et", "admin")
    tester, tester_id = join(client, ws, "Sara", "sara@x.et", "tester")
    owner_id = client.me["user"]["id"]
    assert tester.post(f"/workspaces/{ws}/invitations", json={"email": "n@x.et"}).status_code == 403
    assert tester.get(f"/workspaces/{ws}/members").status_code == 200  # members can see who's in
    assert admin.post(f"/workspaces/{ws}/invitations", json={"email": "n@x.et", "role": "owner"}).status_code == 403
    assert admin.patch(f"/workspaces/{ws}/members/{tester_id}", json={"role": "viewer"}).status_code == 200
    assert admin.patch(f"/workspaces/{ws}/members/{owner_id}", json={"role": "tester"}).status_code == 403
    assert admin.delete(f"/workspaces/{ws}/members/{owner_id}").status_code == 403
    last = client.patch(f"/workspaces/{ws}/members/{owner_id}", json={"role": "admin"})
    assert last.status_code == 409 and "at least one owner" in last.json()["detail"]
    assert client.post(f"/workspaces/{ws}/leave").status_code == 409
    client.patch(f"/workspaces/{ws}/members/{admin_id}", json={"role": "owner"})
    assert [w["name"] for w in client.post(f"/workspaces/{ws}/leave").json()["workspaces"]] == []
    assert client.get(f"/workspaces/{ws}/members").status_code == 404
    out = admin.delete(f"/workspaces/{ws}/members/{tester_id}").json()
    assert [m["name"] for m in out["members"]] == ["Abebe"]
    assert tester.get(f"/workspaces/{ws}/members").status_code == 404
    assert admin.delete(f"/workspaces/{ws}/members/{admin_id}").status_code == 400  # use Leave
    for c in (admin, tester):
        c.__exit__(None, None, None)


def test_password_reset(anon, emails):
    sign_up(anon)
    with TestClient(app) as laptop:
        laptop.post("/auth/login", json={"email": "attu@addisverify.et", "password": PASSWORD})
        assert anon.post("/auth/forgot", json={"email": "nobody@x.et"}).status_code == 204  # same answer, nothing sent
        assert emails == []
        assert anon.post("/auth/forgot", json={"email": "ATTU@addisverify.et"}).status_code == 204
        token = re.search(r"/reset/(\S+)", emails[0]["text"]).group(1)
        assert anon.post("/auth/reset", json={"token": token, "password": "short"}).status_code == 422
        me = anon.post("/auth/reset", json={"token": token, "password": "a brand new passphrase"}).json()
        assert me["user"]["email"] == "attu@addisverify.et"
        assert laptop.get("/auth/me").status_code == 401  # every other browser is signed out
        assert anon.post("/auth/reset", json={"token": token, "password": "yet another phrase"}).status_code == 400  # once
        assert anon.post("/auth/login", json={"email": "attu@addisverify.et", "password": "a brand new passphrase"}).status_code == 200


def test_reset_links_expire_and_without_email_go_to_the_log(anon, caplog):
    sign_up(anon)
    with caplog.at_level("WARNING"):
        anon.post("/auth/forgot", json={"email": "attu@addisverify.et"})
    token = re.search(r"/reset/(\S+)", caplog.text).group(1)
    with SessionLocal() as db:
        db.query(models.PasswordReset).update({"expires_at": utcnow() - timedelta(seconds=1)})
        db.commit()
    assert anon.post("/auth/reset", json={"token": token, "password": "a brand new passphrase"}).status_code == 400
    for _ in range(9):
        anon.post("/auth/forgot", json={"email": "attu@addisverify.et"})
    assert anon.post("/auth/forgot", json={"email": "attu@addisverify.et"}).status_code == 429
