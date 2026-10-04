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
    glance = {g["title"]: g for g in report["glance"]}
    assert list(glance) == ["1 high bug still open", "1 high risk not tested", "1 charter partly fulfilled", "1 report waiting for review"]
    assert glance["1 high bug still open"]["detail"] == "Passport verify fails" and glance["1 high bug still open"]["to"] == "/bugs"
    assert glance["1 high risk not tested"]["detail"] == "R2 Selfie upload (score 12)"
    assert glance["1 charter partly fulfilled"]["to"] == f"/sessions/{s['id']}/debrief"
    assert report["charts"]["severity"] == {"high": 1, "medium": 0, "low": 0}
    assert report["charts"]["risks"] == {"tested": 1, "untested": 1, "untested_high": 1}
    assert report["charts"]["time"]["split"] == {"on_charter": 0.5, "bug_investigation": 0.5}
    assert report["top_bugs"]["total"] == 2 and report["top_bugs"]["rows"][0][0] == "Passport verify fails"
    quality = report["sections"][2]
    assert (quality["key"], quality["open"]) == ("quality", True)
    assert [(pt["title"], pt["open"]) for pt in quality["parts"]] == [
        ("Gaps", True), ("Obstacles", False), ("Open questions", False), ("Testing still to do", False), ("How the testers felt", False)]
    gaps = quality["parts"][0]["blocks"][0]["items"]
    assert gaps[0] == {"text": "High risk not tested: R2 Selfie upload (score 12)", "label": "Make a charter", "to": "/risks"}
    assert report["sections"][1]["counts"] == "1 session · 1 h 00 min · 1 tester"
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


def test_summary_view_exports_are_short_and_links_need_the_app_address(client, project, session, monkeypatch):
    from app.config import settings
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Checkout total wrong", "severity": "high"}).json()
    client.patch(f"/bugs/{bug['id']}", json={"kind": "bug", "expected": "100", "actual": "90",
                                              "oracle_heuristic": "claims", "oracle_explanation": "Spec says 100"})
    r = client.get(f"/projects/{project['id']}/report.md", params={"view": "summary"})
    assert 'filename="shopflow-test-report-summary.md"' in r.headers["content-disposition"]
    md = r.text
    assert md.startswith("# Test report: ShopFlow (summary)")
    assert "## At a glance" in md and "- **1 high bug still open**: Checkout total wrong" in md
    assert "- Bugs by severity: 1 high, 0 medium, 0 low" in md and "## Top bugs" in md
    assert "## 2. The testing" not in md  # the full report only
    assert "(Bugs](" not in md and "[Bugs](" not in md  # no app address: no links
    monkeypatch.setattr(settings, "public_app_url", "https://mirmera.example/")
    md = client.get(f"/projects/{project['id']}/report.md", params={"view": "summary"}).text
    assert f"([Bugs](https://mirmera.example/projects/{project['id']}/bugs))" in md
    full = client.get(f"/projects/{project['id']}/report.html").text
    assert "<h2>At a glance</h2>" in full and "2. The testing: how we tested" in full
    assert "Top bugs" in client.get(f"/projects/{project['id']}/report.html", params={"view": "summary"}).text
    assert client.get(f"/projects/{project['id']}/report.md", params={"view": "short"}).status_code == 422


def test_glance_says_when_a_fix_held_or_not(client, project, session):
    from datetime import timedelta as td
    a = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Fixed one", "severity": "high"}).json()
    b = client.post(f"/sessions/{session['id']}/bugs", json={"title": "Still broken", "severity": "medium"}).json()
    for x in (a, b):
        client.patch(f"/bugs/{x['id']}", json={"kind": "bug", "expected": "e", "actual": "a", "oracle_heuristic": "claims", "oracle_explanation": "x"})
    with SessionLocal() as db:
        for x, outcome, key in ((a, "fixed", "#3"), (b, "still_fails", "#4")):
            f = models.BugFiling(bug_id=x["id"], tracker="github", target="o/r", mode="new", issue_key=key, issue_url=f"https://x/{key[1:]}")
            db.add(f)
            db.flush()
            db.add(models.Retest(project_id=project["id"], bug_id=x["id"], filing_id=f.id, tracker="github", issue_key=key,
                                 issue_url=f.issue_url, outcome=outcome, created_at=utcnow() - td(minutes=1)))
        db.commit()
    glance = {g["title"]: g for g in client.get(f"/projects/{project['id']}/report").json()["glance"]}
    assert "high bug" not in " ".join(glance)  # the high one's fix was verified
    assert glance["1 fix didn't hold: #4 was closed, but the bug still reproduces"]["detail"] == "Still broken"
    assert glance["1 fix verified after the issue closed"]["detail"] == "#3 Fixed one"


def test_nothing_to_flag(client, project, session):
    with SessionLocal() as db:
        s = db.get(models.Session, session["id"])
        s.ended_at = utcnow()
        db.add(models.Debrief(session_id=s.id, time_breakdown={}, charter_status="fulfilled"))
        db.commit()
    [g] = client.get(f"/projects/{project['id']}/report").json()["glance"]
    assert (g["tone"], g["title"]) == ("ok", "Nothing needs attention")


def test_many_running_sessions_are_one_gap(client, project, charter):
    for n in range(3):
        client.post("/sessions", json={"charter_id": charter["id"], "tester_id": f"t{n}"})
    gaps = client.get(f"/projects/{project['id']}/report").json()["sections"][2]["parts"][0]["blocks"][0]["items"]
    running = [g for g in gaps if "still running" in g["text"]]
    assert len(running) == 1 and running[0]["text"].startswith("3 sessions still running: ") and running[0]["to"] == "/dashboard"


# ---------- AI-drafted summary ----------

def reviewed_high_bug(client, session, title="Checkout total wrong"):
    bug = client.post(f"/sessions/{session['id']}/bugs", json={"title": title, "severity": "high"}).json()
    client.patch(f"/bugs/{bug['id']}", json={"kind": "bug", "expected": "100", "actual": "90",
                                              "oracle_heuristic": "claims", "oracle_explanation": "Spec says 100"})
    return bug


def test_facts_are_the_reports_own_lines(client, project, session):
    from app.project_report import build, facts
    reviewed_high_bug(client, session)
    with SessionLocal() as db:
        lines = facts(build(db, db.get(models.Project, project["id"])))
    assert lines[0].startswith("Period: all time") and lines[1].startswith("Totals: 1 session")
    assert "At a glance: 1 high bug still open: Checkout total wrong" in lines
    assert "Bugs by severity: 1 high, 0 medium, 0 low" in lines
    assert any(l.startswith("Gap: ") for l in lines) and "Bug: Checkout total wrong (high, regression test: no test)" in lines


def test_draft_keep_edit_and_remove_a_summary(client, project, session):
    reviewed_high_bug(client, session)
    url = f"/projects/{project['id']}/report/summary"
    draft = client.post(url, json={}).json()
    assert draft["text"].startswith("(mock) 1 high bug still open: Checkout total wrong.")
    assert draft["basis"][0] == "At a glance: 1 high bug still open: Checkout total wrong"
    assert (draft["ai_provider"], draft["prompt_version"]) == ("mock", "report-summary-v1")
    assert client.get(f"/projects/{project['id']}/report").json()["ai_summary"] is None  # a draft isn't kept

    kept = client.put(url, json={"text": draft["text"] + " Retest after the fix.", "ai_provider": "mock",
                                 "ai_model": "mock", "prompt_version": draft["prompt_version"], "edited": True}).json()
    assert kept["edited"] is True
    report = client.get(f"/projects/{project['id']}/report").json()
    assert report["ai_summary"]["text"].endswith("Retest after the fix.")
    md = client.get(f"/projects/{project['id']}/report.md", params={"view": "summary"}).text
    assert "## Summary\n\n(mock) 1 high bug still open" in md and "_Drafted by AI (mock · mock), edited by the tester_" in md
    assert "<h2>Summary</h2>" in client.get(f"/projects/{project['id']}/report.html").text

    # Kept for that period only.
    assert client.get(f"/projects/{project['id']}/report", params={"since": "2026-01-01"}).json()["ai_summary"] is None
    assert client.delete(url).status_code == 204
    assert client.get(f"/projects/{project['id']}/report").json()["ai_summary"] is None


def test_invented_numbers_are_refused(client, project, session, monkeypatch):
    from app.ai.base import LLMProvider
    from app.ai.tasks import ungrounded_numbers

    class Inventive(LLMProvider):
        name, model = "inventive", "x"
        def __init__(self): self.calls = 0
        async def complete(self, system, user, **kw):
            self.calls += 1
            return '{"summary": "Testing found 7 high bugs and 99 percent coverage overall.", "basis": ["made up line"]}'

    assert ungrounded_numbers("2 high bugs in 9 sessions", ["Totals: 9 sessions", "2 high"]) == []
    assert ungrounded_numbers("7 high bugs", ["2 high"]) == ["7"]
    reviewed_high_bug(client, session)
    fake = Inventive()
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name: fake)
    r = client.post(f"/projects/{project['id']}/report/summary", json={})
    assert r.status_code == 502 and "numbers that are not in the report (7, 99)" in r.json()["detail"]
    assert fake.calls == 2  # one retry, told which numbers were invented


def test_basis_keeps_only_real_fact_lines(client, project, session, monkeypatch):
    from app.ai.base import LLMProvider

    class Loose(LLMProvider):
        name, model = "loose", "x"
        async def complete(self, system, user, **kw):
            return ('{"summary": "One high bug is still open: Checkout total wrong.", '
                    '"basis": ["At a glance: 1 high bug still open: Checkout total wrong", "Something the AI imagined"]}')

    reviewed_high_bug(client, session)
    monkeypatch.setattr("app.ai.routing.get_provider", lambda name: Loose())
    draft = client.post(f"/projects/{project['id']}/report/summary", json={}).json()
    assert draft["basis"] == ["At a glance: 1 high bug still open: Checkout total wrong"]
