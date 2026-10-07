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
from tests.conftest import end_and_draft

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
    debrief = end_and_draft(client, run['session_id'])
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


EXPIRY = {"method": "label", "value": "Expiry date"}


def test_it_sees_what_the_form_holds_and_what_the_app_answers(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Small models lose track of which fields are filled: each step shows every field's value
    (empty ones marked, passwords never), the page's messages, and what a submit answered."""
    project, charter = kyc
    monkeypatch.setenv("MIRMERA_SECRET_KYC_PASSWORD", "hunter2-not-shown")
    provider = scripted(monkeypatch, [
        step("A valid name first", kind="fill", locator={"method": "label", "value": "Full name"}, value="John Smith"),
        step("The password", kind="fill_secret", locator={"method": "label", "value": "Password"}, secret="MIRMERA_SECRET_KYC_PASSWORD"),
        step("Submit", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="A valid submit is verified."),
    ])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 6}).json())
    first, after_fill, after_secret, after_submit = provider.users
    assert '"Full name" (text): (empty)' in first and "Messages on the page: (none)" in first
    assert '"Full name" (text): "John Smith"' in after_fill
    assert '"Expiry date" (date): (empty)' in after_fill
    assert '"I accept the terms" (checkbox): not checked' in after_fill
    assert '"Document type" (select-one): "National ID"' in after_fill
    assert '(password): (filled)' in after_secret
    assert all("hunter2-not-shown" not in u for u in provider.users)
    submit = run["steps"][2]
    assert submit["status"] == "ok" and submit["detail"] == 'the page now says "Verified"'
    assert 'Messages on the page: "Verified"' in after_submit
    assert '3. click button "Verify" -> ok: the page now says "Verified"' in after_submit  # in the history


def test_it_does_not_repeat_steps_that_teach_nothing(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b: it submitted the same form four times, then filled the date it already had."""
    project, charter = kyc
    scripted(monkeypatch, [
        step("An expired date", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("Submit again", kind="click", locator=VERIFY),
        step("Set the same date", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("A future date instead", kind="fill", locator=EXPIRY, value="2030-01-01"),
        step("Submit with the new date", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="Both dates are verified."),
    ])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 10}).json())
    assert [s["status"] for s in run["steps"]] == ["ok", "ok", "refused", "refused", "ok", "ok", "ok"]
    again, same_value = run["steps"][2], run["steps"][3]
    assert "you did exactly this at step 2" in again["detail"]
    assert 'already holds "2020-01-01"' in same_value["detail"]
    with SessionLocal() as db:  # refused steps are not part of the session's recording
        actions = [e for e in db.query(models.Evidence).filter_by(session_id=run["session_id"]).all() if e.kind == "action"]
        assert len(actions) == 4


def test_a_tester_can_watch_the_agent_and_replay_each_step(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Like watching a colleague's screen: the agent's browser live while it works, then each
    step's picture, with a box where it was about to act."""
    import asyncio

    from app.config import settings
    project, charter = kyc
    seen = {}
    provider = scripted(monkeypatch, [
        step("A name", kind="fill", locator={"method": "label", "value": "Full name"}, value="John Smith"),
        step("Submit", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="Watched."),
    ])
    plain = provider.complete

    async def watched(system, user, **kwargs):
        if len(provider.users) == 1:  # while it works on its second step
            def meanwhile():
                runs = client.get(f"/charters/{charter['id']}/exploration").json()
                live = client.get(f"/exploration-runs/{runs['id']}/frame")
                seq = int(live.headers["X-Frame-Seq"])
                newest = client.get(f"/exploration-runs/{runs['id']}/frame", params={"after": 10**9})  # nothing newer
                origin = settings.cors_origins.split(",")[0].strip()  # the frontend reads the headers cross-origin
                cors = client.get(f"/exploration-runs/{runs['id']}/frame", headers={"Origin": origin})
                seen.update(live=live, seq=seq, again=newest, exposed=cors.headers.get("access-control-expose-headers", ""))
            await asyncio.to_thread(meanwhile)
        return await plain(system, user, **kwargs)
    provider.complete = watched

    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json())
    live = seen["live"]
    assert live.status_code == 200 and live.headers["content-type"] == "image/jpeg" and live.content[:2] == b"\xff\xd8"
    assert live.headers["X-Frame-Url"].startswith("http://127.0.0.1")
    assert "X-Frame-Seq" in seen["exposed"] and "X-Frame-Url" in seen["exposed"]
    assert seen["seq"] >= 1 and seen["again"].status_code == 204  # nothing newer to send: cheap to ask often
    stopped = client.get(f"/exploration-runs/{run['id']}/frame")  # stopped: its steps' pictures show what it did
    assert stopped.status_code == 204 and stopped.headers["X-Frame-Live"] == "0"

    fill, click, done = run["steps"]
    assert fill["frame"] and click["frame"] and "frame" not in done
    x, y, w, h = click["box"]  # where it was about to click, on a 1280×800 page
    assert 0 <= x < 1280 and 0 <= y < 800 and w > 10 and h > 5
    picture = client.get(f"/exploration-runs/{run['id']}/steps/2/frame")
    assert picture.status_code == 200 and picture.content[:2] == b"\xff\xd8"
    assert client.get(f"/exploration-runs/{run['id']}/steps/3/frame").status_code == 404
