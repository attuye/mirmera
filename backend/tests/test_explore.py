"""Exploration agent with a real headless Chromium, against the planted-bug app from
test_regression ("Passport" makes POST /api/verify fail): it explores a charter, leaves a
session (notes, recording, signals) and drafts bugs for a tester to review."""
import json
import re
import time

import pytest

from app import agent_activity, models, schemas
from app.database import SessionLocal
from app.routers import runner as runner_api
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
    # The 500 after the submit is drafted by Mirmera itself; the agent's own report of it is then a repeat.
    # Three steps in a row that taught nothing: Mirmera steps in with the charter's next idea, then the agent goes on.
    assert statuses == ["ok", "ok", "drafted", "not verified", "refused", "refused", "ok", "refused", "ok"]
    assert run["steps"][6]["by"] == "Mirmera" and run["steps"][6]["action"] == 'try Document type = "National ID", then submit'
    assert "Verified" in run["steps"][6]["detail"]
    assert "<charter>" in provider.users[0] and "Try a passport" in provider.users[0]

    [bug_id] = run["bug_ids"]
    bug = client.get(f"/sessions/{run['session_id']}/bugs").json()[0]
    assert bug["id"] == bug_id and bug["kind"] is None  # a draft: the tester reviews it
    assert bug["severity"] == "high" and bug["title"] == "POST /api/verify fails with 500"
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


def test_a_refused_step_costs_none_of_the_budget(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with small local models: 12 of 25 steps refused, so half the run tried nothing."""
    project, charter = kyc
    provider = scripted(monkeypatch, [
        step("An expired date", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("Submit again", kind="click", locator=VERIFY),
        step("Set the same date", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("A future date instead", kind="fill", locator=EXPIRY, value="2030-01-01"),
    ])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 3}).json())
    assert [s["status"] for s in run["steps"]] == ["ok", "ok", "refused", "refused", "ok"]
    assert run["detail"] == "Used all 3 steps."
    # no summary from the agent: Mirmera writes one from the facts, for the run and the session
    assert run["summary"].startswith("Written by Mirmera: the agent didn't finish with a summary (used all 3 steps). "
                                     "It ran 3 actions, 1 of them sending a form, on /.")
    assert "No bug drafts." in run["summary"]
    notes = [n["content"] for n in client.get(f"/sessions/{run['session_id']}/notes").json()]
    assert any(n.startswith("Mirmera agent: Written by Mirmera:") for n in notes)
    assert [re.search(r"Steps left: (\d+)\.", u)[1] for u in provider.users] == ["3", "2", "1", "1", "1"]
    with SessionLocal() as db:  # what the progress bar shows: 3 of 3, not 5 of 3
        ex = agent_activity.exploration(db.get(models.ExplorationRun, run["id"]), db)
        assert ex["steps_used"] == 3 and ex["max_steps"] == 3


def test_a_model_that_is_only_ever_refused_still_stops(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    project, charter = kyc
    monkeypatch.setattr("app.agent.explore.suggest", lambda *a, **k: None)  # no idea for Mirmera to step in with
    scripted(monkeypatch, [step("Look first", kind="note", note="The form has an expiry date field.")])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 3}).json())
    assert [s["status"] for s in run["steps"]] == ["ok"] + ["refused"] * 5
    assert run["status"] == "finished" and run["detail"].startswith("Stopped after 6 replies from the AI, 5 of them refused")


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


def test_the_run_is_filmed_and_each_step_knows_where_it_is_in_the_film(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Watch a run like a video: the recording plays from any step (the player asks for byte ranges)."""
    project, charter = kyc
    scripted(monkeypatch, [
        step("A name", kind="fill", locator={"method": "label", "value": "Full name"}, value="John Smith"),
        step("Submit", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="Filmed."),
    ])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json())
    assert run["video"] and run["video"].endswith(".webm"), run["detail"]
    times = [s["t"] for s in run["steps"]]
    assert times == sorted(times) and times[0] >= 0  # in the order they happened

    whole = client.get(f"/exploration-runs/{run['id']}/video")
    assert whole.status_code == 200 and whole.headers["content-type"] == "video/webm"
    assert whole.content[:4] == b"\x1a\x45\xdf\xa3"  # a WebM (Matroska) file
    size = len(whole.content)
    part = client.get(f"/exploration-runs/{run['id']}/video", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206 and part.content == whole.content[10:20]
    assert part.headers["content-range"] == f"bytes 10-19/{size}"
    tail = client.get(f"/exploration-runs/{run['id']}/video", headers={"Range": "bytes=-5"})
    assert tail.status_code == 206 and tail.content == whole.content[-5:]
    assert client.get(f"/exploration-runs/{run['id']}/video", headers={"Range": f"bytes={size}-"}).status_code == 416
    leftovers = [p.name for p in (runner_api.session_folder(run["session_id"])).iterdir() if p.is_dir()]
    assert leftovers == []  # the recording's temporary folder is gone


def test_each_answer_is_checked_against_the_story_and_a_broken_rule_becomes_a_bug(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b: it submitted an expired date, the app said "Verified", and it moved on.
    Mirmera now asks a separate, focused question after each submit: does this answer break one
    of the story's acceptance criteria? If so, a checked bug draft."""
    from app.ai.mock_provider import MockProvider
    project, charter = kyc
    story = client.post(f"/projects/{project['id']}/stories", json={
        "key": "SCRUM-11", "title": "Sender verifies identity",
        "description": "As a UK sender I want to verify my identity.\n\nAcceptance criteria\n"
                       "- All three document types are verified the same way\n"
                       "- An expired document is refused, with its expiry date shown\n"}).json()
    client.put(f"/stories/{story['id']}/charters", json={"ids": [charter["id"]]})

    class Steering(Scripted):  # the scripted steps steer; the mock answers the checking question
        async def complete(self, system, user, **kwargs):
            if kwargs.get("task") == "explore_judge":
                self.judged = getattr(self, "judged", []) + [user]
                return await MockProvider().complete(system, user, **kwargs)
            return await super().complete(system, user, **kwargs)

    provider = Steering([
        step("Valid first", kind="fill", locator=EXPIRY, value="2030-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("Expired", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="Tried a future and a past date."),
    ])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 8}).json())

    assert "AC2: An expired document is refused, with its expiry date shown" in provider.users[0]  # the agent sees the rules
    valid, expired = provider.judged
    assert "Expiry date = 2030-01-01" in valid and "Says: Verified" in valid
    assert "Expiry date = 2020-01-01" in expired and "AC2: An expired document is refused" in expired
    steps = run["steps"]
    assert (steps[1]["judged"], steps[1]["status"]) == ("consistent", "ok")
    assert steps[3]["judged"] == "breaks" and steps[3]["status"] == "drafted"
    assert steps[3]["detail"].startswith('the page still says "Verified"')  # same answer as the valid date

    [bug_id] = run["bug_ids"]
    bug = client.get(f"/sessions/{run['session_id']}/bugs").json()[0]
    assert bug["id"] == bug_id and bug["kind"] is None and bug["severity"] == "high"
    assert bug["title"] == "An expiry date in the past is accepted although AC2 says it is refused"  # Mirmera reads expiry rules itself
    assert "2020-01-01" in bug["actual"]
    assert "Breaks AC2: An expired document is refused" in bug["description"]
    notes = [n["content"] for n in client.get(f"/sessions/{run['session_id']}/notes").json()]
    assert any(n.startswith("Sent:") and "2020-01-01" in n and "Breaks" in n for n in notes)  # coverage, in the tester's words


def test_acceptance_criteria_are_read_from_the_story():
    from app.agent.explore import criteria_from
    story = ("As a UK sender, I want to verify my identity.\n\nAcceptance criteria\n"
             "The document type can be National ID, Passport or Driver's licence.\n\n"
             "All three types are verified the same way; none is second-class.\n"
             "* Many of our senders hold only a passport: verifying with a passport must work first time.\n\n"
             "Notes:\nnot a criterion\n")
    assert criteria_from(story) == [
        "The document type can be National ID, Passport or Driver's licence.",
        "All three types are verified the same way; none is second-class.",
        "Many of our senders hold only a passport: verifying with a passport must work first time."]
    assert criteria_from("Acceptance criteria: an expired ID is refused") == ["an expired ID is refused"]
    assert criteria_from("Intro\n- one rule here\n- another rule") == ["one rule here", "another rule"]
    assert criteria_from(None) == []



class Reading(Scripted):
    """Scripted steps steer the browser; the checking question gets the given facts in turn
    (or the mock's reading when they run out)."""

    def __init__(self, steps, facts=()):
        super().__init__(steps)
        self.facts, self.judged = list(facts), []

    async def complete(self, system, user, **kwargs):
        if kwargs.get("task") == "explore_judge":
            self.judged.append(user)
            if self.facts:
                return json.dumps(self.facts.pop(0))
            from app.ai.mock_provider import MockProvider
            return await MockProvider().complete(system, user, **kwargs)
        return await super().complete(system, user, **kwargs)


def story_for(client, project, charter, criteria):
    story = client.post(f"/projects/{project['id']}/stories", json={
        "key": "SCRUM-11", "title": "Sender verifies identity",
        "description": "Acceptance criteria\n" + "\n".join(f"- {c}" for c in criteria)}).json()
    client.put(f"/stories/{story['id']}/charters", json={"ids": [charter["id"]]})


def test_the_ai_reports_facts_and_mirmera_decides_so_a_misreading_drafts_no_bug(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """The three drafts qwen3:8b made on SCRUM-11, replayed. Only the real one is a bug now:
    a refusal that the rule expects agrees with it, and a quote that isn't in the answer counts for nothing."""
    project, charter = kyc
    story_for(client, project, charter, ["An expired document is refused, with its expiry date shown",
                                          "The sender must accept the terms; without it, verification is refused with a reason"])
    name = {"method": "label", "value": "Full name"}
    terms = {"method": "label", "value": "I accept the terms"}
    provider = Reading([
        step("Expired, terms not ticked", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("A name", kind="fill", locator=name, value="John Smith"),
        step("Submit", kind="click", locator=VERIFY),
        step("Tick the terms", kind="check", locator=terms),
        step("Submit", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="Done."),
    ], facts=[
        # 1. qwen: "not refused, it asked to accept the terms" — but it did refuse, as AC2 expects
        {"answer_means": "refused", "answer_quote": "Verified", "rule": "AC2", "rule_expects": "refuse", "because": "terms"},
        # 2. qwen copied an example: "yet the document was verified" — not what the app said
        {"answer_means": "accepted", "answer_quote": "the document was verified", "rule": "AC1", "rule_expects": "refuse",
         "because": "The expiry date 2020-01-01 is in the past, yet the document was verified"},
        # 3. the real one: expired, everything else filled, "Verified"
        {"answer_means": "accepted", "answer_quote": "Verified", "rule": "AC1", "rule_expects": "refuse",
         "because": "2020-01-01 is in the past", "title": "Expired document is accepted"},
    ])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 10}).json())
    judged = [s.get("judged") for s in run["steps"] if s.get("judged")]
    # The first submit already sent an expired date and got "Verified". The AI misread it (note 1), but
    # AC1 names expired documents, so Mirmera checks that itself and drafts it there, once. The form
    # keeps the expired date, so the later submits break AC1 too (Mirmera's own reading, whatever the
    # AI quoted); they aren't drafted again.
    assert judged == ["breaks", "breaks", "breaks"]
    assert run["steps"][1]["status"] == "drafted" and run["steps"][5]["status"] == "ok"  # the same rule: not drafted twice
    [bug] = client.get(f"/sessions/{run['session_id']}/bugs").json()
    assert bug["title"] == "An expiry date in the past is accepted although AC1 says it is refused" and bug["severity"] == "high"
    assert 'Per AC1 ("An expired document is refused' in bug["expected"] and "2020-01-01" in bug["actual"]
    notes = [n["content"] for n in client.get(f"/sessions/{run['session_id']}/notes").json() if n["content"].startswith("Sent:")]
    assert "As AC2 expects." in notes[0]
    assert "Breaks AC1" in notes[1] and "Mirmera's own check" in notes[1]  # not the AI's misquote
    assert "Breaks AC1" in notes[2]


def test_any_answer_counts_a_line_under_the_form_or_a_new_page(client, kyc, app_url, monkeypatch, chromium_available):  # noqa: F811
    """Many apps answer without a status area: a red line under a field, or the next page."""
    project, charter = kyc
    story_for(client, project, charter, ["An expired document is refused, with its expiry date shown"])
    name = {"method": "label", "value": "Full name"}
    provider = Reading([
        step("The plain form", kind="goto", url=f"{app_url}/plain"),
        step("Expired, no name", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("A name", kind="fill", locator=name, value="John Smith"),
        step("Submit", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="Done."),
    ])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 10}).json())
    refused, accepted = [s for s in run["steps"] if s.get("judged")]
    assert refused["detail"].startswith('the page now says "Full name is required"')
    assert refused["judged"] == "consistent"  # an expired date refused (for a missing name): no bug
    assert accepted["detail"].startswith('the page now says "You\'re verified"')
    assert accepted["judged"] == "breaks"
    assert "Went to: " in provider.judged[1] and "/welcome" in provider.judged[1]
    [bug] = client.get(f"/sessions/{run['session_id']}/bugs").json()
    assert "the app answered" in bug["actual"]


def test_how_mirmera_decides_from_the_facts():
    from app.agent.explore import FormState, decide
    rules = {"AC1": "An expired document is refused", "R2": "Ge'ez names are accepted"}
    full = FormState(fields=[{"label": "Full name", "type": "text", "value": "አበበ", "required": False},
                             {"label": "Terms", "type": "checkbox", "value": "checked", "required": False}])
    gap = FormState(fields=[{"label": "Full name", "type": "text", "value": "", "required": False}])

    def facts(**kw):
        base = {"answer_means": "accepted", "answer_quote": "Verified", "rule": "AC1", "rule_expects": "refuse"}
        return schemas.LLMExploreJudgement(**(base | kw))
    said = ["Verified. Welcome!"]
    assert decide(facts(), rules, full, said)[0] == "breaks"
    assert decide(facts(rule_expects="accept"), rules, full, said)[0] == "consistent"
    assert decide(facts(answer_quote="yet it was verified"), rules, full, said)[0] == "unclear"  # not the app's words
    assert decide(facts(rule="AC9"), rules, full, said)[0] == "unclear"  # no such rule
    assert decide(facts(rule_expects="none"), rules, full, said)[0] == "unclear"
    refused = ["Name must use letters only."]
    r = facts(answer_means="refused", answer_quote="Name must use letters only", rule="R2", rule_expects="accept")
    assert decide(r, rules, full, refused)[0] == "breaks"  # a Ge'ez name refused, everything filled
    assert decide(r, rules, gap, refused)[0] == "unclear"  # a field was empty: may be the reason
    assert decide(facts(answer_means="other"), rules, full, said)[0] == "unclear"



def test_a_mixed_up_locator_is_read_as_meant(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b: {"method": "label", "name": "Document number"} three times running, which
    stopped the run ("unusable reply"). It means the field labelled Document number."""
    project, charter = kyc
    scripted(monkeypatch, [
        step("A name", kind="fill", locator={"method": "label", "name": "Full name"}, value="John Smith"),
        step("Submit", kind="click", locator={"method": "Role", "role": "Button", "value": "Verify"}),
        step("Covered", kind="done", summary="Done."),
    ])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json())
    assert run["status"] == "finished", run["detail"]
    assert [s["status"] for s in run["steps"]] == ["ok", "ok", "ok"]
    assert run["steps"][0]["action"] == 'fill label "Full name" = "John Smith"'
    assert run["steps"][1]["action"] == 'click button "Verify"'


def test_locators_that_still_cant_be_read_are_refused():
    import pytest as _pytest
    from pydantic import ValidationError
    with _pytest.raises(ValidationError):
        schemas.PlanLocator.model_validate({"method": "label"})
    with _pytest.raises(ValidationError):
        schemas.PlanLocator.model_validate({"method": "role", "role": "marquee", "name": "x"})


def test_a_server_error_after_a_step_is_a_bug_without_asking_the_ai(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b on SCRUM-11: Passport → "Something went wrong" and POST /api/verify 500.
    It noted the 500 three times and drafted nothing, and the check couldn't match its quote.
    A crash needs no rule and no AI: Mirmera drafts it, once, and refuses a repeated note."""
    project, charter = kyc
    story_for(client, project, charter, ["Verifying with a passport must work first time"])
    passport = step("Passport", kind="select", locator={"method": "label", "value": "Document type"}, value="Passport")
    provider = Reading([
        passport,
        step("Submit", kind="click", locator=VERIFY),
        step("Note it", kind="note", note="Passport gives a 500."),
        step("Note it again", kind="note", note="Passport gives a 500. "),
        step("Covered", kind="done", summary="Passport fails."),
    ], facts=[{"answer_means": "error", "answer_quote": "Something went wrong", "rule": "AC1", "rule_expects": "accept"}])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 8}).json())
    submit = run["steps"][1]
    assert submit["status"] == "drafted" and "POST /api/verify fails with 500" in submit["detail"]
    assert submit["judged"] == "breaks"  # the check agrees, but the crash is the one draft
    assert [s["status"] for s in run["steps"][2:4]] == ["ok", "refused"]
    [bug] = client.get(f"/sessions/{run['session_id']}/bugs").json()
    assert bug["title"] == "POST /api/verify fails with 500" and bug["severity"] == "high"
    assert bug["actual"].startswith('After click button "Verify" (sent: ') and "Document type = Passport" in bug["actual"]
    assert 'The page said: "Something went wrong"' in bug["actual"]


def test_a_nearly_word_for_word_quote_counts():
    from app.agent.explore import quoted
    said = ["Document number and expiry date are required."]
    assert quoted("Document number and expiry date are required", said)  # no full stop
    assert quoted("document number and expiry date required", said)  # a word dropped
    assert not quoted("The document was verified", said)  # made up
    assert not quoted("", said)


def test_a_tester_sends_hints_while_it_runs_only(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    from fastapi.testclient import TestClient
    from app.main import app
    from tests.conftest import sign_up
    project, charter = kyc
    scripted(monkeypatch, [step("Look", kind="wait")])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Scripted([step("Look", kind="wait")], delay=0.3))
    run = client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 6}).json()
    r = client.post(f"/exploration-runs/{run['id']}/hints", json={"text": "Try a Passport"})
    assert r.status_code == 200 and r.json()["hints"][0] | {"at": None} == {
        "id": r.json()["hints"][0]["id"], "text": "Try a Passport", "by": "Attu", "at": None, "step": None}
    with TestClient(app) as sara:
        sign_up(sara, name="Sara", email="sara@x.et", workspace="Sara's")
        assert sara.post(f"/exploration-runs/{run['id']}/hints", json={"text": "x" * 5}).status_code == 404
    done = finished(client, run)
    assert done["hints"][0]["step"] is not None  # the agent read it
    assert client.post(f"/exploration-runs/{run['id']}/hints", json={"text": "Too late"}).status_code == 409
    assert client.post(f"/exploration-runs/{run['id']}/hints", json={"text": ""}).status_code == 422


def test_tricky_files_are_what_they_say(tmp_path, monkeypatch):
    from app.agent import files
    monkeypatch.setattr(files.settings, "evidence_dir", str(tmp_path))
    read = lambda n: files.path_of(n).read_bytes()  # noqa: E731
    assert read("id-photo.jpg")[:3] == b"\xff\xd8\xff" and read("sideways.jpg")[2:4] == b"\xff\xe1"
    assert read("empty.jpg") == b"" and read("program.jpg")[:2] == b"MZ" and read("id-scan.pdf")[:5] == b"%PDF-"
    assert 24 < files.path_of("big-photo.png").stat().st_size / 1_048_576 < 26
    assert files.path_of("nothing.png") is None and files.name_of("@file:tiny.png") == "tiny.png"
    assert [v for _, v in files.ideas()][:2] == ["@file:empty.jpg", "@file:big-photo.png"]
    assert files.ideas()[-1][1] == "@file:id-scan.pdf"


def test_a_file_size_in_a_rule_is_not_a_number_range():
    from app.agent.explore import boundary_ideas, file_cases, field_rules
    rule = {"AC1": "The ID photo must be a JPG or PNG, up to 5 MB."}
    photo = {"label": "ID photo", "type": "file", "value": "id-photo.jpg"}
    assert field_rules(photo, rule) == [] and boundary_ideas(photo, rule) == []
    assert file_cases(rule, photo) == []  # the ordinary photo keeps the rule
    assert [c[4] for c in file_cases(rule, {**photo, "value": "phone-photo.heic"})] == ["a .heic file, not JPG or PNG"]
    assert [c[4] for c in file_cases(rule, {**photo, "value": "tiny.png"})] == []


def test_requirements_are_read_whole():
    """Seen on the practice spec: each requirement was cut at its first line, so AC2 lost
    "Ge'ez" and AC3 lost what "expired" means."""
    from app.agent.explore import criteria_from
    spec = ("# Identity verification (v1.4)\n\n## Requirements\n"
            "1. Customers can verify with a **National ID, a Passport or a Driver's licence**.\n"
            "   All three are supported equally.\n"
            "2. The customer's **full name is entered as written on the document**, in\n"
            "   Latin or Ge'ez script.\n"
            "3. **Expired documents must be rejected** (KYC directive). A document is expired if its\n"
            "   expiry date is before today.\n\n"
            "## Out of scope for v1.4\n- Selfie and liveness checks; document photo upload.\n\n"
            "<!-- 1. An old requirement\n- another -->\n")
    assert criteria_from(spec) == [
        "Customers can verify with a National ID, a Passport or a Driver's licence. All three are supported equally.",
        "The customer's full name is entered as written on the document, in Latin or Ge'ez script.",
        "Expired documents must be rejected (KYC directive). A document is expired if its expiry date is before today.",
    ]
    # an "Acceptance criteria" section still wins, and its wrapped bullets are joined too
    assert criteria_from("Story\n\nAcceptance criteria\n- Expired documents are\n  refused\n- Phone is optional\n\nNotes:\n- x") == [
        "Expired documents are refused", "Phone is optional"]


def test_mirmera_decides_the_rules_it_reads_itself():
    from datetime import date
    from app.agent.explore import FormState, own_verdict
    rules = {"AC2": "The full name is entered as written on the document, in Latin or Ge'ez script.",
             "AC3": "Expired documents must be rejected. A document is expired if its expiry date is before today.",
             "AC5": "The customer must accept the terms."}
    today = date(2026, 11, 19)

    def sent(**values):
        return FormState(url="http://app/verify", fields=[{"label": k.replace("_", " "), "type": "text", "value": v}
                                                          for k, v in values.items()])
    # the AI called a future expiry date "expired": Mirmera's own check says it isn't
    assert own_verdict("AC3", rules, sent(Expiry_date="2030-01-01"), ["Verified."], "accepted", today)[0] == "consistent"
    assert own_verdict("AC3", rules, sent(Expiry_date="2026-11-19"), ["Verified."], "accepted", today)[0] == "consistent"  # today isn't before today
    assert own_verdict("AC3", rules, sent(Expiry_date="2020-01-01"), ["Verified."], "accepted", today)[0] == "breaks"
    assert own_verdict("AC2", rules, sent(Full_name="አበበ ከበደ"), ["Name must use letters only."], "refused", today)[0] == "breaks"
    assert own_verdict("AC5", rules, sent(Expiry_date="2020-01-01"), ["Verified."], "accepted", today) is None  # not one it reads


def test_a_wrong_ai_verdict_neither_drafts_a_bug_nor_hides_the_real_one(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b on the practice app: a valid, future expiry date was judged "expired"
    and drafted; the rule was then reported, so the real one (2020-01-01, "Verified") was skipped."""
    project, charter = kyc
    story_for(client, project, charter, ["Expired documents must be rejected. A document is expired if its expiry "
                                          "date is before today."])
    wrong = {"answer_means": "accepted", "answer_quote": "Verified", "rule": "AC1", "rule_expects": "refuse",
             "because": "the expiry date is in the future", "title": "Accepted although expiry date is in the future"}
    provider = Reading([
        step("Valid first", kind="fill", locator=EXPIRY, value="2030-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("Expired", kind="fill", locator=EXPIRY, value="2020-01-01"),
        step("Submit", kind="click", locator=VERIFY),
        step("Covered", kind="done", summary="Tried a future and a past date."),
    ], facts=[wrong, {**wrong, "rule_expects": "accept", "because": "looks fine"}])  # wrong both times
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 8}).json())
    steps = run["steps"]
    assert (steps[1]["judged"], steps[1]["status"]) == ("consistent", "ok")
    assert steps[1]["judged_why"].startswith("Mirmera's own check")
    assert (steps[3]["judged"], steps[3]["status"]) == ("breaks", "drafted")
    [bug] = client.get(f"/sessions/{run['session_id']}/bugs").json()
    assert bug["title"] == "An expiry date in the past is accepted although AC1 says it is refused"
    assert "2020-01-01" in bug["actual"]


def test_the_ais_thinking_is_not_the_apps_slowness(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b: every submit noted "Slow: the app took 9.7 s to answer", but the time
    was the AI checking the answer. Only the app's own answer time counts."""
    import asyncio
    project, charter = kyc
    story_for(client, project, charter, ["Expired documents must be rejected."])

    class SlowJudge(Reading):
        async def complete(self, system, user, **kwargs):
            if kwargs.get("task") == "explore_judge":
                await asyncio.sleep(6)  # a slow local model, not a slow app
            return await super().complete(system, user, **kwargs)

    provider = SlowJudge([step("A future date", kind="fill", locator=EXPIRY, value="2030-01-01"),
                          step("Submit", kind="click", locator=VERIFY),
                          step("Covered", kind="done", summary="One date.")])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json())
    assert provider.judged  # the slow check did run
    notes = [n["content"] for n in client.get(f"/sessions/{run['session_id']}/notes").json()]
    assert not [n for n in notes if n.startswith("Slow:")]


def test_a_bug_found_late_in_a_long_run_still_has_steps_to_replay(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen on the practice app: the Ge'ez refusal, drafted at step 24, had "no steps to replay":
    the journey since the start was longer than a replay takes, so the steps were dropped."""
    project, charter = kyc
    tries = [step(f"Name {i}", kind="try", values={"Full name": f"Abebe Kebede {i}"}) for i in range(34)]
    scripted(monkeypatch, tries + [step("Passport", kind="try", values={"Document type": "Passport"}),
                                   step("Covered", kind="done", summary="Many names, then a passport.")])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 40}).json(), timeout=240)
    with SessionLocal() as db:
        journey = len(db.get(models.ExplorationRun, run["id"]).journey or [])
    bug = next(b for b in client.get(f"/sessions/{run['session_id']}/bugs").json() if "500" in b["title"])
    with SessionLocal() as db:
        steps = db.get(models.Bug, bug["id"]).edited_steps
    assert steps, f"no steps kept (the run's journey had {journey}+ steps)"
    assert len(steps) <= 60 and steps[0]["type"] == "goto"
    assert steps[-1]["action"]["action"] == "click"  # the submit that showed the problem
    names = [s["action"].get("value") for s in steps if s["type"] == "action" and s["action"]["locator"].get("value") == "Full name"]
    assert names == ["Abebe Kebede"]  # each field once, as the form was sent: a try changes one field, the rest valid
    assert any(s["type"] == "action" and s["action"].get("value") == "Passport" for s in steps)


def test_a_short_journey_is_kept_as_it_was():
    from app.agent.explore import shortest
    go = {"type": "goto", "url": "http://app/verify"}
    fill = lambda v: {"type": "action", "action": {"action": "fill", "locator": {"method": "label", "value": "Name"}, "value": v}}  # noqa: E731
    click = {"type": "action", "action": {"action": "click", "locator": {"method": "role", "role": "button", "name": "Verify"}}}
    short = [go, fill("A"), click, fill("B"), click]
    assert shortest(short) == short  # an earlier submit can matter: kept when it fits
    long = [go] + [x for i in range(40) for x in (fill(f"N{i}"), click)]
    assert shortest(long) == [go, fill("N39"), click]


def test_steps_beyond_the_charter_are_marked_and_a_find_there_gets_its_own_charter(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b on the practice app: a charter about the expiry date spent half its steps on
    document types and names (and found two bugs there). It may go on, but the session says so, and
    what it found there gets a follow-up charter."""
    project, existing = kyc
    # the project's other charter is about document types: no follow-up is suggested for them while it is
    client.patch(f"/charters/{existing['id']}", json={"mission_statement": "Explore the home page to discover what it offers"})
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore the expiry date with today, yesterday and tomorrow, everything else valid, "
                             "to discover whether expired documents are rejected"}).json()
    story_for(client, project, charter, ["All three document types are verified the same way",
                                          "Expired documents must be rejected"])
    provider = Reading([
        step("Past", kind="try", values={"Expiry date": "2020-01-01"}),
        step("Future", kind="try", values={"Expiry date": "2030-01-01"}),
        step("A passport", kind="try", values={"Document type": "Passport"}),
        step("Done", kind="done", summary="Expiry dates, then a passport."),
    ])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: provider)
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 8}).json())

    assert '"focus": "The charter is about: Expiry date.' in provider.users[0]  # the agent is told
    tries = [s for s in run["steps"] if s["kind"] == "try"]
    assert [bool(s.get("beyond_charter")) for s in tries] == [False, False, True]
    assert tries[2]["detail"].endswith("(beyond the charter)")
    assert run["summary"].startswith("Expiry dates, then a passport. On the charter (the expiry date): 2 tests. Beyond it: 1 (Document type), which found: ")
    mission = "Explore document type with its test ideas, everything else valid, to discover whether it works as AC1 says"
    assert f'Suggested follow-up charters: "{mission}".' in run["summary"]
    follow = next(c for c in client.get(f"/projects/{project['id']}/charters").json() if c["mission_statement"] == mission)
    assert (follow["source"], follow["status"], follow["priority"]) == ("follow-up", "proposed", "high")
    assert [s["title"] for s in follow["stories"]] == ["Sender verifies identity"]  # on the same story
    notes = [n["content"] for n in client.get(f"/sessions/{run['session_id']}/notes").json()]
    assert any(n.startswith("From step 3 the agent went beyond the charter (the expiry date): Document type.") for n in notes)

    # explored again: the follow-up isn't suggested twice
    second = Reading([step("A passport", kind="try", values={"Document type": "Passport"}), step("Done", kind="done", summary="Again.")])
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: second)
    again = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 4}).json())
    assert "Beyond it: 1 (Document type)" in again["summary"]  # beyond again…
    assert "Suggested follow-up charters" not in again["summary"]  # …but its follow-up already exists


def test_the_charter_target_decides_its_focus():
    from app.agent.explore import beyond_charter, focus_fields
    fields = [{"label": l, "type": "text"} for l in ("Full name", "Document type", "Document number", "Expiry date")]
    # "…to discover whether expired documents are rejected" doesn't make the document fields its focus
    mission = "Explore the expiry date with today, yesterday and tomorrow to discover whether expired documents are rejected"
    assert focus_fields(mission, fields) == ["Expiry date"]
    assert focus_fields("Explore Ge'ez names with real names to discover whether they are kept", fields) == ["Full name"]
    assert focus_fields("Explore My documents after verifying to discover whether all are listed", fields) == ["Document type", "Document number"]
    assert beyond_charter(["Full name"], ["Expiry date"]) and not beyond_charter(["Expiry date"], ["Expiry date"])
    assert not beyond_charter(["Full name"], [])  # no focus known: nothing is beyond it


def test_a_follow_up_checks_the_rule_its_field_is_about():
    """Seen on the practice app: Document type's follow-up said "as AC2 says", the name rule, because
    AC2 mentions "the document". AC1 names the values tried there."""
    from app.agent.explore import rule_for_field
    rules = {"AC1": "Customers can verify with a National ID, a Passport or a Driver's licence. All three are supported equally.",
             "AC2": "The customer's full name is entered as written on the document, in Latin or Ge'ez script.",
             "AC3": "Expired documents must be rejected. A document is expired if its expiry date is before today."}
    assert rule_for_field(rules, "Document type", ["Passport", "Driver's licence"]) == "AC1"
    assert rule_for_field(rules, "Full name", ["አበበ ከበደ", "O'Brien-Smith"]) == "AC2"
    assert rule_for_field(rules, "Phone number", ["0911223344"]) is None


def test_valid_data_is_sent_again_only_after_something_failed(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Seen with qwen3:8b: four "try the form with valid data" in a run, three of them meant as other
    tests (its thought said "Document number = 0", its values were empty). The baseline is sent once
    per page; again only to re-check after a failure."""
    project, charter = kyc
    valid = step("Valid", kind="try", values={})
    scripted(monkeypatch, [
        valid,                                                   # 1. the baseline
        step("Future", kind="try", values={"Expiry date": "2030-01-01"}),
        valid,                                                   # 3. nothing failed since: refused
        step("Passport", kind="try", values={"Document type": "Passport"}),  # a 500
        valid,                                                   # 5. a re-check after the failure: run
        valid,                                                   # 6. refused again
        step("Done", kind="done", summary="Done."),
    ])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 10}).json())
    tries = [(s["n"], s["status"]) for s in run["steps"] if s["kind"] == "try"]
    assert [st for _, st in tries] == ["ok", "ok", "refused", "drafted", "ok", "refused"]
    refused = next(s for s in run["steps"] if s["status"] == "refused")
    assert refused["detail"].startswith("not run: you sent the form with valid data at step 1 on this page, and nothing has failed since")


PROFILE = b"""<!doctype html><html><body><h1>Profile</h1>
<form id="f"><label>Nickname <input name="nick" maxlength="50"></label>
<label>Bio <textarea name="bio"></textarea></label><button>Save</button></form><p id="out"></p>
<script>document.getElementById('f').onsubmit = (e) => { e.preventDefault()
  const f = e.target; document.getElementById('out').textContent = 'Saved. Bio: ' + f.bio.value.slice(0, 80) }</script>
</body></html>"""


@pytest.fixture
def profile_app():
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PROFILE)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_a_counterstring_says_where_text_was_cut(client, project, profile_app, monkeypatch, chromium_available):  # noqa: F811
    """James Bach's counterstring: a field that keeps 50 characters, and a saved text shown cut at 80,
    each said with the exact number, without AI."""
    client.patch(f"/projects/{project['id']}", json={"app_url": profile_app})
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore the profile with long text to discover where it is cut"}).json()
    scripted(monkeypatch, [step("Nickname", kind="try", values={"Nickname": "@count300"}),
                           step("Bio", kind="try", values={"Bio": "@count300"}),
                           step("Done", kind="done", summary="Both fields.")])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json())
    tries = [s for s in run["steps"] if s["kind"] == "try"]
    assert tries[0]["action"] == 'try Nickname = "@count300", then submit'  # the code, not 300 characters
    assert "Nickname took 50 of 300 characters" in tries[0]["detail"]
    assert "Bio is shown cut at 80 characters" in tries[1]["detail"]
    notes = [n["content"] for n in client.get(f"/sessions/{run['session_id']}/notes").json()]
    assert "Counterstring: Nickname took 50 of 300 characters (each number in it is the position of the * after it)." in notes


def test_counterstrings():
    from app.agent.explore import counter_cut, counterstring, expand
    assert counterstring(10) == "*3*5*7*10*"
    s = counterstring(300)
    assert len(s) == 300 and s.endswith("*300*") and expand("@count300") == s
    assert all(s[int(m.group(1)) - 1] == "*" for m in __import__("re").finditer(r"(\d+)\*", s))  # each number: where its * is
    assert counter_cut(s, s[:50], "") == ["took 50 of 300 characters"]
    assert counter_cut(s, s, "Saved: " + s[:120]) == ["is shown cut at 120 characters"]
    assert counter_cut(s, s, "Saved: " + s) == [] and counter_cut(s, s, "Thanks!") == []  # whole, or not shown at all


def test_a_server_error_comes_with_a_curl_command(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """The Passport 500: the draft says how to send the same request without the UI."""
    project, charter = kyc
    scripted(monkeypatch, [step("A passport", kind="try", values={"Document type": "Passport"}),
                           step("Done", kind="done", summary="A passport.")])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 4}).json())
    bug = next(b for b in client.get(f"/sessions/{run['session_id']}/bugs").json() if "500" in b["title"])
    assert "Reproduce without the UI" in bug["description"]
    cmd = bug["description"].split("```\n")[1].split("\n```")[0]
    assert cmd.startswith("curl -X POST ") and "/api/verify" in cmd.splitlines()[0]
    assert "Passport" in cmd and "--data-raw" in cmd


def test_the_run_keeps_its_network_log_without_secrets(client, kyc, monkeypatch, chromium_available):  # noqa: F811
    """Every request the browser made, as a HAR to download: the Passport 500 is in it; the password isn't."""
    project, charter = kyc
    monkeypatch.setenv("MIRMERA_SECRET_KYC_PASSWORD", "hunter2-not-in-the-log")
    scripted(monkeypatch, [
        step("The password", kind="fill_secret", locator={"method": "label", "value": "Password"}, secret="MIRMERA_SECRET_KYC_PASSWORD"),
        step("A passport", kind="try", values={"Document type": "Passport"}),
        step("Done", kind="done", summary="A passport.")])
    run = finished(client, client.post(f"/charters/{charter['id']}/explore", json={"max_steps": 5}).json())
    assert run["har"] == f"network-{run['id'][:8]}.har"
    folder = runner_api.session_folder(run["session_id"])
    assert not list(folder.glob("*.raw.har"))  # the uncleaned log is never left behind
    r = client.get(f"/exploration-runs/{run['id']}/har")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert ".har" in r.headers["content-disposition"]
    har = r.json()
    verify = [e for e in har["log"]["entries"] if "/api/verify" in e["request"]["url"]]
    assert verify and verify[-1]["request"]["method"] == "POST" and verify[-1]["response"]["status"] == 500
    assert "hunter2-not-in-the-log" not in r.text
    assert client.get("/exploration-runs/nope/har").status_code == 404
