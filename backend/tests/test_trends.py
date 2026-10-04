"""Testing trends: week by week, and what changed over the last four weeks."""
from datetime import timedelta

from app import models
from app.database import SessionLocal
from app.timeutil import utcnow
from app.trends import week_start


def session_in(db, charter_id, weeks_ago, minutes, tester="attu", split=None, status=None):
    start = utcnow() - timedelta(weeks=weeks_ago, hours=1)
    s = models.Session(charter_id=charter_id, tester_id=tester, started_at=start, ended_at=start + timedelta(minutes=minutes))
    db.add(s)
    db.flush()
    if split or status:
        db.add(models.Debrief(session_id=s.id, time_breakdown=split or {}, charter_status=status))
    return s


def bug_in(db, s, severity, kind="bug"):
    db.add(models.Bug(session_id=s.id, charter_id=s.charter_id, title=f"{severity} bug", severity=severity, kind=kind,
                      created_at=s.started_at + timedelta(minutes=5)))


def test_week_by_week(client, project, charter):
    risk = client.post(f"/projects/{project['id']}/risks", json={
        "title": "Passport rejected", "failure_mode": "valid passports fail", "quality_criterion": "capability",
        "impact": 4, "likelihood": 4}).json()
    with SessionLocal() as db:
        r = db.get(models.Risk, risk["id"])
        r.created_at = utcnow() - timedelta(weeks=10)
        db.add(models.RiskChange(risk_id=r.id, source="ai", status="accepted", from_impact=4, from_likelihood=4,
                                 to_impact=4, to_likelihood=2, final_impact=4, final_likelihood=2,
                                 decided_at=utcnow() - timedelta(weeks=2)))
        old = session_in(db, charter["id"], 6, 120, split={"on_charter": 0.75, "setup": 0.25}, status="fulfilled")
        bug_in(db, old, "high")
        new1 = session_in(db, charter["id"], 1, 60, tester="sara", status="partially_fulfilled")
        new2 = session_in(db, charter["id"], 1, 60)
        for sev in ("high", "medium", "medium"):
            bug_in(db, new1, sev)
        bug_in(db, new2, "low", kind="question")
        db.commit()

    t = client.get(f"/projects/{project['id']}/trends", params={"weeks": 8}).json()
    assert t["weeks"] == 8 and len(t["rows"]) == 8
    assert t["rows"][-1]["week"] == week_start(utcnow().date()).isoformat()  # the current week is last
    by_week = {r["week"]: r for r in t["rows"]}
    six = by_week[week_start((utcnow() - timedelta(weeks=6, hours=1)).date()).isoformat()]
    assert (six["sessions"], six["hours"], six["bugs"]["high"], six["bugs_per_hour"]) == (1, 2.0, 1, 0.5)
    assert six["time_split"] == {"on_charter": 0.75, "setup": 0.25} and six["charters"]["fulfilled"] == 1
    one = by_week[week_start((utcnow() - timedelta(weeks=1, hours=1)).date()).isoformat()]
    assert (one["sessions"], one["testers"], one["hours"], one["questions"]) == (2, 2, 2.0, 1)
    assert one["bugs"] == {"high": 1, "medium": 2, "low": 0} and one["bugs_per_hour"] == 1.5
    assert one["charters"]["partly"] == 1
    quiet = t["rows"][0]
    assert (quiet["sessions"], quiet["bugs_per_hour"]) == (0, None)  # no testing: no rate

    # Risk exposure follows the accepted re-score: 16 until two weeks ago, then 8.
    assert t["rows"][-4]["risk_exposure"] == 16 and t["rows"][-1]["risk_exposure"] == 8

    changes = {c["name"]: c for c in t["changes"]}
    assert changes["Testing time"]["text"] == "Testing time: no change (2.0 h)"
    assert changes["Bugs found"]["text"] == "Bugs found: 1 → 3 (+200%)"
    assert changes["Bugs per hour of testing"]["direction"] == "up"
    assert changes["Risk exposure"]["text"] == "Risk exposure: 16 → 8 (−50%)"


def test_an_empty_project_and_the_week_range(client, project):
    t = client.get(f"/projects/{project['id']}/trends").json()
    assert t["weeks"] == 12 and all(r["sessions"] == 0 for r in t["rows"]) and t["changes"] == []
    assert t["rows"][0]["risk_exposure"] is None  # no accepted risks
    assert client.get(f"/projects/{project['id']}/trends", params={"weeks": 500}).json()["weeks"] == 52
    assert client.get(f"/projects/{project['id']}/trends", params={"weeks": 1}).json()["weeks"] == 4
