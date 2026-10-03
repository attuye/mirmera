"""Projects, artifacts and charters: the planning side of the current API."""
import pytest

from app.ai import base as ai_base
from app.ai.base import LLMProvider, ProviderNotConfigured


class BrokenProvider(LLMProvider):
    name = "broken"

    async def complete(self, system, user, **kwargs):
        raise ProviderNotConfigured("Set ANTHROPIC_API_KEY")


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# ---------- projects ----------

def test_project_is_created_with_the_default_provider(client, project):
    assert project["name"] == "ShopFlow"
    assert project["default_ai_provider"] == "mock"
    assert client.get("/projects").json()[0]["id"] == project["id"]


def test_unknown_project_is_404(client):
    assert client.get("/projects/nope").status_code == 404


# ---------- artifacts ----------

def test_artifact_gets_an_ai_summary(client, project):
    r = client.post(f"/projects/{project['id']}/artifacts",
                    json={"type": "diff", "label": "pr-482", "raw_content": "+ rate = fx.get(c)"})
    assert r.status_code == 200
    assert "Mock summary" in r.json()["ai_summary"]


def test_artifact_is_saved_even_when_the_summary_fails(client, project, monkeypatch):
    # class Broken:
    #     async def summarize_artifact(self, raw, kind):
    #         raise RuntimeError("no API key")

    # monkeypatch.setattr("app.ai.routing.get_provider", lambda name: Broken())
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name: BrokenProvider())
    r = client.post(f"/projects/{project['id']}/artifacts",
                    json={"type": "spec", "label": "spec.md", "raw_content": "Coupons survive FX"})
    assert r.status_code == 200
    assert r.json()["ai_summary"] is None
    assert len(client.get(f"/projects/{project['id']}/artifacts").json()) == 1


def test_artifact_type_is_validated(client, project):
    r = client.post(f"/projects/{project['id']}/artifacts",
                    json={"type": "video", "label": "x", "raw_content": "x"})
    assert r.status_code == 422


# ---------- charter suggestions ----------

def _artifact_id(client, project):
    return client.post(f"/projects/{project['id']}/artifacts",
                       json={"type": "diff", "label": "d", "raw_content": "def apply_coupon(): ..."}).json()["id"]


def test_suggestions_come_from_the_project_provider(client, project):
    r = client.post(f"/projects/{project['id']}/charters/suggest",
                    json={"artifact_ids": [_artifact_id(client, project)]})
    assert r.status_code == 200
    suggestions = r.json()
    assert len(suggestions) == 3
    assert {s["priority"] for s in suggestions} <= {"high", "medium", "low"}


def test_suggestions_need_artifacts_from_this_project(client, project):
    r = client.post(f"/projects/{project['id']}/charters/suggest", json={"artifact_ids": ["other"]})
    assert r.status_code == 400


def test_provider_without_a_key_is_a_clear_503(client, project, monkeypatch):
    monkeypatch.setattr("app.ai.openai_provider.settings.openai_api_key", None)
    r = client.post(f"/projects/{project['id']}/charters/suggest",
                    json={"artifact_ids": [_artifact_id(client, project)], "provider": "openai"})
    assert r.status_code == 503
    assert "OPENAI_API_KEY" in r.json()["detail"]


def test_unknown_provider_is_a_clear_client_error(client, project):
    r = client.post(f"/projects/{project['id']}/charters/suggest",
                    json={"artifact_ids": [_artifact_id(client, project)], "provider": "gpt"})
    assert r.status_code == 400
    assert "gpt" in r.json()["detail"]


# ---------- charters ----------

def test_saved_charter_starts_as_proposed(client, charter):
    assert charter["status"] == "proposed"
    assert charter["source"] == "manual"
    assert charter["risk_areas"] == ["pricing"]


def test_charters_are_listed_per_project(client, project, charter):
    other = client.post("/projects", json={"name": "Other", "org_id": "x"}).json()
    assert [c["id"] for c in client.get(f"/projects/{project['id']}/charters").json()] == [charter["id"]]
    assert client.get(f"/projects/{other['id']}/charters").json() == []


def test_provider_registry_rejects_unknown_names():
    with pytest.raises(ai_base.UnknownProvider):
        ai_base.get_provider("nope")