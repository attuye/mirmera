"""Dismissing a draft with a reason: Mirmera doesn't draft that problem again in the project."""
import time

import pytest

from tests.test_agent_eval import Scripted, step  # noqa: F401
from tests.test_agent_eval import booking_app  # noqa: F401  (fixture)
from tests.test_runner import chromium_available  # noqa: F401  (fixture)


def explore(client, project_id, url, replies, monkeypatch):
    from app import models
    from app.agent import explore as agent
    from app.database import SessionLocal
    provider = Scripted(replies)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    charter = client.post(f"/projects/{project_id}/charters", json={"mission_statement": "Explore booking a table"}).json()
    run = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 4}).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        run = client.get(f"/exploration-runs/{run['id']}").json()
        if run["status"] != "running" and run["id"] not in agent.ACTIVE:
            break
        time.sleep(0.3)
    with SessionLocal() as db:
        notes = [n.content for n in db.get(models.Session, run["session_id"]).notes]
    return run, provider, notes


@pytest.mark.tours
def test_a_dismissed_problem_is_not_drafted_again(client, booking_app, monkeypatch, chromium_available):  # noqa: F811
    from app.agent import explore as agent
    for flag in ("FAULTS", "KEYBOARD", "SAVED_TEXT", "INTERRUPTIONS"):
        monkeypatch.setattr(agent, flag, () if flag == "FAULTS" else False)
    project = client.post("/projects", json={"name": "Tables"}).json()
    client.patch(f"/projects/{project['id']}", json={"app_url": booking_app})
    replies = [step("Open", kind="goto", url=f"{booking_app}/book"),
               step("Book", kind="try", values={"Your name": "Abebe Kebede"}),
               step("Done", kind="done", summary="Booked once.")]
    run, _, _ = explore(client, project["id"], booking_app, replies, monkeypatch)
    drafts = {client.get(f"/bugs/{b}").json()["title"]: b for b in run["bug_ids"]}
    page_error = drafts["Page error: Cannot read properties of null (reading 'name')"]
    double = drafts["Clicking Book twice quickly creates it twice"]

    # The team: the confirmation page is only reached from the form, by design. The double booking stays.
    r = client.post(f"/bugs/{page_error}/dismiss", json={"reason": "by_design", "note": "The confirmation is only shown once, on purpose."})
    assert r.status_code == 200 and r.json()["dismissal"]["reason"] == "by_design"
    assert r.json()["last_change"].startswith("Dismissed by Attu: works as intended")
    listed = client.get(f"/projects/{project['id']}/dismissals").json()
    assert [(d["title"], d["reason"]) for d in listed] == [("Page error: Cannot read properties of null (reading 'name')", "by_design")]

    run2, provider, notes = explore(client, project["id"], booking_app, replies, monkeypatch)
    titles = [client.get(f"/bugs/{b}").json()["title"] for b in run2["bug_ids"]]
    assert "Page error: Cannot read properties of null (reading 'name')" not in titles
    assert "Clicking Book twice quickly creates it twice" in titles  # not dismissed: seen again
    assert any(n.startswith("Not drafted again: Page error: Cannot read properties of null (reading 'name') "
                            "(dismissed on") and "as works as intended" in n for n in notes), notes
    # the AI is told too, so it doesn't report it in other words
    assert '"dismissed_by_the_team": [\n    "\\"Page error: Cannot read properties' in provider.users[0]
    assert "The confirmation is only shown once, on purpose." in provider.users[0]

    # Undo: a draft again, and Mirmera may draft it again.
    undone = client.delete(f"/bugs/{page_error}/dismiss").json()
    assert undone["dismissal"] is None and client.get(f"/projects/{project['id']}/dismissals").json() == []
    assert double  # kept as a draft throughout


def test_cant_reproduce_is_drafted_again_marked_as_seen_before(client, session):
    """Dismissed as 'can't reproduce' and met again: drafted, saying so (it may be intermittent)."""
    from app import dismissals, models, schemas
    from app.agent.explore import _draft_bug
    from app.database import SessionLocal
    from app.ai.mock_provider import MockProvider
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Search times out"}).json()
    p = schemas.ExploreProblem(title="Search times out", severity="medium", expected="Results", actual="Spinner",
                               symptom=schemas.Symptom(kind="failed_request", value="/api/search", explanation="504"))
    with SessionLocal() as db:
        b = db.get(models.Bug, bug["id"])
        b.signature, b.symptom = "failed_request:/api/search", p.symptom.model_dump()
        db.commit()
    assert client.post(f"/bugs/{bug['id']}/dismiss", json={"reason": "cant_reproduce"}).status_code == 200
    bid, new = _draft_bug(session["id"], session["charter_id"], p, "seen", [], "http://x/", None, MockProvider())
    assert (bid, new) == (bug["id"], False)  # the same draft, seen again
    again = client.get(f"/bugs/{bug['id']}").json()
    assert again["dismissal"] is None and again["last_change"] == "Seen again after it was dismissed as can't reproduce: a draft again"
    assert len(again["sightings"]) == 1
    with SessionLocal() as db:
        assert db.query(models.Dismissal).count() == 0 and dismissals.REASONS["cant_reproduce"] == "can't reproduce"


def test_a_filed_bug_cant_be_dismissed(client, session):
    from app import models
    from app.database import SessionLocal
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Filed one"}).json()
    with SessionLocal() as db:
        db.add(models.BugFiling(bug_id=bug["id"], tracker="jira", target="KYC", mode="new", issue_key="KYC-9", issue_url="u"))
        db.commit()
    r = client.post(f"/bugs/{bug['id']}/dismiss", json={"reason": "not_a_problem"})
    assert r.status_code == 409 and "close it there" in r.text
    assert client.post(f"/bugs/{bug['id']}/dismiss", json={"reason": "meh"}).status_code == 422
