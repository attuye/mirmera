"""The coverage map: pages and product areas reached, and the gaps."""
from datetime import timedelta

from app import models
from app.coverage import page_key
from app.database import SessionLocal
from app.timeutil import utcnow


def test_page_keys_collapse_ids_and_ignore_hosts_and_queries():
    assert page_key("https://staging.kyc.et/orders/42?tab=2#x") == "/orders/:id"
    assert page_key("http://localhost:3000/orders/7f3c2a9e-1b2c-4d5e-8f90-123456789abc/edit") == "/orders/:id/edit"
    assert page_key("https://kyc.et/") == "/" and page_key("https://kyc.et") == "/"
    assert page_key("https://kyc.et/docs/passport/") == "/docs/passport"
    assert page_key("https://kyc.et/u/a1B2c3D4e5F6g7H8") == "/u/:id"
    assert page_key("https://kyc.et/settings-and-preferences") == "/settings-and-preferences"  # long, but a word
    assert page_key("about:blank") is None and page_key(None) is None


def add(db, session_id, kind, url, minutes_ago, summary=None, data=None, count=1):
    ts = utcnow() - timedelta(minutes=minutes_ago)
    db.add(models.Evidence(session_id=session_id, kind=kind, url=url, summary=summary or url, data=data, count=count,
                           captured_at=ts, last_seen_at=ts))


def test_the_map_from_recorded_sessions(client, project, charter, session):
    base = "https://staging.kyc.et"
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Verify fails for passports", "severity": "high"}).json()
    other = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "sara"}).json()
    with SessionLocal() as db:
        s = session["id"]
        add(db, s, "navigation", f"{base}/", 30)
        add(db, s, "navigation", f"{base}/verify/12", 29)
        add(db, s, "action", f"{base}/verify/12", 28, data={"action": "fill", "locator": {"method": "label", "value": "Full name"}, "value": "Abebe"})
        add(db, s, "action", f"{base}/verify/12", 27, data={"action": "click", "locator": {"method": "role", "role": "button", "name": "Verify"}}, count=3)
        add(db, s, "failed_request", f"{base}/api/verify/12", 26, summary=f"POST {base}/api/verify/12 → 500", count=2)
        add(db, s, "navigation", f"{base}/help", 20)
        add(db, s, "console_error", f"{base}/help", 19, summary="TypeError: x is undefined\n at help.js:1")
        b = db.get(models.Bug, bug["id"])
        b.created_at = utcnow() - timedelta(minutes=25)  # logged while on the verify page
        add(db, other["id"], "navigation", "http://localhost:3000/verify/99", 3000 * 24 * 60 // 100)  # ~30 days ago
        db.commit()

    m = client.get(f"/projects/{project['id']}/coverage").json()
    assert (m["sessions"], m["recorded_sessions"]) == (2, 2)
    pages = {p["path"]: p for p in m["pages"]}
    assert list(pages) == ["/", "/help", "/verify/:id"]

    verify = pages["/verify/:id"]
    assert (verify["sessions"], verify["testers"], verify["visits"], verify["actions"]) == (2, ["attu", "sara"], 2, 4)
    assert verify["hosts"] == ["localhost:3000", "staging.kyc.et"]
    assert verify["controls"] == ['click button "Verify"', 'fill label "Full name"']  # never the value typed
    assert verify["problems"] == [{"summary": "POST /api/verify/:id → 500", "count": 2}]
    assert [b["title"] for b in verify["bugs"]] == ["Verify fails for passports"]
    assert verify["flags"] == []

    assert pages["/"]["flags"] == ["not_tried", "one_tester"]
    assert pages["/help"]["flags"] == ["not_tried", "problems_without_bug", "one_tester"]
    assert pages["/help"]["problems"][0]["summary"] == "TypeError: x is undefined"
    assert m["gaps"][0] == "/help: TypeError: x is undefined (x1) was recorded but no bug was logged there"
    assert "/: opened but nothing was tried on it" in m["gaps"]


def test_stale_pages_and_the_since_filter(client, project, session):
    with SessionLocal() as db:
        add(db, session["id"], "navigation", "https://kyc.et/old", 20 * 24 * 60)
        db.get(models.Session, session["id"]).started_at = utcnow() - timedelta(days=21)
        db.commit()
    m = client.get(f"/projects/{project['id']}/coverage").json()
    assert "stale" in m["pages"][0]["flags"] and any(g.startswith("/old: last visited") for g in m["gaps"])
    since = (utcnow() - timedelta(days=7)).date()
    assert client.get(f"/projects/{project['id']}/coverage", params={"since": str(since)}).json()["pages"] == []


def test_product_areas_covered_through_charter_risks(client, project):
    with SessionLocal() as db:
        db.add(models.ProductModel(project_id=project["id"], summary="KYC", elements=[
            {"category": "function", "name": "Passport verification", "description": "MRZ check"},
            {"category": "function", "name": "Selfie liveness"},
            {"category": "data", "name": "Expiry dates"}]))
        db.commit()
    passport = client.post(f"/projects/{project['id']}/risks", json={
        "title": "Passport rejected", "failure_mode": "valid passports fail", "quality_criterion": "capability",
        "elements": ["passport verification"], "impact": 4, "likelihood": 4}).json()
    client.post(f"/projects/{project['id']}/risks", json={
        "title": "Liveness spoofed", "failure_mode": "photos pass", "quality_criterion": "security",
        "elements": ["Selfie liveness"], "impact": 5, "likelihood": 3})
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore passports", "risk_ids": [passport["id"]]}).json()
    client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"})

    m = client.get(f"/projects/{project['id']}/coverage").json()
    areas = {a["name"]: a for a in m["areas"]}
    assert areas["Passport verification"]["sessions"] == 1 and areas["Passport verification"]["risks"] == ["R1 Passport rejected"]
    assert areas["Selfie liveness"]["sessions"] == 0
    assert "Product area not explored: Selfie liveness (function): no session on R2 Liveness spoofed" in m["gaps"]
    assert "Product area not explored: Expiry dates (data): no risk names it" in m["gaps"]


def test_no_recordings_yet(client, project):
    m = client.get(f"/projects/{project['id']}/coverage").json()
    assert (m["pages"], m["areas"], m["gaps"], m["recorded_sessions"]) == ([], [], [], 0)
