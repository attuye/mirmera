"""The test strategy: facts per section from the project (and a sprint), a draft from them,
an AI draft that may only use numbers from the facts, versions per focus, exports."""
from datetime import timedelta

import pytest

from app import models, strategy
from app.ai import tasks
from app.ai.llm_json import LLMOutputError
from app.database import SessionLocal
from app.timeutil import utcnow
from tests.test_openapi import SPEC


def day(n: int) -> str:
    return (utcnow().date() + timedelta(days=n)).isoformat()


@pytest.fixture
def planned(client, project):
    """A project with a mission, risks, a story in a sprint, a charter and a session, and a spec."""
    pid = project["id"]
    client.patch(f"/projects/{pid}", json={"testing_mission": "Find what stops a customer verifying with a passport",
                                          "app_url": "https://kyc.example"})
    high = client.post(f"/projects/{pid}/risks", json={"title": "Forged IDs pass", "failure_mode": "A forged ID is verified",
                                                      "quality_criterion": "security", "impact": 5, "likelihood": 4}).json()
    low = client.post(f"/projects/{pid}/risks", json={"title": "Logo blurry", "failure_mode": "The logo is blurry on retina",
                                                     "quality_criterion": "usability", "impact": 1, "likelihood": 2}).json()
    story = client.post(f"/projects/{pid}/stories", json={"title": "Upload a passport", "key": "KYC-12"}).json()
    client.put(f"/stories/{story['id']}/risks", json={"ids": [high["id"]]})
    charter = client.post(f"/projects/{pid}/charters", json={"mission_statement": "Explore passport upload", "story_ids": [story["id"]], "risk_ids": [high["id"]],
                                                            "heuristics": ["goldilocks"], "oracles": ["claims"]}).json()
    sprint = client.post(f"/projects/{pid}/sprints", json={"name": "Sprint 14", "goal": "Passports end to end", "starts_on": day(-3),
                                                          "ends_on": day(10), "story_ids": [story["id"]]}).json()
    client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"})
    client.post(f"/projects/{pid}/openapi", json={"text": SPEC})
    return {"pid": pid, "high": high, "low": low, "sprint": sprint}


def test_facts_per_section_for_a_sprint(client, planned):
    out = client.get(f"/projects/{planned['pid']}/strategy?sprint_id={planned['sprint']['id']}").json()
    assert [s["key"] for s in out["sections"]] == list(strategy.KEYS) and out["versions"] == []
    f = out["facts"]
    assert f["mission"][0] == "Testing mission: Find what stops a customer verifying with a passport"
    assert any(l.startswith("Focus: Sprint 14") and "goal: Passports end to end" in l for l in f["mission"])
    assert any("owners: Attu" in l for l in f["context"]) and "App under test: https://kyc.example" in f["context"]
    assert "Story KYC-12: Upload a passport" in f["product"] and any(l.startswith("API KYC API 2.1: 5 endpoints, 0 tried") for l in f["product"])
    assert f["quality"][0].startswith("Security: 1 risk, highest R1 Forged IDs pass (score 20)")
    assert f["risks"][0] == "R1 Forged IDs pass (score 20, security): explored"  # the sprint's own risk first
    assert any("Goldilocks (1)" in l for l in f["approach"])
    assert any(l.startswith("Candidate (low score 2): R2 Logo blurry") for l in f["out_of_scope"])
    assert any("DELETE /documents/{id}" in l for l in f["out_of_scope"])  # deprecated endpoint
    draft = out["draft"]
    assert draft["out_of_scope"].startswith("Decide with the team what won't be tested and why. Candidates:")
    assert draft["mission"].startswith("- Testing mission:")


def test_without_a_mission_or_model_it_says_so(client, project):
    f = client.get(f"/projects/{project['id']}/strategy").json()["facts"]
    assert f["mission"][0].startswith("No testing mission yet")
    assert f["product"][0].startswith("No product model yet")
    assert f["quality"][0].startswith("No accepted risks yet")


def test_versions_per_focus_and_exports(client, planned):
    pid, sid = planned["pid"], planned["sprint"]["id"]
    assert client.post(f"/projects/{pid}/strategy", json={"sections": {"mission": "  "}}).status_code == 422
    assert client.post(f"/projects/{pid}/strategy", json={"sections": {"nope": "x"}}).status_code == 400
    v1 = client.post(f"/projects/{pid}/strategy", json={"sections": {"mission": "Protect passport verification.", "risks": "- R1 first"}}).json()
    v2 = client.post(f"/projects/{pid}/strategy", json={"sections": {"mission": "Protect verification, passports first."}}).json()
    s1 = client.post(f"/projects/{pid}/strategy", json={"sprint_id": sid, "sections": {"mission": "Sprint 14: passports end to end."},
                                                       "ai_provider": "mock", "ai_model": "mock-1"}).json()
    assert (v1["version"], v2["version"], s1["version"]) == (1, 2, 1)  # numbered per focus
    assert (v2["author"], s1["sprint_name"], s1["ai_provider"]) == ("Attu", "Sprint 14", "mock")
    assert [v["version"] for v in client.get(f"/projects/{pid}/strategy").json()["versions"]] == [2, 1]
    assert [v["id"] for v in client.get(f"/projects/{pid}/strategy?sprint_id={sid}").json()["versions"]] == [s1["id"]]

    md = client.get(f"/projects/{pid}/strategy/{v1['id']}/export?format=md")
    assert md.headers["content-disposition"].endswith('filename="test-strategy-v1.md"')
    assert "# Test strategy: ShopFlow" in md.text and "## Mission\n\nProtect passport verification." in md.text
    assert "## Context\n\n_(empty)_" in md.text
    page = client.get(f"/projects/{pid}/strategy/{s1['id']}/export?format=html").text
    assert "<h1>Test strategy: ShopFlow · Sprint 14</h1>" in page
    assert client.get(f"/projects/{pid}/strategy/{v1['id']}/export?format=pdf").status_code == 400
    assert client.get(f"/projects/{pid}/strategy/nope/export").status_code == 404

    out = client.delete(f"/sprints/{sid}").json()
    assert out["deleted"] == {"sprints": 1, "test_strategies": 1}
    assert client.delete(f"/projects/{pid}?confirm=ShopFlow").json()["deleted"]["test_strategies"] == 2


def test_ai_draft_uses_only_the_facts(client, planned):
    out = client.post(f"/projects/{planned['pid']}/strategy/ai-draft", json={"sprint_id": planned["sprint"]["id"]}).json()
    assert out["ai_provider"] == "mock" and out["prompt_version"] == "test-strategy-v1"
    assert out["sections"]["mission"].startswith("(mock) Testing mission: Find what stops")
    assert out["sections"]["out_of_scope"].startswith("(mock) Do we leave out")


class Inventing:
    """A model that adds a number not in the facts, every time."""
    name, model = "inventive", "x"

    def __init__(self):
        self.calls = 0

    async def complete(self, system, user, **kw):
        self.calls += 1
        return '{"sections": {"mission": "We will run 42 sessions."}}'


def test_invented_numbers_are_refused_after_one_retry():
    import asyncio
    model = Inventing()
    with pytest.raises(LLMOutputError, match="42"):
        asyncio.run(tasks.write_strategy(model, project_name="KYC", focus="the whole project", facts={"mission": ["Testing mission: x"]}))
    assert model.calls == 2
