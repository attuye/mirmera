"""The project test report: the product, the testing, how good the testing was."""
from datetime import timedelta

from app import models
from app.database import SessionLocal
from app.project_report import hours, period_bounds, to_html, to_markdown
from app.timeutil import utcnow


def risk(client, project, title, impact, likelihood):
    r = client.post(f"/projects/{project['id']}/risks", json={
        "title": title, "failure_mode": f"{title} fails", "quality_criterion": "capability",
        "impact": impact, "likelihood": likelihood})
    assert r.status_code == 200, r.text
    return r.json()


def test_hours_and_whole_day_periods():
    assert hours(59 * 60) == "59 min" and hours(95 * 60) == "1 h 35 min"
    from datetime import date
    start, end = period_bounds(date(2026, 10, 1), date(2026, 10, 3))
    assert (start.day, end.day) == (1, 4)  # the last day is included


def test_empty_project_says_so(client, project):
    r = client.get(f"/projects/{project['id']}/report").json()
    assert r["summary"]["sessions"] == 0
    texts = [b.get("text", "") for b in r["blocks"]]
    assert "No sessions in this period." in texts and "No bugs or questions were reviewed in this period." in texts


def test_the_three_stories_from_what_was_recorded(client, project):
    passport = risk(client, project, "Passport verification", 4, 4)  # 16: high
    upload = risk(client, project, "Selfie upload", 4, 3)  # 12: high, never tested
    charter = client.post(f"/projects/{project['id']}/charters", json={
        "mission_statement": "Explore passport verification", "risk_ids": [passport["id"]]}).json()
    s = client.post("/sessions", json={"charter_id": charter["id"], "tester_id": "attu"}).json()
    bug = client.post(f"/sessions/{s['id']}/bugs", json={"title": "Passport verify fails", "severity": "high"}).json()
    client.patch(f"/bugs/{bug['id']}", json={
        "kind": "bug", "expected": "Verified", "actual": "Error", "reproducibility": "always",
        "oracle_heuristic": "product", "oracle_explanation": "ID works", "open_questions": ["Only on staging?"]})
    question = client.post(f"/sessions/{s['id']}/bugs", json={"title": "Is the 5 MB limit right?", "severity": "low"}).json()
    client.patch(f"/bugs/{question['id']}", json={"kind": "question", "actual": "Rejected at 5.1 MB"})
    client.post(f"/sessions/{s['id']}/bugs", json={"title": "Odd spinner"})  # not reviewed
    with SessionLocal() as db:
        row = db.get(models.Session, s["id"])
        row.started_at = utcnow() - timedelta(minutes=80)
        row.ended_at = utcnow() - timedelta(minutes=5)
        row.total_paused_seconds = 15 * 60
        db.add(models.Debrief(session_id=s["id"], time_breakdown={"on_charter": 0.5, "bug_investigation": 0.5},
                              charter_status="partially_fulfilled", charter_status_rationale="Ran out of time on renewals",
                              coverage_summary="Passport and ID flows on Chrome", obstacles=["Staging was down for 10 minutes"],
                              open_questions=["Which passports must pass?"], tester_feelings="Uneasy about renewals",
                              outlook=[{"charter": "Explore passport renewals", "reason": "not reached"}]))
        db.commit()

    r = client.get(f"/projects/{project['id']}/report")
    assert r.status_code == 200
    report = r.json()
    sm = report["summary"]
    assert (sm["sessions"], sm["tested_seconds"], sm["bugs"], sm["high"], sm["questions"], sm["unreviewed"]) == (1, 3600, 1, 1, 1, 1)

    blocks = report["blocks"]
    assert blocks[0]["text"].startswith("1 session of exploratory testing (1 h 00 min) by 1 tester")
    tables = [b for b in blocks if b["type"] == "table"]
    found = tables[0]
    assert [row[0] for row in found["rows"]] == ["Passport verify fails", "Is the 5 MB limit right?"]  # bugs before questions
    assert found["rows"][0][1:5] == ["high", "always", ["not filed"], "no test"]
    risks = tables[1]
    assert risks["rows"][0][:4] == ["R1 Passport verification", "16 (4x4)", "capability", "1 session"]
    assert risks["rows"][0][4] == "1"
    assert risks["rows"][1][3] == "not tested"
    sessions = tables[2]
    assert sessions["rows"][0][:2] == ["Explore passport verification", "attu"] and sessions["rows"][0][4] == "Partly fulfilled"

    md = to_markdown(report_obj(client, project))
    assert "## 1. The product: what we found" in md and "## 3. How good the testing was" in md
    assert "- High risk not tested: R2 Selfie upload (score 12)" in md
    assert "Charter partly fulfilled: Explore passport verification: Ran out of time on renewals" in md
    assert "- 1 bug report not reviewed" in md and "1 bug without a regression test" in md
    assert "- Staging was down for 10 minutes (attu, Explore passport verification)" in md
    assert "- Which passports must pass?" in md and "- Only on staging? (bug: Passport verify fails)" in md
    assert "- Explore passport renewals (not reached)" in md
    assert "> Uneasy about renewals" in md
    assert "on charter 50%, bug investigation 50%" in md
    assert upload["ref"] == "R2"


def report_obj(client, project):
    from app.project_report import build
    with SessionLocal() as db:
        return build(db, db.get(models.Project, project["id"]))


def test_a_period_leaves_out_sessions_outside_it(client, project, session):
    with SessionLocal() as db:
        db.get(models.Session, session["id"]).started_at = utcnow() - timedelta(days=10)
        db.commit()
    today = utcnow().date()
    r = client.get(f"/projects/{project['id']}/report", params={"since": str(today - timedelta(days=2)), "until": str(today)}).json()
    assert r["summary"]["sessions"] == 0 and r["period"] == f"{today - timedelta(days=2)} to {today}"
    assert client.get(f"/projects/{project['id']}/report", params={"since": str(today), "until": str(today - timedelta(days=1))}).status_code == 422


def test_markdown_download_and_printable_page(client, project, session):
    client.post(f"/sessions/{session['id']}/bugs", json={"title": "Coupon | total wrong"})
    md = client.get(f"/projects/{project['id']}/report.md", params={"since": "2026-01-01"})
    assert md.headers["content-type"].startswith("text/markdown")
    assert 'filename="shopflow-test-report-2026-01-01-to-' in md.headers["content-disposition"]
    assert md.text.startswith("# Test report: ShopFlow")
    page = client.get(f"/projects/{project['id']}/report.html")
    assert page.headers["content-type"].startswith("text/html")
    assert "<title>Test report: ShopFlow</title>" in page.text and "@media print" in page.text


def test_cells_are_escaped_and_issues_link():
    report = {"project_name": "<b>X</b>", "period": "all time", "generated_at": utcnow(), "blocks": [
        {"type": "table", "headers": ["Bug", "Issue"], "rows": [["a | b", [{"text": "#3 (closed)", "href": "https://x/3"}]]]}]}
    assert "| a \\| b | [#3 (closed)](https://x/3) |" in to_markdown(report)
    page = to_html(report)
    assert "&lt;b&gt;X&lt;/b&gt;" in page and '<a href="https://x/3">#3 (closed)</a>' in page


def test_filed_issues_show_closed_and_the_retest(client, project, session, monkeypatch, tmp_path):
    from app.config import settings
    from app.trackers import mock as mock_trackers
    from tests.test_trackers import reviewed_bug
    monkeypatch.setattr(settings, "mock_trackers", True)
    monkeypatch.setattr(settings, "evidence_dir", str(tmp_path / "evidence"))
    mock_trackers.reset()
    client.patch(f"/projects/{project['id']}", json={"github_repo": "addisverify/kyc"})
    bug = reviewed_bug(client, session)
    filing = client.post(f"/bugs/{bug['id']}/file", json={"tracker": "github"}).json()["filing"]
    mock_trackers.MockTracker("github", "addisverify/kyc").set_closed(filing["issue_key"])
    client.post(f"/projects/{project['id']}/retests/check", json={})
    report = client.get(f"/projects/{project['id']}/report").json()
    row = [b for b in report["blocks"] if b["type"] == "table"][0]["rows"][0]
    assert row[3] == [{"text": f"{filing['issue_key']} (closed)", "href": filing["issue_url"]}]
    assert row[5] == "No test"
    assert (report["summary"]["filed"], report["summary"]["closed"]) == (1, 1)
