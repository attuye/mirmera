"""The Mirmera app for Jira: Atlassian-signed tokens only, linking a site with a one-time code,
and the panel on an issue showing that workspace's testing and nothing else."""
import json
import time
from datetime import timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import jira_app, models
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.timeutil import utcnow
from tests.conftest import sign_up

APP_ID = "ari:cloud:ecosystem::app/1b2c3d4e-0000-4000-8000-123456789abc"
INSTALL = "ari:cloud:ecosystem::installation/aaaa-1111"


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


ATLASSIAN = _key()
STRANGER = _key()


def _jwk(private, kid):
    d = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key()))
    return {**d, "kid": kid, "alg": "RS256", "use": "sig"}


@pytest.fixture
def forge(monkeypatch):
    """Atlassian, as far as Mirmera can tell: a JWKS with one key, and tokens signed by it."""
    published = {"keys": [_jwk(ATLASSIAN, "k1")]}
    fetches = []

    def fetch():
        fetches.append(1)
        return published

    monkeypatch.setattr(settings, "jira_app_id", APP_ID)
    monkeypatch.setattr(settings, "public_app_url", "https://mirmera.addisverify.et")
    monkeypatch.setattr(jira_app, "fetch_jwks", fetch)
    jira_app.forget_jwks()

    def token(installation=INSTALL, *, aud=APP_ID, iss=jira_app.ISSUER, exp=300, key=ATLASSIAN, kid="k1", principal="acc-123"):
        now = int(time.time())
        claims = {"iss": iss, "aud": aud, "iat": now, "nbf": now, "exp": now + exp, "jti": "j1", "principal": principal,
                  "app": {"installationId": installation, "id": APP_ID, "environment": {"type": "DEVELOPMENT", "id": "e1"}},
                  "context": {"cloudId": "cloud-42"}}
        return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})}

    token.published = published
    token.fetches = fetches
    yield token
    jira_app.forget_jwks()


def ws(client):
    return client.me["workspaces"][0]["id"]


def link_site(client, forge, anon, installation=INSTALL, site="https://addisverify.atlassian.net"):
    code = client.post(f"/workspaces/{ws(client)}/jira-app/codes").json()["code"]
    r = anon.post("/jira-app/link", json={"code": code, "site_url": site + "/"}, headers=forge(installation))
    assert r.status_code == 200, r.text
    return r.json()


def test_only_tokens_atlassian_signed_for_this_app(anon, forge, monkeypatch):
    assert anon.get("/jira-app/status").status_code == 401
    assert anon.get("/jira-app/status", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401
    assert anon.get("/jira-app/status", headers=forge()).json() == {"linked": False, "workspace": None, "site_url": None,
                                                                     "linked_at": None, "app_url": None}
    for headers, why in [
        (forge(aud="ari:cloud:ecosystem::app/someone-else"), "different Forge app"),
        (forge(exp=-120), "expired"),
        (forge(iss="someone"), "isn't valid"),
        (forge(key=STRANGER), "isn't valid"),  # right key id, wrong key: a forged signature
        (forge(kid="k9"), "doesn't publish"),
    ]:
        r = anon.get("/jira-app/status", headers=headers)
        assert r.status_code == 401 and why in r.json()["detail"], (why, r.text)

    monkeypatch.setattr(settings, "jira_app_id", None)
    r = anon.get("/jira-app/status", headers=forge())
    assert r.status_code == 401 and "JIRA_APP_ID" in r.json()["detail"]


def test_keys_are_cached_and_a_new_key_is_picked_up(anon, forge):
    for _ in range(3):
        assert anon.get("/jira-app/status", headers=forge()).status_code == 200
    assert len(forge.fetches) == 1
    forge.published["keys"].append(_jwk(STRANGER, "k2"))  # Atlassian rotates in a new key
    assert anon.get("/jira-app/status", headers=forge(key=STRANGER, kid="k2")).status_code == 401  # fetched a moment ago
    assert len(forge.fetches) == 1  # made-up key ids can't make Mirmera fetch again and again
    jira_app._jwks["at"] -= jira_app.REFETCH_SECONDS + 1
    assert anon.get("/jira-app/status", headers=forge(key=STRANGER, kid="k2")).status_code == 200
    assert len(forge.fetches) == 2


def test_linking_a_site_with_a_one_time_code(client, anon, forge):
    overview = client.get(f"/workspaces/{ws(client)}/jira-app").json()
    assert overview == {"configured": True, "installations": []}
    made = client.post(f"/workspaces/{ws(client)}/jira-app/codes").json()
    assert made["code"].startswith("mjl_")
    with SessionLocal() as db:
        assert made["code"] not in str([r.code_hash for r in db.query(models.JiraLinkCode)])  # only a hash is kept

    assert anon.post("/jira-app/link", json={"code": "mjl_wrongwrongwrong"}, headers=forge()).status_code == 400
    assert anon.post("/jira-app/link", json={"code": made["code"]}).status_code == 401  # a code alone isn't enough
    r = anon.post("/jira-app/link", json={"code": made["code"], "site_url": "https://addisverify.atlassian.net/"}, headers=forge())
    assert r.json()["linked"] is True and r.json()["workspace"] == "AddisVerify"
    assert r.json()["app_url"] == "https://mirmera.addisverify.et"
    assert anon.post("/jira-app/link", json={"code": made["code"]}, headers=forge("another-site")).status_code == 400  # used

    site = client.get(f"/workspaces/{ws(client)}/jira-app").json()["installations"]
    assert [(s["site_url"], s["cloud_id"], s["environment"], s["linked_by"]) for s in site] == [
        ("https://addisverify.atlassian.net", "cloud-42", "DEVELOPMENT", "Attu")]

    assert client.delete(f"/workspaces/{ws(client)}/jira-app/installations/{site[0]['id']}").status_code == 204
    assert anon.get("/jira-app/status", headers=forge()).json()["linked"] is False
    r = anon.post("/jira-app/issue-panel", json={"issue_key": "KYC-1"}, headers=forge())
    assert r.status_code == 409 and "isn't linked" in r.json()["detail"]


def test_codes_expire_and_jira_can_unlink(client, anon, forge):
    code = client.post(f"/workspaces/{ws(client)}/jira-app/codes").json()["code"]
    with SessionLocal() as db:
        db.query(models.JiraLinkCode).update({"expires_at": utcnow() - timedelta(minutes=1)})
        db.commit()
    assert "expired" in anon.post("/jira-app/link", json={"code": code}, headers=forge()).json()["detail"]
    link_site(client, forge, anon)
    assert anon.delete("/jira-app/link", headers=forge()).json()["linked"] is False
    assert client.get(f"/workspaces/{ws(client)}/jira-app").json()["installations"] == []


def test_only_workspace_admins_manage_the_link(client, forge):
    with TestClient(app) as sara:
        sign_up(sara, name="Sara", email="sara@x.et", workspace="Sara's")
        assert sara.post(f"/workspaces/{ws(client)}/jira-app/codes").status_code == 404
        with SessionLocal() as db:
            user = db.query(models.User).filter_by(email="sara@x.et").one()
            db.add(models.Membership(workspace_id=ws(client), user_id=user.id, role="tester"))
            db.commit()
        assert sara.post(f"/workspaces/{ws(client)}/jira-app/codes").status_code == 403
        assert sara.get(f"/workspaces/{ws(client)}/jira-app").status_code == 403


def _testing_on_kyc_12(client):
    """A story KYC-12 in project KYC, with a risk, a charter, a session and a bug filed as KYC-40."""
    project = client.post("/projects", json={"name": "KYC portal"}).json()
    pid = project["id"]
    client.patch(f"/projects/{pid}", json={"jira_project_key": "KYC"})
    story = client.post(f"/projects/{pid}/stories", json={"title": "Upload a passport", "key": "KYC-12"}).json()
    client.post(f"/stories/{story['id']}/risks/new", json={
        "title": "Expired passports pass", "failure_mode": "An expired passport is verified",
        "quality_criterion": "capability", "impact": 5, "likelihood": 3})
    charter = client.post(f"/projects/{pid}/charters", json={"mission_statement": "Explore passport upload with expired documents"}).json()
    client.put(f"/stories/{story['id']}/charters", json={"ids": [charter["id"]]})
    session = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Expired passport accepted", "severity": "high"}).json()
    with SessionLocal() as db:
        filing = models.BugFiling(bug_id=bug["id"], tracker="jira", target="KYC", mode="new", issue_key="KYC-40",
                                  issue_url="https://addisverify.atlassian.net/browse/KYC-40")
        db.add(filing)
        db.flush()
        db.add(models.Retest(project_id=pid, bug_id=bug["id"], filing_id=filing.id, tracker="jira", issue_key="KYC-40",
                             issue_url=filing.issue_url, outcome="still_fails"))
        db.commit()
    return pid, story, bug


def test_the_panel_on_a_story_a_bug_and_an_untouched_issue(client, anon, forge):
    pid, story, bug = _testing_on_kyc_12(client)
    link_site(client, forge, anon)

    panel = anon.post("/jira-app/issue-panel", json={"issue_key": "kyc-12", "project_key": "KYC"}, headers=forge()).json()
    assert panel["issue_key"] == "KYC-12" and panel["workspace"] == "AddisVerify" and panel["bugs"] == []
    [s] = panel["stories"]
    assert (s["title"], s["risks"], s["charters"], s["sessions"], s["bugs"]) == ("Upload a passport", 1, 1, 1, 1)
    assert s["url"] == f"https://mirmera.addisverify.et/projects/{pid}/stories/{story['id']}"
    assert any("bug report" in line and "to review" in line for line in s["left"])

    panel = anon.post("/jira-app/issue-panel", json={"issue_key": "KYC-40"}, headers=forge()).json()
    assert panel["stories"] == []
    [b] = panel["bugs"]
    assert (b["title"], b["severity"], b["filed_as"], b["retest"]) == ("Expired passport accepted", "high", "new issue", "still_fails")
    assert b["found_in"] == "Explore passport upload with expired documents"

    panel = anon.post("/jira-app/issue-panel", json={"issue_key": "KYC-99"}, headers=forge()).json()
    assert panel["stories"] == [] and panel["bugs"] == []
    assert panel["projects"] == [{"id": pid, "name": "KYC portal", "stories_url": f"https://mirmera.addisverify.et/projects/{pid}/stories"}]

    assert anon.post("/jira-app/issue-panel", json={"issue_key": "not a key"}, headers=forge()).status_code == 422


def test_a_site_sees_only_its_own_workspace(client, anon, forge):
    _testing_on_kyc_12(client)  # Attu's workspace has KYC-12
    with TestClient(app) as sara:
        sara.me = sign_up(sara, name="Sara", email="sara@x.et", workspace="Sara's")
        link_site(sara, forge, anon, installation="sara-site")
        panel = anon.post("/jira-app/issue-panel", json={"issue_key": "KYC-12"}, headers=forge("sara-site")).json()
        assert panel["workspace"] == "Sara's" and panel["stories"] == [] and panel["bugs"] == [] and panel["projects"] == []
    link_site(client, forge, anon)
    assert len(anon.post("/jira-app/issue-panel", json={"issue_key": "KYC-12"}, headers=forge()).json()["stories"]) == 1
