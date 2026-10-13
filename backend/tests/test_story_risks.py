"""Suggest risks for a story: the project's risks it touches (to link) and the new ones it brings in."""
from app import models
from app.database import SessionLocal
from app.story_risks import same_risk

PASSPORT = ("As a customer I can upload my passport to verify my identity.\n"
            "Acceptance criteria:\n- JPG, PNG or PDF up to 10 MB\n- An expired passport is refused with a reason")


def story(client, project, title="Upload a passport", description=PASSPORT):
    return client.post(f"/projects/{project['id']}/stories", json={"title": title, "description": description}).json()


def risk(client, project, title, failure_mode="x fails for customers"):
    return client.post(f"/projects/{project['id']}/risks", json={
        "title": title, "failure_mode": failure_mode, "quality_criterion": "capability", "impact": 4, "likelihood": 3}).json()


def suggest(client, project, st, **extra):
    r = client.post(f"/projects/{project['id']}/ai-jobs", json={"kind": "story_risks", "request": {"story_id": st["id"], **extra}})
    assert r.status_code == 200, r.text
    return client.get(f"/ai-jobs/{r.json()['id']}").json()  # the test client returns after the background work


def test_touched_project_risks_and_new_ones(client, project):
    expired = risk(client, project, "Expired passports accepted")
    risk(client, project, "Slow dashboard on Mondays")
    st = story(client, project)
    job = suggest(client, project, st)
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result["story_id"] == st["id"] and result["thin"] is None
    # the project's risk it touches: suggested, not linked yet
    assert [(x["ref"], x["status"]) for x in result["links"]] == [(expired["ref"], "suggested")]
    assert result["links"][0]["reason"] == "(mock) The story mentions expired."
    # new ones: proposed, linked to the story, riskiest first, numbered after the project's
    assert [x["ref"] for x in result["new"]] == ["R3", "R4", "R5"]
    detail = client.get(f"/stories/{st['id']}").json()
    assert [(r["ref"], r["status"]) for r in detail["risk_list"]] == [("R3", "proposed"), ("R4", "proposed"), ("R5", "proposed")]
    with SessionLocal() as db:
        r3 = db.query(models.Risk).filter(models.Risk.project_id == project["id"], models.Risk.ref == "R3").one()
        assert (r3.source, r3.prompt_version, r3.tester_notes) == ("ai", "story-risks-v1", "Suggested from story Upload a passport.")
        assert (r3.score, r3.title) == (12, "Upload a passport: valid input refused")  # the riskiest gets the lowest ref


def test_linking_or_dropping_a_suggestion_is_remembered(client, project):
    expired = risk(client, project, "Expired passports accepted")
    upload = risk(client, project, "Passport upload times out")
    st = story(client, project)
    suggest(client, project, st)

    linked = client.post(f"/stories/{st['id']}/risk-suggestions/{expired['id']}", json={"action": "link"})
    assert linked.status_code == 200
    assert expired["ref"] in [r["ref"] for r in linked.json()["risk_list"]]
    dropped = client.post(f"/stories/{st['id']}/risk-suggestions/{upload['id']}", json={"action": "drop"}).json()
    assert upload["ref"] not in [r["ref"] for r in dropped["risk_list"]]

    latest = client.get(f"/projects/{project['id']}/ai-jobs/latest", params={"kind": "story_risks", "story_id": st["id"]}).json()
    assert {x["ref"]: x["status"] for x in latest["result"]["links"]} == {expired["ref"]: "linked", upload["ref"]: "dropped"}
    assert client.post(f"/stories/{st['id']}/risk-suggestions/nope", json={"action": "link"}).status_code == 404

    # asked again: a risk already linked isn't suggested again
    again = suggest(client, project, st)["result"]
    assert expired["ref"] not in [x["ref"] for x in again["links"]]


def test_a_new_risk_the_project_already_has_becomes_a_link(client, project, monkeypatch):
    from app import schemas
    twin = risk(client, project, "Expired IDs accepted at upload")
    proposed = {"title": "Expired ids accepted at upload", "failure_mode": "An expired ID verifies a customer",
                "quality_criterion": "capability", "elements": ["upload"], "impact": 5, "likelihood": 3,
                "rationale": "r", "evidence": "stated"}

    async def answer(*args, **kwargs):  # the model proposes, as new, a risk the project has (and once more)
        return schemas.LLMStoryRisks(touches=[], new_risks=[proposed, {**proposed, "title": "Brand new: upload twice"},
                                                            {**proposed, "title": "Brand new: upload twice!"}])
    monkeypatch.setattr("app.ai.tasks.story_risks", answer)
    result = client.post(f"/projects/{project['id']}/story-risks", json={"story_id": story(client, project)["id"]}).json()
    assert [x["title"] for x in result["new"]] == ["Brand new: upload twice"]  # the repeat is made once
    assert result["links"] == [{"risk_id": twin["id"], "ref": twin["ref"], "title": twin["title"], "score": 12, "status": "suggested",
                                "reason": f'Proposed as a new risk ("Expired ids accepted at upload"), but {twin["ref"]} already covers it.'}]


def test_a_story_with_no_detail(client, project):
    bare = story(client, project, "Pay in Birr", description=None)
    job = suggest(client, project, bare)
    assert job["status"] == "failed" and "no description to find risks in" in job["error"]
    assert client.post(f"/projects/{project['id']}/story-risks", json={"story_id": bare["id"]}).status_code == 409

    short = story(client, project, "Pay with telebirr", description="Customers can pay with telebirr.")
    result = suggest(client, project, short)["result"]
    assert result["thin"].startswith("(mock) The story has no acceptance criteria") and len(result["new"]) == 1


def test_jobs_are_per_story(client, project):
    a, b = story(client, project, "Upload a passport"), story(client, project, "Upload a selfie")
    suggest(client, project, a)
    latest = lambda st: client.get(f"/projects/{project['id']}/ai-jobs/latest",  # noqa: E731
                                   params={"kind": "story_risks", "story_id": st["id"]}).json()
    assert latest(a)["request"]["story_id"] == a["id"]
    assert latest(b) is None  # another story's suggestions aren't this one's
    other = client.post("/projects", json={"name": "Other"}).json()
    assert client.post(f"/projects/{other['id']}/story-risks", json={"story_id": a["id"]}).status_code == 404


def test_same_risk_titles():
    assert same_risk("Expired IDs accepted", "expired ids accepted")
    assert same_risk("Upload a passport: valid input refused", "Upload passport: valid input refused")
    assert not same_risk("Expired IDs accepted", "Expired passports accepted at upload")
