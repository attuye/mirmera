"""Sprints: dates and stories, everything tested in those dates, and what's left."""
from datetime import timedelta

from sqlalchemy import create_engine, inspect

from app import models
from app.database import Base, SessionLocal, add_missing_columns
from app.timeutil import utcnow


def day(offset: int) -> str:
    return (utcnow().date() + timedelta(days=offset)).isoformat()


def story(client, project, title, key=None):
    return client.post(f"/projects/{project['id']}/stories", json={"title": title, "key": key}).json()


def charter(client, project, mission, story_ids=(), risk_ids=()):
    return client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": mission, "story_ids": list(story_ids), "risk_ids": list(risk_ids)}).json()


def test_sprints_and_their_states(client, project):
    pid = project["id"]
    assert client.post(f"/projects/{pid}/sprints", json={"name": "S1", "starts_on": day(0), "ends_on": day(-1)}).status_code == 422
    past = client.post(f"/projects/{pid}/sprints", json={"name": "Sprint 13", "starts_on": day(-20), "ends_on": day(-7)}).json()
    now = client.post(f"/projects/{pid}/sprints", json={"name": "Sprint 14", "starts_on": day(-6), "ends_on": day(7),
                                                       "goal": " Passports end to end "}).json()
    nxt = client.post(f"/projects/{pid}/sprints", json={"name": "Sprint 15", "starts_on": day(8), "ends_on": day(21)}).json()
    assert (past["state"], now["state"], nxt["state"]) == ("past", "current", "upcoming")
    assert now["days_left"] == 8 and now["goal"] == "Passports end to end"
    assert [s["name"] for s in client.get(f"/projects/{pid}/sprints").json()] == ["Sprint 15", "Sprint 14", "Sprint 13"]
    edited = client.patch(f"/sprints/{nxt['id']}", json={"name": "Sprint 15b", "goal": ""}).json()
    assert edited["name"] == "Sprint 15b" and edited["goal"] is None
    assert client.patch(f"/sprints/{nxt['id']}", json={"ends_on": day(0)}).status_code == 422


def test_stories_move_between_sprints(client, project):
    pid = project["id"]
    a, b = story(client, project, "Upload a passport", "KYC-12"), story(client, project, "Retry an upload", "KYC-13")
    s13 = client.post(f"/projects/{pid}/sprints", json={"name": "Sprint 13", "starts_on": day(-20), "ends_on": day(-7),
                                                       "story_ids": [a["id"], b["id"]]}).json()
    assert s13["stories"] == 2 and s13["story_list"][0]["sprint"] == {"id": s13["id"], "name": "Sprint 13"}
    s14 = client.post(f"/projects/{pid}/sprints", json={"name": "Sprint 14", "starts_on": day(-6), "ends_on": day(7)}).json()
    client.put(f"/sprints/{s14['id']}/stories", json={"ids": [a["id"]]})  # carried over
    assert [s["key"] for s in client.get(f"/sprints/{s13['id']}").json()["story_list"]] == ["KYC-13"]
    out = client.patch(f"/stories/{b['id']}", json={"sprint_id": None}).json()
    assert out["sprint"] is None
    assert client.patch(f"/stories/{b['id']}", json={"sprint_id": "nope"}).status_code == 400
    assert client.patch(f"/stories/{b['id']}", json={"sprint_id": s14["id"]}).json()["sprint"]["name"] == "Sprint 14"
    assert client.delete(f"/sprints/{s14['id']}").json() == {"deleted": {"sprints": 1}}
    assert client.get(f"/stories/{a['id']}").json()["sprint"] is None  # the story stays


def test_testing_in_the_sprint_inside_and_outside_its_stories(client, project):
    pid = project["id"]
    high = client.post(f"/projects/{pid}/risks", json={"title": "Fraud passes", "failure_mode": "A forged ID is verified",
                                                       "quality_criterion": "security", "impact": 5, "likelihood": 4}).json()
    other = client.post(f"/projects/{pid}/risks", json={"title": "Slow upload", "failure_mode": "Upload takes a minute",
                                                        "quality_criterion": "performance", "impact": 4, "likelihood": 4}).json()
    st = story(client, project, "Upload a passport", "KYC-12")
    client.put(f"/stories/{st['id']}/risks", json={"ids": [high["id"]]})
    inside = charter(client, project, "Explore passport upload", [st["id"]])
    outside = charter(client, project, "Hunt for slow pages", risk_ids=[other["id"]])
    sprint = client.post(f"/projects/{pid}/sprints", json={"name": "Sprint 14", "starts_on": day(-6), "ends_on": day(7),
                                                          "story_ids": [st["id"]]}).json()
    s1 = client.post("/sessions", json={"charter_id": inside["id"], "tester_id": "attu"}).json()
    client.post(f"/sessions/{s1['id']}/bugs", json={"title": "Big scans refused", "severity": "high"})
    client.post(f"/sessions/{s1['id']}/end")
    s2 = client.post("/sessions", json={"charter_id": outside["id"], "tester_id": "sara"}).json()
    client.post(f"/sessions/{s2['id']}/end")
    old = client.post("/sessions", json={"charter_id": outside["id"], "tester_id": "sara"}).json()
    with SessionLocal() as db:  # a session from before the sprint doesn't count
        db.get(models.Session, old["id"]).started_at = utcnow() - timedelta(days=30)
        db.commit()

    d = client.get(f"/sprints/{sprint['id']}").json()
    assert d["sessions"] == 2 and d["bugs"] == 1
    assert (d["under_stories"]["sessions"], d["under_stories"]["bugs"]) == (1, 1)
    assert d["outside_stories"]["sessions"] == 1
    assert [c["mission_statement"] for c in d["outside_charters"]] == ["Hunt for slow pages"]
    assert [r["ref"] for r in d["high_unexplored"]] == [high["ref"]]  # the inside charter doesn't investigate it
    assert d["high_unexplored"][0]["in_sprint"] is True
    texts = [l["text"] for l in d["left_list"]]
    assert texts[0] == f"KYC-12: {high['ref']} Fraud passes: no charter in this story investigates it."
    assert texts[-1] == f"High risk not explored in this sprint: {high['ref']} Fraud passes (score 20, linked to a story here)"
    assert any(l["to"] == f"/stories/{st['id']}#charters" for l in d["left_list"])  # anchors point at the story

    summary = client.get(f"/projects/{pid}/sprints").json()[0]
    assert (summary["stories"], summary["sessions"], summary["left"]) == (1, 2, d["story_list"][0]["left"])

    with SessionLocal() as db:  # once the sprint is over, an open story is called out
        db.get(models.Sprint, sprint["id"]).ends_on = utcnow().date() - timedelta(days=1)
        db.commit()
    assert client.get(f"/sprints/{sprint['id']}").json()["left_list"][0]["text"] == "KYC-12: not done, and the sprint has ended"


def test_coverage_until_and_project_delete(client, project):
    pid = project["id"]
    c = charter(client, project, "Explore")
    s = client.post("/sessions", json={"charter_id": c["id"], "tester_id": "attu"}).json()
    assert client.get(f"/projects/{pid}/coverage?until={day(-1)}").json()["sessions"] == 0
    assert client.get(f"/projects/{pid}/coverage?since={day(0)}&until={day(0)}").json()["sessions"] == 1
    client.post(f"/sessions/{s['id']}/end")
    client.post(f"/projects/{pid}/sprints", json={"name": "S", "starts_on": day(0), "ends_on": day(1),
                                                  "story_ids": [story(client, project, "Upload")["id"]]})
    out = client.delete(f"/projects/{pid}?confirm=ShopFlow").json()["deleted"]
    assert (out["sprints"], out["stories"]) == (1, 1)


def test_a_database_from_before_sprints_gets_the_column(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'stories.db'}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE stories")  # as the stories step made it
        conn.exec_driver_sql("CREATE TABLE stories (id VARCHAR PRIMARY KEY, project_id VARCHAR NOT NULL, key VARCHAR,"
                             " title VARCHAR NOT NULL, description TEXT, url VARCHAR, tracker VARCHAR, tracker_status VARCHAR,"
                             " closed BOOLEAN, artifact_id VARCHAR, created_at DATETIME, updated_at DATETIME)")
    assert add_missing_columns(engine) == ["stories.sprint_id"]
    assert "sprint_id" in {c["name"] for c in inspect(engine).get_columns("stories")}
