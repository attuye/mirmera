"""The risk model: product model -> risks -> charters from risks."""
import json

import pytest

from app.ai.base import LLMProvider


class Scripted(LLMProvider):
    """Replies with fixed JSON and records the prompts it received."""
    name = "scripted"
    model = "scripted-model"

    def __init__(self, reply: dict):
        self.reply = json.dumps(reply)
        self.users: list[str] = []

    async def complete(self, system, user, **kwargs):
        self.users.append(user)
        return self.reply


@pytest.fixture
def artifact(client, project):
    return client.post(f"/projects/{project['id']}/artifacts", json={
        "type": "diff", "label": "pr-482", "raw_content": "def apply_coupon(code): ...\ndef convert_currency(): ...",
    }).json()


@pytest.fixture
def modelled(client, project, artifact):
    """A project with a product model built from one artifact."""
    r = client.post(f"/projects/{project['id']}/product-model", json={"artifact_ids": [artifact["id"]]})
    assert r.status_code == 200
    return project


@pytest.fixture
def risks(client, modelled):
    return client.post(f"/projects/{modelled['id']}/risks/analyze", json={}).json()


def accept(client, risk):
    return client.patch(f"/risks/{risk['id']}", json={"status": "accepted"}).json()


# ---------- projects ----------

def test_project_mission_and_provider_can_be_updated(client, project):
    r = client.patch(f"/projects/{project['id']}", json={
        "testing_mission": "Find release blockers in checkout", "default_ai_provider": "local",
    })
    assert r.status_code == 200
    assert r.json()["testing_mission"] == "Find release blockers in checkout"
    assert r.json()["default_ai_provider"] == "local"


def test_unknown_provider_is_rejected_on_update(client, project):
    r = client.patch(f"/projects/{project['id']}", json={"default_ai_provider": "gpt"})
    assert r.status_code == 400
    assert client.get(f"/projects/{project['id']}").json()["default_ai_provider"] == "mock"


# ---------- product model ----------

def test_project_model_is_built_and_records_provenance(client, project, artifact):
    r = client.post(f"/projects/{project['id']}/product-model", json={"artifact_ids": [artifact["id"]]})
    model = r.json()
    assert r.status_code == 200
    assert {e["name"] for e in model["elements"]} >= {"apply_coupon", "convert_currency"}
    assert all(e["category"] in {"function", "data"} for e in model["elements"])
    assert model["unknowns"]
    assert (model["ai_provider"], model["prompt_version"]) == ("mock", "product-model-v1")
    assert model["artifact_ids"] == [artifact["id"]]
    assert client.get(f"/projects/{project['id']}/product-model").json()["id"] == model["id"]


def test_project_model_needs_artifacts_from_this_project(client, project):
    r = client.post(f"/projects/{project['id']}/product-model", json={"artifact_ids": ["nope"]})
    assert r.status_code == 400


def test_no_product_model_yet_is_404(client, project):
    assert client.get(f"/projects/{project['id']}/product-model").status_code == 404


def test_huge_artifacts_are_truncated_and_reported(client, project):
    big = client.post(f"/projects/{project['id']}/artifacts",
                      json={"type": "spec", "label": "huge-spec.md", "raw_content": "x" * 20_000}).json()
    r = client.post(f"/projects/{project['id']}/product-model", json={"artifact_ids": [big["id"]]})
    assert r.json()["truncated_artifacts"] == ["huge-spec.md"]


# ---------- risk analysis ----------

def test_risk_analysis_needs_a_product_model(client, project):
    r = client.post(f"/projects/{project['id']}/risks/analyze", json={})
    assert r.status_code == 409


def test_ai_risks_start_proposed_and_are_numbered_by_score(client, risks):
    assert [r["ref"] for r in risks] == ["R1", "R2", "R3"]
    assert [r["score"] for r in risks] == [12, 10, 4]
    assert {r["status"] for r in risks} == {"proposed"}
    assert {r["source"] for r in risks} == {"ai"}
    assert risks[0]["prompt_version"] == "risk-analysis-v1"
    assert {r["evidence"] for r in risks} <= {"stated", "inferred", "speculative"}


def test_second_analysis_continues_numbering_and_sees_existing_risks(client, modelled, risks, monkeypatch):
    provider = Scripted({"risks": [{
        "title": "Rounding differs by currency", "failure_mode": "Totals off by one cent",
        "quality_criterion": "capability", "elements": ["convert_currency"],
        "impact": 2, "likelihood": 3, "rationale": "r", "evidence": "inferred",
    }]})
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    new = client.post(f"/projects/{modelled['id']}/risks/analyze", json={}).json()
    assert [r["ref"] for r in new] == ["R4"]
    assert risks[0]["title"] in provider.users[0]  # told what already exists


def test_risk_list_is_sorted_and_hides_retired(client, modelled, risks):
    client.patch(f"/risks/{risks[0]['id']}", json={"status": "retired"})
    listed = client.get(f"/projects/{modelled['id']}/risks").json()
    assert [r["ref"] for r in listed] == ["R2", "R3"]
    retired = client.get(f"/projects/{modelled['id']}/risks", params={"status": "retired"}).json()
    assert [r["ref"] for r in retired] == ["R1"]


def test_manual_risk_is_accepted_and_labelled_as_tester(client, project):
    r = client.post(f"/projects/{project['id']}/risks", json={
        "title": "Coupon reuse", "failure_mode": "One-time coupon works twice",
        "quality_criterion": "capability", "impact": 4, "likelihood": 2,
    }).json()
    assert (r["ref"], r["status"], r["source"], r["evidence"]) == ("R1", "accepted", "manual", "tester")


# ---------- tester review ----------

def test_accepting_and_rescoring_is_recorded(client, risks):
    risk = risks[0]
    accepted = accept(client, risk)
    assert accepted["status"] == "accepted"
    assert accepted["last_change"] == "Accepted by tester"

    rescored = client.patch(f"/risks/{risk['id']}", json={"impact": 5}).json()
    assert rescored["score"] == 15
    assert rescored["last_change"] == "Re-scored I4xL3 -> I5xL3"


def test_scores_must_stay_between_1_and_5(client, risks):
    assert client.patch(f"/risks/{risks[0]['id']}", json={"impact": 6}).status_code == 422
    assert client.patch(f"/risks/{risks[0]['id']}", json={"likelihood": 0}).status_code == 422


def test_unknown_risk_is_404(client):
    assert client.patch("/risks/nope", json={"status": "accepted"}).status_code == 404


# ---------- charters from risks ----------

def test_charters_need_accepted_risks(client, modelled, risks):
    r = client.post(f"/projects/{modelled['id']}/charters/from-risks", json={})
    assert r.status_code == 400  # all three are still only proposed


def test_charters_are_drafted_from_accepted_risks_only(client, modelled, risks):
    for risk in risks[:2]:
        accept(client, risk)
    out = client.post(f"/projects/{modelled['id']}/charters/from-risks", json={}).json()
    assert len(out["charters"]) == 1
    draft = out["charters"][0]
    assert draft["risk_refs"] == ["R1", "R2"]
    assert draft["risk_ids"] == [risks[0]["id"], risks[1]["id"]]
    assert draft["mission_statement"].startswith("Explore ")
    assert " to discover " in draft["mission_statement"]
    assert draft["test_ideas"] and draft["oracles"]
    assert out["uncovered_risk_refs"] == []


def test_invented_risk_refs_are_dropped_with_a_warning(client, modelled, risks, monkeypatch):
    for risk in risks:
        accept(client, risk)
    provider = Scripted({"charters": [
        {"target": "checkout", "resources": "test cards", "information": "pricing errors", "rationale": "r",
         "risk_refs": ["R1", "R99"], "test_ideas": ["a"], "oracles": ["claims"], "priority": "high"},
        {"target": "ghost area", "resources": "x", "information": "y", "rationale": "r",
         "risk_refs": ["R42"], "test_ideas": ["a"], "oracles": ["world"], "priority": "low"},
    ]})
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    out = client.post(f"/projects/{modelled['id']}/charters/from-risks", json={}).json()
    assert [c["risk_refs"] for c in out["charters"]] == [["R1"]]
    assert len(out["warnings"]) == 3  # R99 ignored, R42 ignored, ghost charter dropped
    assert out["uncovered_risk_refs"] == ["R2", "R3"]


def test_saved_charter_keeps_its_risks_and_ideas(client, modelled, risks):
    accept(client, risks[0])
    draft = client.post(f"/projects/{modelled['id']}/charters/from-risks", json={}).json()["charters"][0]
    saved = client.post(f"/projects/{modelled['id']}/charters", json={
        "mission_statement": draft["mission_statement"], "rationale": draft["rationale"],
        "priority": draft["priority"], "source": "risk-based",
        "risk_ids": draft["risk_ids"], "test_ideas": draft["test_ideas"], "oracles": draft["oracles"],
    })
    assert saved.status_code == 200
    charter = saved.json()
    assert charter["risk_ids"] == [risks[0]["id"]]
    assert charter["test_ideas"] == draft["test_ideas"]
    assert client.get(f"/charters/{charter['id']}").json()["risk_ids"] == [risks[0]["id"]]


def test_charter_cannot_link_risks_from_another_project(client, project, risks):
    other = client.post("/projects", json={"name": "Other"}).json()
    r = client.post(f"/projects/{other['id']}/charters", json={
        "mission_statement": "Explore", "risk_ids": [risks[0]["id"]],
    })
    assert r.status_code == 400


def test_old_style_charters_still_work(client, charter):
    # Charters without risks (the original flow) keep working unchanged.
    assert charter["risk_ids"] == []
    assert charter["test_ideas"] == [] and charter["oracles"] == []
