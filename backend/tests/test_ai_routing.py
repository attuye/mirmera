"""AI provider per task, and checking a model task by task."""
import json

import pytest

from app.ai.base import LLMProvider, ProviderNotConfigured
from app.ai.mock_provider import MockProvider


@pytest.fixture
def asked(monkeypatch):
    """Records which provider names were asked for; every one answers like the mock."""
    names = []

    def fake(name):
        names.append(name)
        return MockProvider()
    monkeypatch.setattr("app.ai.routing.get_provider", fake)
    return names


def test_a_task_uses_its_own_provider_and_the_rest_the_default(client, project, session, asked):
    r = client.patch(f"/projects/{project['id']}", json={"ai_task_providers": {"bug_report": "claude", "debrief": "local"}})
    assert r.status_code == 200 and r.json()["ai_task_providers"] == {"bug_report": "claude", "debrief": "local"}
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Total is negative"}).json()
    client.post(f"/bugs/{bug['id']}/draft", json={})
    client.post(f"/sessions/{session['id']}/end")  # drafts the debrief
    client.post(f"/projects/{project['id']}/artifacts", json={"type": "spec", "label": "s", "raw_content": "Coupons"})
    assert asked == ["claude", "local", "mock"]  # bug report, debrief, artifact summary (the default)


def test_a_request_override_still_wins(client, project, session, asked):
    client.patch(f"/projects/{project['id']}", json={"ai_task_providers": {"bug_report": "claude"}})
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Total is negative"}).json()
    client.post(f"/bugs/{bug['id']}/draft", json={"provider": "openai"})
    assert asked == ["openai"]


def test_choices_are_validated_and_can_be_removed(client, project):
    url = f"/projects/{project['id']}"
    assert client.patch(url, json={"ai_task_providers": {"poetry": "mock"}}).status_code == 400
    assert client.patch(url, json={"ai_task_providers": {"debrief": "gpt-9"}}).status_code == 400
    client.patch(url, json={"ai_task_providers": {"debrief": "local", "agent": "claude"}})
    p = client.patch(url, json={"ai_task_providers": {"debrief": "local", "agent": ""}}).json()
    assert p["ai_task_providers"] == {"debrief": "local"}
    assert client.patch(url, json={"ai_task_providers": {}}).json()["ai_task_providers"] == {}


def test_the_overview_lists_tasks_and_which_providers_are_set_up(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "", raising=False)
    o = client.get("/ai").json()
    assert [t["key"] for t in o["tasks"]][:2] == ["artifact_summary", "product_model"] and len(o["tasks"]) == 12
    by_name = {p["name"]: p for p in o["providers"]}
    assert by_name["mock"]["configured"] is True
    assert by_name["claude"]["configured"] is False and "ANTHROPIC_API_KEY" in by_name["claude"]["problem"]


# ---------- model check ----------

def test_every_task_checks_out_on_the_mock(client):
    keys = [t["key"] for t in client.get("/ai").json()["tasks"]]
    for key in keys:
        r = client.post("/ai/check", json={"provider": "mock", "task": key}).json()
        assert r["result"] == "ok", (key, r)
        assert r["sample"], key


class Flaky(LLMProvider):
    """An unusable reply first, then whatever the mock would say."""
    name, model = "flaky", "flaky-1"

    def __init__(self, bad_replies):
        self.bad = bad_replies

    async def complete(self, system, user, **kwargs):
        if self.bad:
            self.bad -= 1
            return "Sure! Here is my answer: …"
        return await MockProvider().complete(system, user, **kwargs)


@pytest.mark.parametrize("bad, result", [(1, "retried"), (2, "failed")])
def test_a_check_says_when_the_first_reply_was_unusable(client, monkeypatch, bad, result):
    monkeypatch.setattr("app.ai.check.get_provider", lambda name: Flaky(bad))
    r = client.post("/ai/check", json={"provider": "local", "task": "bug_report"}).json()
    assert r["result"] == result and r["model"] == "flaky-1"
    assert ("second worked" in r["detail"]) if result == "retried" else ("No JSON" in r["detail"])


def test_a_provider_that_is_not_set_up_fails_with_the_reason(client, monkeypatch):
    def missing(name):
        raise ProviderNotConfigured("Set ANTHROPIC_API_KEY in backend/.env to use the claude provider")
    monkeypatch.setattr("app.ai.check.get_provider", missing)
    r = client.post("/ai/check", json={"provider": "claude", "task": "debrief"}).json()
    assert r["result"] == "failed" and "ANTHROPIC_API_KEY" in r["detail"]


def test_unknown_providers_and_tasks_are_refused(client):
    assert client.post("/ai/check", json={"provider": "gpt-9", "task": "debrief"}).status_code == 400
    assert client.post("/ai/check", json={"provider": "mock", "task": "poetry"}).status_code == 400
