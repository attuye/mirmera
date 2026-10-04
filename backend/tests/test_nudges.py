"""Test-idea nudges: specific suggestions from what the session has done."""
from datetime import timedelta

from app import models
from app.database import SessionLocal
from app.nudges import field_type, untried_variants
from app.timeutil import utcnow

BASE = "https://staging.kyc.et"


def act(db, session_id, action, name, value=None, minutes_ago=10, method="label", **extra):
    loc = {"method": "role", "role": "button", "name": name} if method == "role" else {"method": method, "value": name}
    data = {"action": action, "locator": loc, **({"value": value} if value is not None else {}), **extra}
    ts = utcnow() - timedelta(minutes=minutes_ago)
    db.add(models.Evidence(session_id=session_id, kind="action", url=f"{BASE}/verify", summary=action, data=data,
                           captured_at=ts, last_seen_at=ts))


def nudges(client, session):
    r = client.get(f"/sessions/{session['id']}/nudges")
    assert r.status_code == 200, r.text
    return r.json()


def test_field_types_and_what_is_left_to_try():
    assert field_type("Expiry date", ["2030-01-01"]) == "date"
    assert field_type("Contact", ["a@b.co"]) == "email"
    assert field_type("Mobile number", ["0912"]) == "phone"
    assert field_type("Quantity", ["3"]) == "number"
    assert field_type("Full name", ["Abebe Kebede"]) == "text"
    todo = untried_variants("text", ["Abebe Kebede", ""])
    assert "leave it empty" not in todo and "non-Latin script and accents: አበበ ከበደ, Zoë" in todo
    assert "non-Latin script and accents: አበበ ከበደ, Zoë" not in untried_variants("text", ["አበበ"])
    assert "a negative number (-1)" not in untried_variants("number", ["-5"])


def test_a_fresh_session_has_only_charter_ideas(client, project):
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore passports", "test_ideas": ["Renew an expired passport"]}).json()
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    [idea] = nudges(client, s)
    assert (idea["kind"], idea["suggestions"]) == ("idea", ["Renew an expired passport"])
    client.post(f"/sessions/{s['id']}/notes", json={"tag": "test", "content": "Tried renewing an expired passport: works"})
    assert nudges(client, s) == []  # a note mentions it


def test_nudges_from_what_was_recorded(client, project, session):
    risk = client.post(f"/projects/{project['id']}/risks", json={
        "title": "Passport rejected", "failure_mode": "Valid passports are rejected at verification",
        "quality_criterion": "capability", "impact": 4, "likelihood": 4}).json()
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore passport verification", "risk_ids": [risk["id"]]}).json()
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    with SessionLocal() as db:
        act(db, s["id"], "fill", "Full name", "Abebe Kebede")
        act(db, s["id"], "fill", "Password", None, masked=True, secret="MIRMERA_SECRET_PASSWORD")
        act(db, s["id"], "fill", "Expiry date", "2030-01-01")
        act(db, s["id"], "select", "Document type", "Passport")
        act(db, s["id"], "upload", "Photo", files=["id.jpg"])
        act(db, s["id"], "click", "Verify", method="role")
        ts = utcnow() - timedelta(minutes=5)
        db.add(models.Evidence(session_id=s["id"], kind="failed_request", url=f"{BASE}/api/verify",
                               summary=f"POST {BASE}/api/verify/12 → 500", count=2, captured_at=ts, last_seen_at=ts))
        db.commit()

    found = nudges(client, s)
    kinds = [n["kind"] for n in found]
    assert kinds[:2] == ["problem", "risk"]  # most useful first
    assert found[0]["title"] == "Investigate: POST /api/verify/:id → 500" and "recorded this 2 times" in found[0]["why"]
    assert found[1]["suggestions"] == ["Valid passports are rejected at verification"]
    titles = {n["title"]: n for n in found}
    assert 'Vary "Password"' not in titles  # secrets are never suggested
    name = titles['Vary "Full name"']
    assert name["why"] == 'You\'ve tried "Abebe Kebede". It looks like a text field.'
    assert name["suggestions"][0] == "leave it empty" and len(name["suggestions"]) == 5
    assert titles['Vary "Expiry date"']["suggestions"][1] == "a date in the past (an expired document?)"
    assert titles['Other choices in "Document type"']["why"] == "You've chosen Passport."
    assert 'Odd files for "Photo"' in titles and 'Interrupt "Verify"' in titles
    assert len(found) == 7  # one per thing worth trying, no repeats

    # A bug logged after the failure covers it.
    client.post(f"/sessions/{s['id']}/bugs", json={"title": "Passport verification fails"})
    assert "problem" not in [n["kind"] for n in nudges(client, s)]


def test_not_now_and_tried_it(client, project):
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore uploads", "test_ideas": ["Upload a HEIC photo", "Upload while offline"]}).json()
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    first, second = nudges(client, s)
    assert client.post(f"/sessions/{s['id']}/nudges/{first['id']}/dismiss").status_code == 204
    assert [n["id"] for n in nudges(client, s)] == [second["id"]]
    note = client.post(f"/sessions/{s['id']}/nudges/{second['id']}/tried", json={"note": "Tried: offline upload shows a retry"}).json()
    assert (note["tag"], note["content"]) == ("test", "Tried: offline upload shows a retry")
    assert nudges(client, s) == []
    assert client.post(f"/sessions/{s['id']}/nudges/x/tried", json={"note": ""}).status_code == 422


def test_half_time_with_nothing_found_and_pages_nobody_tried(client, project, charter, session):
    other = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "sara"}).json()
    with SessionLocal() as db:
        ts = utcnow() - timedelta(minutes=50)
        db.add(models.Evidence(session_id=other["id"], kind="navigation", url=f"{BASE}/help", summary="x", captured_at=ts, last_seen_at=ts))
        db.get(models.Session, session["id"]).started_at = utcnow() - timedelta(minutes=50)
        db.commit()
    found = {n["kind"]: n for n in nudges(client, session)}
    assert found["page"]["suggestions"] == ["/help"]
    assert found["time"]["why"] == "50 of 90 minutes, and no bug, issue or question logged."
    client.post(f"/sessions/{session['id']}/notes", json={"tag": "question", "content": "Is a 90-day expiry allowed?"})
    assert "time" not in [n["kind"] for n in nudges(client, session)]


def test_an_ended_session_has_no_nudges(client, session):
    with SessionLocal() as db:
        db.get(models.Session, session["id"]).ended_at = utcnow()
        db.commit()
    assert nudges(client, session) == []


def test_a_note_about_one_idea_does_not_hide_a_similar_one(client, project):
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore uploads", "test_ideas": ["Upload a HEIC photo", "Upload while offline"]}).json()
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    client.post(f"/sessions/{s['id']}/notes", json={"tag": "test", "content": "Tried Upload a HEIC photo: rejected clearly"})
    assert [n["suggestions"] for n in nudges(client, s)] == [["Upload while offline"]]
