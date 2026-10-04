"""Slow requests and accessibility findings: evidence the AI sees, coverage, nudges."""
from datetime import timedelta

from app import models
from app.database import SessionLocal
from app.evidence_text import browser_context
from app.timeutil import utcnow


def add(db, session_id, kind, summary, url="https://kyc.et/verify", minutes_ago=5, count=1):
    ts = utcnow() - timedelta(minutes=minutes_ago)
    db.add(models.Evidence(session_id=session_id, kind=kind, summary=summary, url=url, count=count,
                           captured_at=ts, last_seen_at=ts))


def seeded(client, session):
    with SessionLocal() as db:
        add(db, session["id"], "navigation", "https://kyc.et/verify", minutes_ago=8)
        add(db, session["id"], "accessibility", "Form field without a label: input#phone")
        add(db, session["id"], "accessibility", "Low contrast text (1.9:1, needs 4.5:1): \"Fine print\"")
        add(db, session["id"], "slow_request", "GET https://kyc.et/api/verify took longer than 2 s", count=3)
        db.commit()


def test_the_ai_sees_them_as_problems_the_page_reported(client, session):
    seeded(client, session)
    with SessionLocal() as db:
        rows = db.query(models.Evidence).filter_by(session_id=session["id"]).order_by(models.Evidence.captured_at).all()
        text = browser_context(rows, rows[0].captured_at, used=True)
    assert text.startswith("Recorded: 3 slow requests, 2 accessibility findings; 1 page visited.")
    assert "[slow request ×3] GET https://kyc.et/api/verify took longer than 2 s" in text
    assert "[accessibility finding] Form field without a label: input#phone" in text


def test_coverage_lists_them_but_only_errors_count_as_problems_without_a_bug(client, project, session):
    seeded(client, session)
    [page] = client.get(f"/projects/{project['id']}/coverage").json()["pages"]
    kinds = {p["kind"] for p in page["problems"]}
    assert kinds == {"slow_request", "accessibility"}
    assert "problems_without_bug" not in page["flags"]


def test_nudges_for_slowness_and_one_for_accessibility(client, session):
    seeded(client, session)
    nudges = {n["title"]: n for n in client.get(f"/sessions/{session['id']}/nudges").json()}
    slow = nudges["Investigate: GET https://kyc.et/api/verify took longer than 2 s"]
    assert slow["heuristic"] == "Performance: is it slow for users?" and "3 times" in slow["why"]
    a11y = nudges["Accessibility: 2 findings recorded"]
    assert a11y["why"].startswith("e.g. Form field without a label: input#phone")
    assert a11y["suggestions"][0].startswith("Use the main form with the keyboard only")
    client.post(f"/sessions/{session['id']}/notes", json={"tag": "test", "content": "Keyboard only: the form works"})
    assert "Accessibility: 2 findings recorded" not in {n["title"] for n in client.get(f"/sessions/{session['id']}/nudges").json()}
