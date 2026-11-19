"""Charters for a story from its accepted risks; suggestions that stay; editing a charter."""
from app import models
from app.database import SessionLocal

SPEC = ("As a customer I can verify my identity.\nAcceptance criteria:\n- National ID, Passport or Driver's licence\n"
        "- Expired documents are refused")


def story_with_risks(client, project, accepted=("Expired documents accepted", "Passports refused"), proposed=("Slow page",)):
    story = client.post(f"/projects/{project['id']}/stories", json={"title": "Identity verification", "description": SPEC}).json()
    ids = []
    for title, status in [(t, "accepted") for t in accepted] + [(t, "proposed") for t in proposed]:
        r = client.post(f"/projects/{project['id']}/risks", json={
            "title": title, "failure_mode": f"{title}, for customers", "quality_criterion": "capability",
            "impact": 5 if "Expired" in title else 4, "likelihood": 3}).json()
        client.patch(f"/risks/{r['id']}", json={"status": status})
        ids.append(r["id"])
    client.put(f"/stories/{story['id']}/risks", json={"ids": ids})
    return story


def job(client, project, kind, request):
    r = client.post(f"/projects/{project['id']}/ai-jobs", json={"kind": kind, "request": request})
    assert r.status_code == 200, r.text
    return client.get(f"/ai-jobs/{r.json()['id']}").json()


def test_charters_come_from_the_storys_accepted_risks(client, project):
    st = story_with_risks(client, project)
    done = job(client, project, "story_charters", {"story_id": st["id"]})
    assert done["status"] == "done", done["error"]
    [draft] = done["result"]["drafts"]
    # the template, the riskiest first, linked to the accepted risks only (not the proposed one)
    assert draft["mission_statement"] == ("Explore expired documents accepted with boundary values and realistic test data "
                                          "to discover whether R1, R2 can actually happen")
    assert (draft["risk_refs"], draft["status"]) == (["R1", "R2"], "suggested")
    assert done["result"]["uncovered_risk_refs"] == []
    # still there after leaving the page: the latest job for this story
    latest = client.get(f"/projects/{project['id']}/ai-jobs/latest", params={"kind": "story_charters", "story_id": st["id"]}).json()
    assert latest["id"] == done["id"]


def test_no_accepted_risks_no_charters(client, project):
    st = story_with_risks(client, project, accepted=())
    failed = job(client, project, "story_charters", {"story_id": st["id"]})
    assert failed["status"] == "failed" and failed["error"].startswith("Accept at least one of the story's risks first")


def test_a_charter_the_story_has_is_not_suggested_again(client, project):
    st = story_with_risks(client, project)
    first = job(client, project, "story_charters", {"story_id": st["id"]})["result"]["drafts"][0]
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": first["mission_statement"], "source": "risk-based", "risk_ids": first["risk_ids"], "story_ids": [st["id"]]}).json()
    assert charter["id"]
    assert job(client, project, "story_charters", {"story_id": st["id"]})["result"]["drafts"] == []


def test_accepted_and_dropped_suggestions_are_remembered(client, project):
    st = story_with_risks(client, project, accepted=("Expired documents accepted", "Passports refused", "Ge'ez names refused"))
    done = job(client, project, "story_charters", {"story_id": st["id"]})
    assert len(done["result"]["drafts"]) == 2
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Explore expiry dates to discover x"}).json()
    marked = client.post(f"/ai-jobs/{done['id']}/items", json={"index": 0, "status": "accepted", "charter_id": charter["id"]}).json()
    marked = client.post(f"/ai-jobs/{done['id']}/items", json={"index": 1, "status": "dropped"}).json()
    assert [(d["status"], d.get("charter_id")) for d in marked["result"]["drafts"]] == [("accepted", charter["id"]), ("dropped", None)]
    assert client.post(f"/ai-jobs/{done['id']}/items", json={"index": 5, "status": "dropped"}).status_code == 404
    other = client.post("/projects", json={"name": "Other"}).json()
    theirs = client.post(f"/projects/{other['id']}/charters", json={"mission_statement": "Explore something else to discover y"}).json()
    assert client.post(f"/ai-jobs/{done['id']}/items", json={"index": 0, "status": "accepted", "charter_id": theirs["id"]}).status_code == 404


def test_suggestions_from_a_change_stay_too(client, project):
    art = client.post(f"/projects/{project['id']}/artifacts", json={"type": "spec", "label": "KYC spec", "raw_content": SPEC}).json()
    done = job(client, project, "charter_suggestions", {"artifact_ids": [art["id"]]})
    assert done["status"] == "done", done["error"]
    assert done["result"]["source"]["label"] == "KYC spec"
    assert done["result"]["suggestions"] and {s["status"] for s in done["result"]["suggestions"]} == {"suggested"}
    marked = client.post(f"/ai-jobs/{done['id']}/items", json={"index": 0, "status": "dropped"}).json()
    assert marked["result"]["suggestions"][0]["status"] == "dropped"


def test_edit_a_charter_until_a_session_uses_it(client, project):
    charter = client.post(f"/projects/{project['id']}/charters", json={"mission_statement": "Investigate the expiry date validation logic"}).json()
    mission = ("Explore expiry dates with a past date, today and a future date, everything else valid, "
               "to discover whether expired documents are rejected")
    edited = client.patch(f"/charters/{charter['id']}", json={"mission_statement": f"  {mission} ", "priority": "high"})
    assert edited.status_code == 200 and (edited.json()["mission_statement"], edited.json()["priority"]) == (mission, "high")
    assert client.patch(f"/charters/{charter['id']}", json={"mission_statement": "short"}).status_code == 422

    session = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "Attu"})
    assert session.status_code == 200, session.text
    refused = client.patch(f"/charters/{charter['id']}", json={"mission_statement": mission + " again"})
    assert refused.status_code == 409 and "1 session has used this charter" in refused.json()["detail"]
    with SessionLocal() as db:
        assert db.get(models.Charter, charter["id"]).mission_statement == mission


def test_the_charter_sentence_reads_as_one_sentence():
    from app.routers.risks import mission_of
    assert mission_of("Document expiry date validation", "Test data with known expiry dates.",
                      "Whether expired documents are rejected.") == (
        "Explore document expiry date validation with test data with known expiry dates "
        "to discover whether expired documents are rejected")
    # names and acronyms keep their capitals
    assert mission_of("Ge'ez script names", "KYC examples", "Whether they're kept") == (
        "Explore Ge'ez script names with KYC examples to discover whether they're kept")
