"""Deleting bugs, sessions and projects with everything under them, and archiving projects."""
from pathlib import Path

import pytest

from app import models
from app.config import settings
from app.database import SessionLocal
from app.routers.runner import session_folder


@pytest.fixture(autouse=True)
def isolated_files(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "evidence_dir", str(tmp_path / "evidence"))


def add(*rows):
    with SessionLocal() as db:
        db.add_all(rows)
        db.commit()


def count(model, **filters):
    with SessionLocal() as db:
        q = db.query(model)
        for k, v in filters.items():
            q = q.filter(getattr(model, k) == v)
        return q.count()


def full_session(client, charter, tag="A"):
    """A session with notes, two bugs (one with a test, runs and a filing), a debrief, evidence and files."""
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    client.post(f"/sessions/{s['id']}/notes", json={"tag": "test", "content": f"note {tag}"})
    bug = client.post(f"/sessions/{s['id']}/bugs", json={"title": f"Bug {tag}"}).json()
    other = client.post(f"/sessions/{s['id']}/bugs", json={"title": f"Other bug {tag}"}).json()
    add(models.Evidence(session_id=s["id"], bug_id=bug["id"], kind="screenshot", summary="When logged"),
        models.Evidence(session_id=s["id"], kind="navigation", summary="page", url="http://x/"),
        models.RegressionTest(bug_id=bug["id"], filename="t.spec.ts", code="//", plan={}, known_failing=True),
        models.ReproductionRun(bug_id=bug["id"], status="done", base_url="http://x", start_url="http://x/", max_steps=5, runs=1),
        models.IsolationRun(bug_id=bug["id"], status="done", base_url="http://x", symptom={"kind": "text", "value": "x"}),
        models.BugFiling(bug_id=bug["id"], tracker="github", issue_key="#1", issue_url="https://github.com/o/r/issues/1",
                         target="o/r", mode="new"))
    folder = session_folder(s["id"])
    folder.mkdir(parents=True)
    (folder / "trace.zip").write_bytes(b"zip")
    return s, bug, other


# ---------- bugs ----------

def test_deleting_a_bug_takes_its_tests_runs_and_filings_but_keeps_the_screenshot(client, charter):
    s, bug, other = full_session(client, charter)
    r = client.delete(f"/bugs/{bug['id']}")
    assert r.status_code == 200
    assert r.json()["deleted"] == {"regression_tests": 1, "reproduction_runs": 1, "isolation_runs": 1, "bug_filings": 1, "bugs": 1}
    assert client.get(f"/bugs/{bug['id']}").status_code == 404
    assert client.get(f"/bugs/{other['id']}").status_code == 200
    assert count(models.Evidence, session_id=s["id"], kind="screenshot") == 1  # kept, no longer linked
    assert count(models.Evidence, bug_id=bug["id"]) == 0
    assert client.delete(f"/bugs/{bug['id']}").status_code == 404


# ---------- sessions ----------

def test_deleting_a_session_takes_everything_recorded_in_it(client, project, charter):
    s, bug, _ = full_session(client, charter, "A")
    keep, keep_bug, _ = full_session(client, charter, "B")
    client.post(f"/sessions/{s['id']}/end")
    risk = client.post(f"/projects/{project['id']}/risks", json={
        "title": "Coupons stack", "failure_mode": "Two coupons apply", "quality_criterion": "capability",
        "impact": 3, "likelihood": 3}).json()
    add(models.RiskChange(risk_id=risk["id"], session_id=s["id"], source="ai", status="accepted", from_impact=3,
                          from_likelihood=3, to_impact=4, to_likelihood=3, reason="Seen twice"),
        models.RiskChange(risk_id=risk["id"], session_id=s["id"], source="ai", status="proposed", from_impact=3,
                          from_likelihood=3, to_impact=5, to_likelihood=3, reason="Undecided"))

    r = client.delete(f"/sessions/{s['id']}")
    assert r.status_code == 200, r.text
    d = r.json()["deleted"]
    assert (d["sessions"], d["bugs"], d["notes"], d["debriefs"], d["evidence"], d["risk_changes"]) == (1, 2, 1, 1, 2, 1)
    assert client.get(f"/sessions/{s['id']}").status_code == 404
    assert not session_folder(s["id"]).exists()
    assert count(models.RiskChange, risk_id=risk["id"], status="accepted") == 1  # a decision stays in the risk's history
    assert count(models.RiskChange, session_id=s["id"]) == 0
    assert client.get(f"/charters/{charter['id']}").status_code == 200
    # the other session is untouched
    assert client.get(f"/sessions/{keep['id']}").status_code == 200 and session_folder(keep["id"]).exists()
    assert client.get(f"/bugs/{keep_bug['id']}").status_code == 200
    assert count(models.Evidence, session_id=keep["id"]) == 2


def test_a_session_with_its_browser_open_cannot_be_deleted(client, session, monkeypatch):
    monkeypatch.setattr("app.housekeeping.manager.get", lambda sid: object())
    r = client.delete(f"/sessions/{session['id']}")
    assert r.status_code == 409 and "Stop it first" in r.json()["detail"]


def test_deleting_a_session_takes_its_agent_runs_and_comparisons(client, project, charter):
    """Seen on a laptop: a session deleted from its debrief left its exploration run, so the
    charter's "Open the session" led to a session that was gone (a blank page)."""
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    add(models.ExplorationRun(project_id=project["id"], charter_id=charter["id"], session_id=s["id"], status="finished",
                              start_url="http://x/"),
        models.Comparison(session_id=s["id"], env_a={"name": "a", "url": "http://a"}, env_b={"name": "b", "url": "http://b"}))
    assert client.get(f"/charters/{charter['id']}/exploration").json()["session_id"] == s["id"]
    deleted = client.delete(f"/sessions/{s['id']}").json()["deleted"]
    assert (deleted["exploration_runs"], deleted["comparisons"]) == (1, 1)
    assert client.get(f"/charters/{charter['id']}/exploration").json() is None  # "Let Mirmera explore" again
    assert count(models.ExplorationRun) == count(models.Comparison) == 0


def test_a_session_mirmera_is_exploring_in_cannot_be_deleted(client, project, charter, monkeypatch):
    from app.agent import explore
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    add(models.ExplorationRun(id="run-1", project_id=project["id"], charter_id=charter["id"], session_id=s["id"],
                              status="running", start_url="http://x/"))
    monkeypatch.setattr(explore, "ACTIVE", {"run-1"})
    r = client.delete(f"/sessions/{s['id']}")
    assert r.status_code == 409 and r.json()["detail"].startswith("Mirmera is exploring in this session")


def test_leftovers_from_an_older_mirmera_are_removed_on_start(client, project, charter):
    """A database where a session was deleted before this fix: its run still points at it."""
    from app.database import engine, remove_orphans
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    kept = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    add(models.ExplorationRun(project_id=project["id"], charter_id=charter["id"], session_id=s["id"], start_url="http://x/"),
        models.ExplorationRun(project_id=project["id"], charter_id=charter["id"], session_id=kept["id"], start_url="http://x/"))
    with engine.begin() as conn:  # the old delete: the session went, its run stayed
        for table in ("notes", "sessions"):
            conn.exec_driver_sql(f"DELETE FROM {table} WHERE {'session_id' if table == 'notes' else 'id'} = ?", (s["id"],))
    assert remove_orphans(engine) == ["exploration_runs: 1"]
    assert remove_orphans(engine) == []  # nothing left to do
    assert count(models.ExplorationRun, session_id=kept["id"]) == 1


def test_a_bug_the_agent_is_working_on_cannot_be_deleted(client, session, monkeypatch):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Busy"}).json()
    run = models.ReproductionRun(id="busy-run", bug_id=bug["id"], status="running", base_url="http://x", start_url="http://x/", max_steps=5, runs=1)
    add(run)
    from app.agent import reproduce as agent
    monkeypatch.setattr(agent, "ACTIVE", {"busy-run"})
    for url in (f"/bugs/{bug['id']}", f"/sessions/{session['id']}"):
        r = client.delete(url)
        assert r.status_code == 409 and "reproduction agent" in r.json()["detail"]


# ---------- projects ----------

def test_deleting_a_project_needs_its_name_and_takes_everything(client, project, charter):
    s, _, _ = full_session(client, charter)
    risk = client.post(f"/projects/{project['id']}/risks", json={
        "title": "Coupons stack", "failure_mode": "Two coupons apply", "quality_criterion": "capability",
        "impact": 3, "likelihood": 3}).json()
    client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore coupons", "risk_ids": [risk["id"]]})
    client.post(f"/projects/{project['id']}/artifacts", json={"type": "spec", "label": "spec.md", "raw_content": "Coupons"})
    other = client.post("/projects", json={"name": "Keep me"}).json()
    other_charter = client.post(f"/projects/{other['id']}/charters", json={"mission_statement": "Explore other"}).json()

    assert client.delete(f"/projects/{project['id']}").status_code == 422
    assert client.delete(f"/projects/{project['id']}", params={"confirm": "shopflow"}).status_code == 422
    r = client.delete(f"/projects/{project['id']}", params={"confirm": "ShopFlow"})
    assert r.status_code == 200, r.text
    d = r.json()["deleted"]
    assert (d["sessions"], d["bugs"], d["charters"], d["risks"], d["artifacts"]) == (1, 2, 2, 1, 1)
    assert client.get(f"/projects/{project['id']}").status_code == 404
    assert not session_folder(s["id"]).exists()
    with SessionLocal() as db:
        assert db.execute(models.charter_risks.select()).fetchall() == []
        for model in (models.Session, models.Bug, models.Note, models.Evidence, models.RegressionTest, models.BugFiling):
            assert db.query(model).count() == 0, model.__tablename__
    assert [p["name"] for p in client.get("/projects").json()] == ["Keep me"]
    assert client.get(f"/charters/{other_charter['id']}").status_code == 200


# ---------- archiving ----------

def test_archiving_hides_nothing_on_the_server_and_can_be_undone(client, project, charter):
    r = client.post(f"/projects/{project['id']}/archive")
    assert r.status_code == 200 and r.json()["archived_at"]
    listed = client.get("/projects").json()[0]
    assert listed["archived_at"] and listed["charter_count"] == 1  # the page decides what to show
    assert client.get(f"/projects/{project['id']}/charters").json()[0]["id"] == charter["id"]  # still usable
    assert client.post(f"/projects/{project['id']}/unarchive").json()["archived_at"] is None
