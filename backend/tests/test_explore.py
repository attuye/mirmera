"""Exploration agent with a real headless Chromium, against the planted-bug app from
test_regression ("Passport" makes POST /api/verify fail): it explores a charter, leaves a
session (notes, recording, signals) and drafts bugs for a tester to review."""
import time

import pytest

from app import models
from app.database import SessionLocal
from tests.test_regression import App, app_url, bug_present  # noqa: F401  (fixtures)
from tests.test_reproduce import Scripted, step
from tests.test_runner import chromium_available, headless_runner  # noqa: F401

VERIFY = {"method": "role", "role": "button", "name": "Verify"}


@pytest.fixture
def kyc(client, app_url):  # noqa: F811
    project = client.post("/projects", json={"name": "KYC portal"}).json()
    client.patch(f"/projects/{project['id']}", json={"app_url": app_url})
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore identity verification with each document type to discover what fails",
        "test_ideas": ["Try a passport", "Try an Amharic name"]}).json()
    return project, charter


def finished(client, run, timeout=120.0):
    deadline = time.monotonic() + timeout
    while True:
        r = client.get(f"/exploration-runs/{run['id']}").json()
        if r["status"] != "running":
            return r
        if time.monotonic() > deadline:
            raise AssertionError(f"Still running after {timeout}s: {r['steps'][-3:]}")
        time.sleep(0.3)


def scripted(monkeypatch, steps):
    provider = Scripted(steps)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    return provider


def test_it_explores_and_drafts_a_checked_bug(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    project, charter = kyc
    provider = scripted(monkeypatch, [
        step("Start with the passport idea", kind="note", note="Trying a passport first."),
        step("Pick passport", kind="select", locator={"method": "label", "value": "Document type"}, value="Passport"),
        step("Submit", kind="click", locator=VERIFY),
        step("A claim Mirmera can't see", kind="problem", problem={
            "title": "Says verified when it isn't", "expected": "x" * 5, "actual": "y" * 5,
            "symptom": {"kind": "text_visible", "value": "Your account is closed", "explanation": "made up"}}),
        step("The verify call failed", kind="problem", problem={
            "title": "Passport verification fails", "expected": "Passport holders are verified like ID holders",
            "actual": "Something went wrong is shown and POST /api/verify answers 500", "severity": "high",
            "symptom": {"kind": "failed_request", "value": "/api/verify", "explanation": "the request is listed"}}),
        step("Same again", kind="problem", problem={
            "title": "Passport fails again", "expected": "x" * 5, "actual": "y" * 5,
            "symptom": {"kind": "failed_request", "value": "/api/verify", "explanation": "again"}}),
        step("Leave the app", kind="goto", url="https://example.com/"),
        step("Covered", kind="done", summary="Passport fails; national ID not tried yet."),
    ])
    r = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 10})
    assert r.status_code == 200, r.text
    run = finished(client, r.json())
    assert run["status"] == "finished", run["detail"]
    assert run["summary"] == "Passport fails; national ID not tried yet."
    statuses = [s["status"] for s in run["steps"]]
    assert statuses == ["ok", "ok", "ok", "not verified", "drafted", "refused", "refused", "ok"]
    assert "<charter>" in provider.users[0] and "Try a passport" in provider.users[0]

    [bug_id] = run["bug_ids"]
    bug = client.get(f"/sessions/{run['session_id']}/bugs").json()[0]
    assert bug["id"] == bug_id and bug["kind"] is None  # a draft: the tester reviews it
    assert bug["severity"] == "high" and bug["title"] == "Passport verification fails"
    steps = client.get(f"/bugs/{bug_id}/steps").json()
    assert steps["source"] == "edited"
    assert [s["type"] for s in steps["steps"]] == ["goto", "action", "action"]
    with SessionLocal() as db:
        session = db.get(models.Session, run["session_id"])
        assert session.tester_id == "Mirmera agent" and session.ended_at is None and session.paused_at is not None
        notes = [n.content for n in session.notes]
        assert "Trying a passport first." in notes and any(n.startswith("Mirmera agent:") for n in notes)
        kinds = {e.kind for e in session.evidence}
        assert {"navigation", "action", "failed_request", "screenshot"} <= kinds
        assert db.query(models.BugFiling).count() == 0  # nothing reaches a tracker on its own

    # A tester reviews the paused session and ends it: the debrief is drafted from the agent's notes,
    # and the time the session sat paused doesn't count as testing.
    debrief = client.post(f"/sessions/{run['session_id']}/end").json()
    assert debrief["session_id"] == run["session_id"]
    with SessionLocal() as db:
        session = db.get(models.Session, run["session_id"])
        assert session.paused_at is None and session.total_paused_seconds >= 0


def test_it_needs_an_app_url_and_runs_once_at_a_time(client, monkeypatch, chromium_available):  # noqa: F811
    project = client.post("/projects", json={"name": "No URL"}).json()
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore it"}).json()
    r = client.post(f"/charters/{charter['id']}/explore", json={})
    assert r.status_code == 409 and "no app URL" in r.json()["detail"]


def test_the_mock_explorer_runs_end_to_end(client, kyc, chromium_available):  # noqa: F811
    project, charter = kyc
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={}).json())
    assert run["status"] == "finished", run["detail"]
    assert [s["kind"] for s in run["steps"]][:3] == ["note", "fill", "click"]
    assert client.get(f"/charters/{charter['id']}/exploration").json()["id"] == run["id"]
    assert client.post(f"/exploration-runs/{run['id']}/cancel").status_code == 409  # already finished
