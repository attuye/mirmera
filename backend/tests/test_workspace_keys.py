"""Workspace keys: encrypted at rest, never sent back, used for that workspace's work only."""
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app import keys, models
from app.ai.routing import provider_for
from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.trackers import get_tracker
from tests.conftest import sign_up

TOKEN = "github_pat_11AAAA_supersecretvalue_9xyz"


@pytest.fixture(autouse=True)
def no_server_keys(monkeypatch):
    """As on a fresh server: nothing in backend/.env, so only workspace keys count."""
    for name in keys.NAMES:
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(settings, "mock_trackers", False)
    keys.reset_box()
    yield
    keys.reset_box()


def ws(client):
    return client.me["workspaces"][0]["id"]


def test_keys_are_encrypted_and_never_shown(client):
    out = client.put(f"/workspaces/{ws(client)}/keys", json={"values": {
        "github_token": TOKEN, "jira_base_url": "https://acme.atlassian.net", "jira_email": "qa@acme.et"}}).json()
    by = {k["name"]: k for k in out["keys"]}
    assert by["github_token"]["set"] and by["github_token"]["shown"] == "…9xyz" and by["github_token"]["updated_by"] == "Attu"
    assert by["jira_base_url"]["shown"] == "https://acme.atlassian.net"  # not a secret: shown in full
    assert by["anthropic_api_key"]["set"] is False
    assert TOKEN not in str(client.get(f"/workspaces/{ws(client)}/keys").json())
    with SessionLocal() as db:
        row = db.query(models.WorkspaceKey).filter_by(name="github_token").one()
        assert TOKEN not in row.value and row.value.startswith("gAAAA")  # a Fernet token
    cleared = client.put(f"/workspaces/{ws(client)}/keys", json={"values": {"github_token": ""}}).json()
    assert {k["name"]: k for k in cleared["keys"]}["github_token"]["set"] is False
    assert {k["name"]: k for k in cleared["keys"]}["jira_email"]["set"] is True  # untouched
    assert client.put(f"/workspaces/{ws(client)}/keys", json={"values": {"aws_key": "x"}}).status_code == 400
    assert client.put(f"/workspaces/{ws(client)}/keys", json={"values": {"jira_base_url": "acme.atlassian.net"}}).status_code == 422


def test_only_admins_of_the_workspace(client):
    with TestClient(app) as sara:
        sign_up(sara, name="Sara", email="sara@x.et", workspace="Sara's")
        assert sara.get(f"/workspaces/{ws(client)}/keys").status_code == 404
        with SessionLocal() as db:
            user = db.query(models.User).filter_by(email="sara@x.et").one()
            db.add(models.Membership(workspace_id=ws(client), user_id=user.id, role="tester"))
            db.commit()
        assert sara.get(f"/workspaces/{ws(client)}/keys").status_code == 403
        assert sara.put(f"/workspaces/{ws(client)}/keys", json={"values": {"github_token": "mine"}}).status_code == 403


def test_trackers_use_the_workspace_token_and_other_workspaces_do_not(client, project, monkeypatch):
    client.patch(f"/projects/{project['id']}", json={"github_repo": "addisverify/kyc"})
    status = {t["name"]: t for t in client.get(f"/projects/{project['id']}/trackers").json()}
    assert status["github"]["missing"] == ["a GitHub token (Workspace keys)"]
    client.put(f"/workspaces/{ws(client)}/keys", json={"values": {"github_token": TOKEN}})
    status = {t["name"]: t for t in client.get(f"/projects/{project['id']}/trackers").json()}
    assert status["github"]["configured"] is True
    with SessionLocal() as db:
        tracker = get_tracker("github", db.get(models.Project, project["id"]))
        assert tracker._client_args["headers"]["Authorization"] == f"Bearer {TOKEN}"

    with TestClient(app) as other:
        other_ws = sign_up(other, name="Sara", email="sara@x.et", workspace="Other Co")["workspaces"][0]["id"]
        theirs = other.post("/projects", json={"name": "Theirs"}).json()
        other.patch(f"/projects/{theirs['id']}", json={"github_repo": "other/app"})
        status = {t["name"]: t for t in other.get(f"/projects/{theirs['id']}/trackers").json()}
        assert status["github"]["missing"] == ["a GitHub token (Workspace keys)"]  # not ours

        monkeypatch.setattr(settings, "github_token", "server-token-from-env")  # shared server key
        status = {t["name"]: t for t in other.get(f"/projects/{theirs['id']}/trackers").json()}
        assert status["github"]["configured"] is True
        monkeypatch.setattr(settings, "server_keys_for_all", False)  # hosted for others: bring your own
        status = {t["name"]: t for t in other.get(f"/projects/{theirs['id']}/trackers").json()}
        assert status["github"]["configured"] is False
        listed = other.get(f"/workspaces/{other_ws}/keys").json()
        assert listed["server_keys_for_all"] is False


def test_ai_providers_use_the_workspace_key(client, project):
    pid = project["id"]
    claude = lambda: next(p for p in client.get(f"/ai?project_id={pid}").json()["providers"] if p["name"] == "claude")
    assert claude()["configured"] is False and "Workspace keys" in claude()["problem"]
    client.put(f"/workspaces/{ws(client)}/keys", json={"values": {"anthropic_api_key": "sk-ant-workspace-key-1234"}})
    assert claude()["configured"] is True
    with SessionLocal() as db:
        p = db.get(models.Project, pid)
        p.default_ai_provider = "claude"
        assert provider_for(p, "debrief").client.api_key == "sk-ant-workspace-key-1234"
    with TestClient(app) as other:
        sign_up(other, name="Sara", email="sara@x.et", workspace="Other Co")
        assert other.get(f"/ai?project_id={pid}").status_code == 404


def test_notifications_use_the_workspace_bot_and_never_leak_it(client, project, monkeypatch):
    bot = "123456:workspace-bot-secret"
    client.put(f"/workspaces/{ws(client)}/keys", json={"values": {"telegram_bot_token": bot}})
    client.put(f"/projects/{project['id']}/notifications", json={"telegram_chat_id": "42", "slack": False, "events": ["high_bug"]})
    calls = []

    def fake_post(url, json, timeout):
        calls.append(url)
        return httpx.Response(400, json={"ok": False, "description": f"Bad Request: chat not found for {url}"})
    monkeypatch.setattr("app.notify.httpx.post", fake_post)
    out = client.post(f"/projects/{project['id']}/notifications/test").json()
    assert bot in calls[0]
    assert bot not in str(out) and "•••" in out["recent"][0]["error"]


def test_a_changed_server_key_makes_saved_keys_unreadable(client, monkeypatch):
    client.put(f"/workspaces/{ws(client)}/keys", json={"values": {"github_token": TOKEN}})
    monkeypatch.setattr(settings, "mirmera_secret_key", "a different key")
    keys.reset_box()
    gh = {k["name"]: k for k in client.get(f"/workspaces/{ws(client)}/keys").json()["keys"]}["github_token"]
    assert (gh["set"], gh["unreadable"]) == (False, True)


def test_the_server_key_file_is_made_once(tmp_path, monkeypatch):
    path = tmp_path / "secret.key"
    monkeypatch.setattr(settings, "mirmera_secret_key", None)
    monkeypatch.setattr(settings, "secret_key_file", str(path))
    keys.reset_box()
    secret = keys.encrypt("hello")
    assert path.exists() and keys.decrypt(secret) == "hello"
    if os.name == "posix":
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    keys.reset_box()  # read again from the same file
    assert keys.decrypt(secret) == "hello"
