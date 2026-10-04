"""Sessions, notes, bugs and debriefs: the testing side of the current API."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from app.ai.base import LLMProvider, ProviderError
from app.routers.sessions import _compute_time_breakdown

T0 = datetime(2026, 10, 1, 9, 0, 0)


def note(client, session, tag, content):
    return client.post(f"/sessions/{session['id']}/notes", json={"tag": tag, "content": content}).json()


# ---------- session lifecycle ----------

def test_starting_a_session_makes_the_charter_active(client, charter, session):
    assert session["ended_at"] is None
    assert client.get(f"/charters/{charter['id']}").json()["status"] == "active"
    assert client.get(f"/charters/{charter['id']}/active-session").json()["id"] == session["id"]


def test_session_needs_an_existing_charter(client):
    r = client.post("/sessions", json={"charter_id": "nope", "tester_id": "attu"})
    assert r.status_code == 404


def test_pause_and_resume(client, session):
    sid = session["id"]
    paused = client.post(f"/sessions/{sid}/pause").json()
    assert paused["paused_at"] is not None
    assert client.post(f"/sessions/{sid}/pause").status_code == 400  # already paused

    resumed = client.post(f"/sessions/{sid}/resume").json()
    assert resumed["paused_at"] is None
    assert resumed["total_paused_seconds"] >= 0
    assert client.post(f"/sessions/{sid}/resume").status_code == 400  # not paused


def test_ending_a_session_drafts_a_debrief_and_closes_the_charter(client, charter, session):
    sid = session["id"]
    note(client, session, "test", "Applied SAVE10, then switched to EUR")
    r = client.post(f"/sessions/{sid}/end")
    assert r.status_code == 200
    debrief = r.json()
    assert "Mock debrief" in debrief["coverage_summary"]
    assert debrief["ai_draft"] == debrief["coverage_summary"]
    assert client.get(f"/charters/{charter['id']}").json()["status"] == "done"
    assert client.get(f"/charters/{charter['id']}/active-session").status_code == 404


def test_session_cannot_be_ended_or_paused_twice(client, session):
    sid = session["id"]
    client.post(f"/sessions/{sid}/end")
    assert client.post(f"/sessions/{sid}/end").status_code == 400
    assert client.post(f"/sessions/{sid}/pause").status_code == 400


def test_debrief_is_still_saved_when_the_ai_fails(client, session, monkeypatch):
    class Broken(LLMProvider):
        async def complete(self, system, user, **kwargs):
            raise ProviderError("provider down")

    monkeypatch.setattr("app.ai.routing.get_provider", lambda name, keys=None: Broken())
    r = client.post(f"/sessions/{session['id']}/end")
    assert r.status_code == 200
    assert r.json()["coverage_summary"] is None


# ---------- notes ----------

def test_notes_are_returned_in_order(client, session):
    for i in range(3):
        note(client, session, "test", f"step {i}")
    contents = [n["content"] for n in client.get(f"/sessions/{session['id']}/notes").json()]
    assert contents == ["step 0", "step 1", "step 2"]


def test_note_tag_is_validated(client, session):
    r = client.post(f"/sessions/{session['id']}/notes", json={"tag": "random", "content": "x"})
    assert r.status_code == 422


# ---------- bugs and the evidence trail ----------

def test_bug_snapshots_the_preceding_notes(client, session):
    for i in range(8):
        note(client, session, "test", f"step {i}")
    bug = client.post(f"/sessions/{session['id']}/bugs",
                      json={"title": "Discount lost after currency switch", "trail_window": 3}).json()
    assert [t["content"] for t in bug["reproduction_trail"]] == ["step 5", "step 6", "step 7"]
    assert bug["charter_id"] == session["charter_id"]


def test_trail_does_not_change_when_more_notes_are_added(client, session):
    note(client, session, "test", "before")
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Bug"}).json()
    note(client, session, "test", "after")
    stored = client.get(f"/sessions/{session['id']}/bugs").json()[0]
    assert [t["content"] for t in stored["reproduction_trail"]] == ["before"]
    assert stored["id"] == bug["id"]


def test_project_bug_list_includes_the_charter_mission(client, project, session):
    client.post(f"/sessions/{session['id']}/bugs", json={"title": "Bug", "severity": "high"})
    bugs = client.get(f"/projects/{project['id']}/bugs").json()
    assert len(bugs) == 1
    assert bugs[0]["charter_mission"] == "Explore checkout with coupons"
    assert bugs[0]["severity"] == "high"


# ---------- debriefs ----------

def test_tester_edit_is_kept_separately_from_the_ai_draft(client, project, session):
    sid = session["id"]
    client.post(f"/sessions/{sid}/end")
    updated = client.patch(f"/sessions/{sid}/debrief", json={"final_text": "My own summary"}).json()
    assert updated["final_text"] == "My own summary"
    assert "Mock debrief" in updated["ai_draft"]

    listed = client.get(f"/projects/{project['id']}/debriefs").json()
    assert listed[0]["coverage_summary"] == "My own summary"


def test_debrief_before_session_end_is_404(client, session):
    assert client.get(f"/sessions/{session['id']}/debrief").status_code == 404


# ---------- time breakdown ----------

def _n(tag, minute):
    return SimpleNamespace(tag=tag, timestamp=T0 + timedelta(minutes=minute))


def test_time_is_split_by_note_tag():
    notes = [_n("setup", 0), _n("test", 10), _n("bug", 40), _n("question", 50)]
    split = _compute_time_breakdown(notes, T0 + timedelta(minutes=60))
    assert split == {"setup": 0.167, "on_charter": 0.5, "bug_investigation": 0.167, "opportunity": 0.167}


def test_no_notes_means_no_breakdown():
    assert _compute_time_breakdown([], T0) == {}