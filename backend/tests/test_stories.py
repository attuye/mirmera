"""User stories: import or write one, link risks and charters, see the testing under it and what's left."""
import pytest

from app import models
from app.config import settings
from app.database import SessionLocal
from app.stories import StoryProblem, parse_ref
from app.trackers import mock as mock_trackers
from tests.test_coverage import add


@pytest.fixture
def mocked(monkeypatch, client, project):
    monkeypatch.setattr(settings, "mock_trackers", True)
    mock_trackers.reset()
    client.patch(f"/projects/{project['id']}", json={"github_repo": "addisverify/kyc", "jira_project_key": "KYC"})
    return project


def risk(client, project, title, impact=4, likelihood=3):
    return client.post(f"/projects/{project['id']}/risks", json={
        "title": title, "failure_mode": f"{title}, for customers", "quality_criterion": "capability",
        "impact": impact, "likelihood": likelihood}).json()


def test_story_references():
    assert parse_ref("KYC-12") == ("jira", "KYC-12") and parse_ref("kyc-12") == ("jira", "KYC-12")
    assert parse_ref("https://acme.atlassian.net/browse/KYC-7?focus=1") == ("jira", "KYC-7")
    assert parse_ref("#45") == ("github", "#45") and parse_ref("45") == ("github", "#45")
    assert parse_ref("https://github.com/a/b/issues/9") == ("github", "#9")
    with pytest.raises(StoryProblem, match="Jira key"):
        parse_ref("the passport story")


def test_import_from_jira_and_github_then_refresh(client, mocked):
    pid = mocked["id"]
    key = client.post("/mock-trackers/issues", json={
        "tracker": "jira", "target": "KYC", "title": "As a customer I can upload my passport",
        "body": "Acceptance criteria:\n- JPG, PNG or PDF up to 10 MB\n" + "- an expired passport is refused with a reason\n" * 20,
    }).json()["issue_key"]
    r = client.post(f"/projects/{pid}/stories/import", json={"ref": key})
    assert r.status_code == 200, r.text
    story = r.json()
    assert (story["key"], story["tracker"], story["closed"]) == (key, "jira", False)
    assert story["description"].endswith("refused with a reason")  # the whole description, not a snippet
    assert client.post(f"/projects/{pid}/stories/import", json={"ref": key.lower()}).status_code == 409
    assert "not in this project's Jira project" in client.post(f"/projects/{pid}/stories/import", json={"ref": "PAY-1"}).json()["detail"]
    assert client.post(f"/projects/{pid}/stories/import", json={"ref": "#99"}).status_code == 400  # the tracker's 404, said

    gh = client.post(f"/projects/{pid}/stories/import", json={"ref": "#1"}).json()
    assert gh["key"] == "#1" and gh["tracker"] == "github"

    mock_trackers.MockTracker("jira", "KYC").set_closed(key)
    refreshed = client.post(f"/stories/{story['id']}/refresh").json()
    assert refreshed["closed"] is True and refreshed["tracker_status"] == "closed"
    assert [s["key"] for s in client.get(f"/projects/{pid}/stories").json()] == ["#1", key]  # open first


def test_a_story_written_here(client, project):
    pid = project["id"]
    r = client.post(f"/projects/{pid}/stories", json={"title": "Customers can retry a failed upload", "key": " KYC-30 "})
    story = r.json()
    assert story["key"] == "KYC-30" and story["tracker"] is None
    assert client.post(f"/stories/{story['id']}/refresh").status_code == 400
    assert client.post(f"/projects/{pid}/stories", json={"title": "Another", "key": "KYC-30"}).status_code == 409
    done = client.patch(f"/stories/{story['id']}", json={"closed": True, "description": "  "}).json()
    assert done["closed"] is True and done["description"] is None


def test_links_testing_and_whats_left(client, project):
    pid = project["id"]
    story = client.post(f"/projects/{pid}/stories", json={"title": "Upload a passport", "key": "KYC-12"}).json()
    sid = story["id"]
    left = client.get(f"/stories/{sid}").json()["left_list"]
    assert [l["action"] for l in left] == ["Link a risk", "Add a charter"]

    big = risk(client, project, "Large scans are refused", 5, 4)
    detail = client.post(f"/stories/{sid}/risks/new", json={
        "title": "Expired passports pass", "failure_mode": "An expired passport is verified", "quality_criterion": "capability",
        "impact": 5, "likelihood": 2}).json()
    expired = next(r for r in detail["risk_list"] if r["title"] == "Expired passports pass")
    detail = client.put(f"/stories/{sid}/risks", json={"ids": [expired["id"], big["id"]]}).json()
    assert [r["ref"] for r in detail["risk_list"]] == [big["ref"], expired["ref"]]  # highest score first
    assert client.get(f"/projects/{pid}/risks").json()[0]["stories"] == [{"id": sid, "key": "KYC-12", "title": "Upload a passport"}]

    charter = client.post(f"/projects/{pid}/charters", json={
        "mission_statement": "Explore passport upload with odd files", "risk_ids": [big["id"]], "story_ids": [sid]}).json()
    assert charter["stories"][0]["key"] == "KYC-12"
    assert client.post(f"/projects/{pid}/charters", json={"mission_statement": "x", "story_ids": ["nope"]}).status_code == 400
    texts = [l["text"] for l in client.get(f"/stories/{sid}").json()["left_list"]]
    assert texts == [f"{expired['ref']} Expired passports pass: no charter in this story investigates it.",
                     "Not explored yet: Explore passport upload with odd files"]

    session = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    client.post(f"/sessions/{session['id']}/bugs", json={"title": "A 6 MB scan is refused", "severity": "high"})
    with SessionLocal() as db:
        add(db, session["id"], "navigation", "https://kyc.et/upload/12", 10)
        add(db, session["id"], "action", "https://kyc.et/upload/12", 9, data={"action": "click"})
        db.commit()
    client.post(f"/sessions/{session['id']}/end")
    d = client.get(f"/stories/{sid}").json()
    assert (d["charters"], d["sessions"], d["bugs"]) == (1, 1, 1)
    assert d["charter_list"][0]["risk_refs"] == [big["ref"]] and d["session_list"][0]["tester"] == "attu"
    assert d["pages"] == [{"page": "/upload/:id", "sessions": 1, "actions": 1}]
    assert [l["action"] for l in d["left_list"]] == ["Add a charter", "Open debrief", "Review"]  # the drafted debrief says partly fulfilled

    again = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "sara"}).json()
    client.post(f"/sessions/{again['id']}/end")
    actions = [l["action"] for l in client.get(f"/stories/{sid}").json()["left_list"]]
    assert actions.count("Open debrief") == 1  # only the charter's latest debrief counts
    client.delete(f"/sessions/{again['id']}")

    summary = client.get(f"/projects/{pid}/stories").json()[0]
    assert (summary["risks"], summary["charters"], summary["sessions"], summary["left"]) == (2, 1, 1, 3)

    stranger = client.post("/projects", json={"name": "Other"}).json()
    foreign = risk(client, stranger, "Somebody else's risk")
    assert client.put(f"/stories/{sid}/risks", json={"ids": [foreign["id"]]}).status_code == 400


def test_charter_suggestions_from_a_story(client, project):
    pid = project["id"]
    story = client.post(f"/projects/{pid}/stories", json={
        "title": "Upload a passport", "key": "KYC-12", "description": "Up to 10 MB", "url": "https://jira.example/browse/KYC-12"}).json()
    a = client.post(f"/stories/{story['id']}/artifact").json()
    assert (a["type"], a["label"], a["file_ref"]) == ("spec", "KYC-12: Upload a passport", "https://jira.example/browse/KYC-12")
    client.patch(f"/stories/{story['id']}", json={"description": "Up to 20 MB"})
    again = client.post(f"/stories/{story['id']}/artifact").json()
    assert again["id"] == a["id"]  # the same artifact, updated
    with SessionLocal() as db:
        assert "Up to 20 MB" in db.get(models.Artifact, a["id"]).raw_content
    assert client.post(f"/projects/{pid}/charters/suggest", json={"artifact_ids": [a["id"]]}).status_code == 200


def test_deleting_keeps_risks_and_charters(client, project):
    pid = project["id"]
    story = client.post(f"/projects/{pid}/stories", json={"title": "Upload a passport"}).json()
    r = risk(client, project, "Large scans are refused")
    client.put(f"/stories/{story['id']}/risks", json={"ids": [r["id"]]})
    charter = client.post(f"/projects/{pid}/charters", json={"mission_statement": "Explore upload", "story_ids": [story["id"]]}).json()
    assert client.delete(f"/stories/{story['id']}").json() == {"deleted": {"stories": 1}}
    assert client.get(f"/charters/{charter['id']}").json()["stories"] == []
    assert client.get(f"/projects/{pid}/risks").json()[0]["stories"] == []

    again = client.post(f"/projects/{pid}/stories", json={"title": "Upload again"}).json()
    client.put(f"/stories/{again['id']}/charters", json={"ids": [charter["id"]]})
    out = client.delete(f"/projects/{pid}?confirm=ShopFlow").json()
    assert out["deleted"]["stories"] == 1
