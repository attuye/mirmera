"""PROOF debrief: drafted when a session ends; outlook charters and observed
risks can be turned into real charters and risks, once each."""
import json
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.ai.base import LLMProvider
from app.database import Base, add_missing_columns


class Scripted(LLMProvider):
    name = "scripted"
    model = "scripted-model"

    def __init__(self, reply: dict):
        self.reply = json.dumps(reply)
        self.users: list[str] = []

    async def complete(self, system, user, **kwargs):
        self.users.append(user)
        return self.reply


PROOF = {
    "charter_status": "partially_fulfilled",
    "charter_status_rationale": "Coupons covered, currencies only EUR",
    "past": "Applied SAVE10 then switched currency.",
    "results": ["Discount lost on switch to EUR"],
    "obstacles": ["FX sandbox was down for 10 minutes"],
    "outlook": [
        {"charter": "Explore rounding with EUR and GBP to discover total mismatches", "reason": "Totals looked odd"},
        {"charter": "Explore coupon expiry with past dates to discover stale discounts", "reason": "Not covered"},
    ],
    "risks_observed": ["Rounding differs between cart and invoice for EUR shoppers"],
    "open_questions": ["Is GBP in scope?"],
}


def note(client, session, tag, content):
    client.post(f"/sessions/{session['id']}/notes", json={"tag": tag, "content": content})


@pytest.fixture
def ended(client, session, monkeypatch):
    """A session ended with a scripted PROOF draft; returns (debrief, provider)."""
    note(client, session, "test", "Applied SAVE10")
    note(client, session, "setup", "Waited for FX sandbox")
    client.post(f"/sessions/{session['id']}/bugs", json={"title": "Discount lost on currency switch"})
    provider = Scripted(PROOF)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name: provider)
    debrief = client.post(f"/sessions/{session['id']}/end").json()
    return debrief, provider


# ---------- drafting ----------

def test_ending_a_session_drafts_a_proof_debrief(ended):
    d, _ = ended
    assert d["charter_status"] == "partially_fulfilled"
    assert d["coverage_summary"] == PROOF["past"]  # Past also fills the old summary field
    assert d["results"] == PROOF["results"]
    assert d["obstacles"] == PROOF["obstacles"]
    assert [o["charter_id"] for o in d["outlook"]] == [None, None]
    assert d["risks_observed"] == [{"text": PROOF["risks_observed"][0], "risk_id": None}]
    assert d["open_questions"] == ["Is GBP in scope?"]
    assert d["tester_feelings"] is None  # never drafted by the AI
    assert (d["ai_provider"], d["prompt_version"]) == ("scripted", "debrief-proof-v2")


def test_the_draft_sees_notes_with_minutes_bugs_risks_and_time_split(client, project, monkeypatch):
    risk = client.post(f"/projects/{project['id']}/risks", json={
        "title": "Coupon lost on currency switch", "failure_mode": "Shoppers overpay",
        "quality_criterion": "capability", "impact": 5, "likelihood": 3,
    }).json()
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore coupons with currencies", "risk_ids": [risk["id"]],
    }).json()
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    note(client, s, "test", "Applied SAVE10")
    bug = client.post(f"/sessions/{s['id']}/bugs", json={"title": "Discount lost", "severity": "high"}).json()
    client.patch(f"/bugs/{bug['id']}", json={
        "kind": "bug", "oracle_heuristic": "claims", "oracle_explanation": "Spec says coupons survive",
    })
    provider = Scripted(PROOF)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name: provider)
    client.post(f"/sessions/{s['id']}/end")

    user = provider.users[0]
    assert "+0 min [test] Applied SAVE10" in user
    assert "- [bug, high, oracle: claims] Discount lost" in user
    assert "Coupon lost on currency switch" in user
    assert "Time split: on charter 100%" in user


def test_mock_provider_drafts_proof_from_tags(client, session):
    note(client, session, "test", "Tried SAVE10")
    note(client, session, "issue", "Staging was slow")
    note(client, session, "question", "Is GBP supported?")
    d = client.post(f"/sessions/{session['id']}/end").json()
    assert d["obstacles"] == ["Staging was slow"]
    assert d["open_questions"] == ["Is GBP supported?"]
    assert len(d["outlook"]) == 1


# ---------- tester edits ----------

def test_tester_adds_feelings_and_edits_results(client, session, ended):
    r = client.patch(f"/sessions/{session['id']}/debrief", json={
        "tester_feelings": "Productive, but the FX outage broke my flow",
        "results": ["Discount lost on switch to EUR", "Checkout works in USD"],
    })
    d = r.json()
    assert d["tester_feelings"] == "Productive, but the FX outage broke my flow"
    assert d["results"][1] == "Checkout works in USD"
    assert d["obstacles"] == PROOF["obstacles"]  # untouched fields stay
    assert d["ai_draft"] == PROOF["past"]


def test_old_style_update_still_works(client, session, ended):
    d = client.patch(f"/sessions/{session['id']}/debrief", json={"final_text": "My summary"}).json()
    assert d["final_text"] == "My summary"


# ---------- outlook -> charters ----------

def test_outlook_charter_is_saved_once_with_the_session_risks(client, project, session, ended):
    d = client.post(f"/sessions/{session['id']}/debrief/outlook/0/charter").json()
    charter_id = d["outlook"][0]["charter_id"]
    assert charter_id and d["outlook"][1]["charter_id"] is None

    charter = client.get(f"/charters/{charter_id}").json()
    assert charter["mission_statement"] == PROOF["outlook"][0]["charter"]
    assert charter["source"] == "follow-up"
    assert charter["status"] == "proposed"

    again = client.post(f"/sessions/{session['id']}/debrief/outlook/0/charter")
    assert again.status_code == 409
    assert len(client.get(f"/projects/{project['id']}/charters").json()) == 2  # original + one follow-up


def test_unknown_outlook_index_is_404(client, session, ended):
    assert client.post(f"/sessions/{session['id']}/debrief/outlook/9/charter").status_code == 404


# ---------- observed risks -> risks ----------

def test_observed_risk_is_promoted_once(client, project, session, ended):
    d = client.post(f"/sessions/{session['id']}/debrief/risks-observed/0/risk", json={
        "title": "Rounding differs by currency", "quality_criterion": "capability", "impact": 4, "likelihood": 3,
    }).json()
    risk_id = d["risks_observed"][0]["risk_id"]
    risk = next(r for r in client.get(f"/projects/{project['id']}/risks").json() if r["id"] == risk_id)
    assert (risk["title"], risk["score"], risk["status"]) == ("Rounding differs by currency", 12, "accepted")
    assert (risk["source"], risk["evidence"]) == ("session", "tester")
    assert risk["failure_mode"] == PROOF["risks_observed"][0]
    assert risk["last_change"] == "Promoted from a session debrief"

    again = client.post(f"/sessions/{session['id']}/debrief/risks-observed/0/risk", json={})
    assert again.status_code == 409


def test_promotion_scores_are_validated(client, session, ended):
    r = client.post(f"/sessions/{session['id']}/debrief/risks-observed/0/risk", json={"impact": 9})
    assert r.status_code == 422


# ---------- upgrading existing databases ----------

def test_existing_debriefs_get_proof_columns_and_still_load(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE debriefs (
                id VARCHAR PRIMARY KEY, session_id VARCHAR NOT NULL UNIQUE, coverage_summary TEXT,
                time_breakdown JSON, open_questions JSON, ai_draft TEXT, final_text TEXT, created_at DATETIME
            );
            INSERT INTO debriefs VALUES ('d1', 's1', 'Old summary', '{}', '["q"]', NULL, NULL, '2026-09-30 10:00:00');
        """)
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine)
    added = {c.split(".")[1] for c in add_missing_columns(engine) if c.startswith("debriefs.")}
    assert {"charter_status", "results", "outlook", "risks_observed", "tester_feelings"} <= added

    from sqlalchemy.orm import Session
    from app import models, schemas
    with Session(engine) as db:
        out = schemas.DebriefOut.model_validate(db.get(models.Debrief, "d1"))
    assert out.coverage_summary == "Old summary"
    assert (out.results, out.outlook, out.risks_observed) == ([], [], [])
