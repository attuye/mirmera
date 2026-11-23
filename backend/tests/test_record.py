"""The agent's track record: what testers made of its drafts, by who judged them, per run."""
from app import models
from app.agent.record import line, record
from app.database import SessionLocal


def draft(db, session, charter, title, judged_by, kind=None, dismissal=None, confirmation=None, agent=True):
    b = models.Bug(session_id=session.id, charter_id=charter.id, title=title, kind=kind, dismissal=dismissal,
                   confirmation=confirmation, environment={"found_by": "Mirmera agent" if agent else "Attu", "judged_by": judged_by})
    db.add(b)
    db.flush()
    return b


def test_the_record_counts_what_testers_made_of_the_drafts(client, project):
    with SessionLocal() as db:
        charter = models.Charter(project_id=project["id"], mission_statement="Explore expiry dates")
        db.add(charter)
        db.flush()
        session = models.Session(charter_id=charter.id, tester_id="Attu")
        db.add(session)
        db.flush()
        three = {"runs": 3, "reproduced": 3}
        bugs = [draft(db, session, charter, "500 on Passport", "mirmera", kind="bug", confirmation=three),
                draft(db, session, charter, "Expired ID accepted", "ai", kind="bug", confirmation=three),
                draft(db, session, charter, "Blurry photo accepted", "ai", dismissal={"reason": "by_design"},
                      confirmation={"runs": 3, "reproduced": 0}),
                draft(db, session, charter, "Should Ge'ez names be allowed?", "ai", kind="question"),
                draft(db, session, charter, "Status shown differs", "mirmera"),
                draft(db, session, charter, "Older draft", None, kind="bug"),
                draft(db, session, charter, "Logged by hand", None, kind="bug", agent=False)]
        db.add(models.ExplorationRun(project_id=project["id"], charter_id=charter.id, session_id=session.id, start_url="x",
                                     bug_ids=[b.id for b in bugs[:5]]))
        db.commit()
        r = record(db, project["id"])
    o = r["overall"]
    assert (o["drafts"], o["bugs"], o["questions"], o["dismissed"], o["waiting"]) == (6, 3, 1, 1, 1)  # not the hand-logged one
    assert o["kept_share"] == 0.6 and o["dismissed_for"] == {"works as intended": 1}
    assert (o["replayed"], o["every_time"], o["never_again"]) == (3, 2, 1)
    judges = {j["judge"]: j for j in r["by_judge"]}
    assert [j["judge"] for j in r["by_judge"]] == ["ai", "mirmera", "unknown"]
    assert (judges["ai"]["bugs"], judges["ai"]["questions"], judges["ai"]["dismissed"]) == (1, 1, 1)
    assert judges["mirmera"]["label"] == "Mirmera's own checks" and judges["mirmera"]["waiting"] == 1
    assert r["runs"][0]["drafts"] == 5 and r["runs"][0]["mission"] == "Explore expiry dates"
    assert line(r) == ("The agent's drafts in this project so far: 3 saved as bugs, 1 as questions, 1 dismissed, "
                       "1 waiting for review. Where the AI judged a rule broken: 1 of 3 kept as bugs.")
    assert client.get(f"/projects/{project['id']}/agent-record").json()["overall"]["drafts"] == 6


def test_no_drafts_no_record(client, project):
    r = client.get(f"/projects/{project['id']}/agent-record").json()
    assert r["overall"]["drafts"] == 0 and r["runs"] == [] and line(r) == ""
