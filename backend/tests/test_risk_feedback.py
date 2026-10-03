"""Feedback loop: a session suggests risk re-scores, the tester decides, and
every decision becomes the risk's history."""
import json

import pytest

from app.ai.base import LLMProvider


class Scripted(LLMProvider):
    name = "scripted"
    model = "scripted-model"

    def __init__(self, reply: dict):
        self.reply = json.dumps(reply)
        self.users: list[str] = []

    async def complete(self, system, user, **kwargs):
        self.users.append(user)
        return self.reply


def make_risk(client, project, title, impact, likelihood):
    return client.post(f"/projects/{project['id']}/risks", json={
        "title": title, "failure_mode": f"{title} for shoppers", "quality_criterion": "capability",
        "impact": impact, "likelihood": likelihood,
    }).json()


@pytest.fixture
def risks(client, project):
    return make_risk(client, project, "Coupon lost on currency switch", 5, 2), \
        make_risk(client, project, "Rounding differs by currency", 3, 3)


@pytest.fixture
def ended(client, project, risks):
    """A session on a charter covering both risks: notes, one bug, ended."""
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore coupons with currency switches", "risk_ids": [r["id"] for r in risks],
    }).json()
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    client.post(f"/sessions/{s['id']}/notes", json={"tag": "test", "content": "Applied SAVE10 then switched to EUR"})
    client.post(f"/sessions/{s['id']}/bugs", json={"title": "Discount lost on switch to EUR", "severity": "high"})
    client.post(f"/sessions/{s['id']}/end")
    return s


def suggest(client, session, provider=None, monkeypatch=None):
    if provider:
        monkeypatch.setattr("app.routers.risk_feedback.get_provider", lambda name: provider)
    return client.post(f"/sessions/{session['id']}/risk-updates", json={})


def by_ref(feedback):
    return {c["risk_ref"]: c for c in feedback["changes"]}


def risk_now(client, project, risk_id):
    return next(r for r in client.get(f"/projects/{project['id']}/risks").json() if r["id"] == risk_id)


# ---------- suggesting ----------

def test_suggestions_need_an_ended_session_with_risks(client, project, risks, session):
    assert suggest(client, session).status_code == 409  # still running
    client.post(f"/sessions/{session['id']}/end")
    r = suggest(client, session)  # ended, but its charter has no risks
    assert r.status_code == 409 and "no linked risks" in r.json()["detail"]


def test_mock_suggests_raising_likelihood_after_a_bug(client, ended):
    fb = suggest(client, ended).json()
    assert (fb["risk_count"], fb["unreviewed_bugs"]) == (2, 1)
    r1, r2 = by_ref(fb)["R1"], by_ref(fb)["R2"]
    assert (r1["status"], r1["from_likelihood"], r1["to_likelihood"]) == ("proposed", 2, 3)
    assert (r2["status"], r2["to_impact"], r2["to_likelihood"]) == ("proposed", 3, 4)
    assert r1["evidence"] == ["- [unreviewed, high] Discount lost on switch to EUR"]
    assert (r1["source"], r1["prompt_version"]) == ("ai", "risk-update-v1")


def test_the_prompt_gets_scores_notes_bugs_browser_and_debrief(client, ended, monkeypatch):
    provider = Scripted({"updates": [], "unchanged": []})
    suggest(client, ended, provider, monkeypatch)
    user = provider.users[0]
    risks = json.loads(user.split("<risks>\n", 1)[1].split("\n</risks>", 1)[0])
    assert [(r["ref"], r["impact"], r["likelihood"]) for r in risks] == [("R1", 5, 2), ("R2", 3, 3)]
    assert "+0 min [test] Applied SAVE10 then switched to EUR" in user
    assert "- [unreviewed, high] Discount lost on switch to EUR" in user
    assert "<browser>\n(the recorded browser was not used)\n</browser>" in user
    assert '"results"' in user.split("<debrief>", 1)[1]  # the tester's PROOF results and obstacles


def test_guard_rails_on_what_the_model_returns(client, ended, monkeypatch):
    provider = Scripted({
        "updates": [
            {"risk_ref": "R1", "impact": 5, "likelihood": 5, "reason": "Bug confirms it", "evidence": ["- bug"]},
            {"risk_ref": "R1", "impact": 1, "likelihood": 1, "reason": "Duplicate", "evidence": ["x"]},
            {"risk_ref": "R9", "impact": 5, "likelihood": 5, "reason": "Invented", "evidence": ["x"]},
        ],
        "unchanged": [],
    })
    fb = suggest(client, ended, provider, monkeypatch).json()
    r1 = by_ref(fb)["R1"]
    assert (r1["to_impact"], r1["to_likelihood"]) == (5, 4)  # L2 -> 5 capped at +2
    assert r1["reason"].endswith("(capped: at most 2 points per session)")
    assert by_ref(fb)["R2"]["status"] == "no_change"  # the model skipped it; recorded, not lost
    assert by_ref(fb)["R2"]["reason"] == "No suggestion was made for this risk."
    assert set(by_ref(fb)) == {"R1", "R2"}
    assert fb["warnings"] == ["Ignored a second suggestion for R1", "Ignored a suggestion for unknown risk R9"]


def test_a_suggestion_without_evidence_is_refused(client, ended, monkeypatch):
    provider = Scripted({"updates": [
        {"risk_ref": "R1", "impact": 5, "likelihood": 4, "reason": "Feels riskier", "evidence": []},
    ]})
    assert suggest(client, ended, provider, monkeypatch).status_code == 502
    assert len(provider.users) == 2  # one retry, then a clear error
    assert client.get(f"/sessions/{ended['id']}/risk-updates").json()["changes"] == []


# ---------- deciding ----------

def test_accepting_rescoring_the_risk_and_recording_it(client, project, risks, ended):
    change = by_ref(suggest(client, ended).json())["R1"]
    accepted = client.post(f"/risk-changes/{change['id']}/accept", json={"note": "Saw it twice"}).json()
    assert (accepted["status"], accepted["final_likelihood"], accepted["tester_note"]) == ("accepted", 3, "Saw it twice")

    risk = risk_now(client, project, risks[0]["id"])
    assert (risk["likelihood"], risk["score"]) == (3, 15)
    assert risk["last_change"] == "Re-scored I5xL2 -> I5xL3 after a session"
    assert client.post(f"/risk-changes/{change['id']}/accept", json={}).status_code == 409


def test_the_tester_can_accept_with_their_own_numbers(client, project, risks, ended):
    change = by_ref(suggest(client, ended).json())["R2"]
    client.post(f"/risk-changes/{change['id']}/accept", json={"impact": 4, "likelihood": 5})
    risk = risk_now(client, project, risks[1]["id"])
    assert (risk["impact"], risk["likelihood"]) == (4, 5)
    assert risk["last_change"].endswith("(adjusted by tester)")
    assert client.post(f"/risk-changes/{change['id']}/accept", json={"impact": 6}).status_code in (409, 422)


def test_rejecting_keeps_the_score_and_the_reason(client, project, risks, ended):
    change = by_ref(suggest(client, ended).json())["R1"]
    rejected = client.post(f"/risk-changes/{change['id']}/reject", json={"note": "Known issue, fixed in 2.4"}).json()
    assert (rejected["status"], rejected["tester_note"]) == ("rejected", "Known issue, fixed in 2.4")
    assert risk_now(client, project, risks[0]["id"])["likelihood"] == 2


def test_a_stale_suggestion_cannot_be_accepted(client, risks, ended):
    change = by_ref(suggest(client, ended).json())["R1"]
    client.patch(f"/risks/{risks[0]['id']}", json={"likelihood": 4})  # tester re-scores by hand meanwhile
    r = client.post(f"/risk-changes/{change['id']}/accept", json={})
    assert r.status_code == 409 and "re-scored since" in r.json()["detail"]


def test_asking_again_replaces_only_undecided_suggestions(client, ended):
    first = by_ref(suggest(client, ended).json())
    client.post(f"/risk-changes/{first['R1']['id']}/accept", json={})
    again = by_ref(suggest(client, ended).json())
    assert again["R1"]["id"] == first["R1"]["id"] and again["R1"]["status"] == "accepted"  # left alone
    assert again["R2"]["id"] != first["R2"]["id"]  # replaced
    client.post(f"/risk-changes/{again['R2']['id']}/reject", json={})
    assert suggest(client, ended).json()["warnings"] == ["Every risk already has a decision for this session."]


# ---------- history ----------

def test_history_holds_session_decisions_and_manual_rescores(client, risks, ended):
    change = by_ref(suggest(client, ended).json())["R1"]
    client.post(f"/risk-changes/{change['id']}/accept", json={})
    client.patch(f"/risks/{risks[0]['id']}", json={"impact": 4, "tester_notes": "Only affects EUR"})
    history = client.get(f"/risks/{risks[0]['id']}/history").json()
    assert [(h["source"], h["status"]) for h in history] == [("tester", "accepted"), ("ai", "accepted")]
    manual = history[0]
    assert (manual["from_impact"], manual["to_impact"], manual["session_id"]) == (5, 4, None)
    assert manual["tester_note"] == "Only affects EUR"
    assert history[1]["session_id"] == ended["id"]


def test_editing_a_risk_without_rescoring_adds_no_history(client, risks):
    client.patch(f"/risks/{risks[0]['id']}", json={"tester_notes": "Checked with project owner"})
    assert client.get(f"/risks/{risks[0]['id']}/history").json() == []
