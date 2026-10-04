"""Retest when a ticket closes: check filed issues, replay, comment on the issue."""
import asyncio
import time

import httpx
import pytest

from app import models
from app.config import settings
from app.database import SessionLocal
from app.regression.retest import comment_blocks
from app.trackers import mock as mock_trackers
from app.trackers.github import GitHubTracker
from app.trackers.jira import JiraTracker
from tests.test_regression import App, app_url, bug_present, generate, recorded_bug  # noqa: F401  (fixtures)
from tests.test_runner import chromium_available, headless_runner  # noqa: F401
from tests.test_trackers import reviewed_bug


@pytest.fixture
def mocked(monkeypatch, tmp_path, client, project):
    monkeypatch.setattr(settings, "mock_trackers", True)
    monkeypatch.setattr(settings, "evidence_dir", str(tmp_path / "evidence"))
    mock_trackers.reset()
    client.patch(f"/projects/{project['id']}", json={"github_repo": "addisverify/kyc", "jira_project_key": "KYC"})
    return project


def file(client, bug, tracker="github"):
    r = client.post(f"/bugs/{bug['id']}/file", json={"tracker": tracker})
    assert r.status_code == 200, r.text
    return r.json()["filing"]


def check(client, project, **body):
    r = client.post(f"/projects/{project['id']}/retests/check", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def finished(client, project, retest_id, timeout=120.0):
    deadline = time.monotonic() + timeout
    while True:
        [r] = [x for x in client.get(f"/projects/{project['id']}/retests").json() if x["id"] == retest_id]
        if r["outcome"] != "running":
            return r
        assert time.monotonic() < deadline, r
        time.sleep(0.3)


def github():
    return mock_trackers.MockTracker("github", "addisverify/kyc")


def test_nothing_filed_says_so(client, project):
    r = client.post(f"/projects/{project['id']}/retests/check", json={})
    assert r.status_code == 409 and "filed" in r.json()["detail"]


def test_open_issues_are_only_looked_up_and_a_bug_without_a_test_is_reported(client, session, mocked):
    bug = reviewed_bug(client, session)
    filing = file(client, bug)
    out = check(client, mocked)
    assert (out["checked"], out["filed"], out["retests"]) == (1, 1, [])
    shown = client.get(f"/sessions/{session['id']}/bugs").json()[0]["filings"][0]
    assert (shown["issue_closed"], shown["issue_status"]) == (False, "open") and shown["status_checked_at"]

    github().set_closed(filing["issue_key"])
    [retest] = check(client, mocked)["retests"]
    assert retest["outcome"] == "no_test" and "no regression test" in retest["detail"]
    assert retest["bug_title"] == bug["title"] and not retest["commented"]
    assert github().comments(filing["issue_key"]) == []  # nothing verified, nothing said
    assert check(client, mocked)["retests"] == []  # one retest per close


def test_an_issue_that_cannot_be_looked_up_is_a_problem_not_a_failure(client, session, mocked):
    bug = reviewed_bug(client, session)
    filing = file(client, bug)
    with SessionLocal() as db:
        db.get(models.BugFiling, filing["id"]).issue_key = "#999"
        db.commit()
    out = check(client, mocked)
    assert out["checked"] == 0 and "#999" in out["problems"][0] and "404" in out["problems"][0]


def test_closed_and_still_failing_then_reopened_closed_and_fixed(client, project, recorded_bug, mocked, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    test = generate(client, recorded_bug)
    key = file(client, recorded_bug)["issue_key"]
    check(client, project)  # seen open

    github().set_closed(key)  # closed, but the bug is still there
    [retest] = check(client, project)["retests"]
    assert retest["outcome"] == "running" and retest["test_id"] == test["id"]
    done = finished(client, project, retest["id"])
    assert done["outcome"] == "still_fails" and done["commented"] and done["replay"]["outcome"] == "reproduces"
    [comment] = github().comments(key)
    assert "The bug is still there" in comment and "### What still fails" in comment and "### Steps replayed" in comment

    github().set_closed(key, False)  # reopened, fixed, closed again
    check(client, project)
    App.fixed = True
    github().set_closed(key)
    [again] = check(client, project)["retests"]
    done = finished(client, project, again["id"])
    assert done["outcome"] == "fixed" and done["commented"]
    assert "It passes: the fix is verified" in github().comments(key)[-1]
    assert "test.fail()" in github().comments(key)[-1]  # the known-bug mark can go
    assert [r["outcome"] for r in client.get(f"/projects/{project['id']}/retests").json()] == ["fixed", "still_fails"]
    assert client.get(f"/projects/{project['id']}/regression-tests").json()[0]["outcome"] == "passes"  # the test agrees


def test_an_address_that_is_down_could_not_check_and_says_so(client, project, recorded_bug, mocked, monkeypatch):
    monkeypatch.setenv("MIRMERA_SECRET_PASSWORD", "s3cret-pass")
    generate(client, recorded_bug)
    key = file(client, recorded_bug)["issue_key"]
    github().set_closed(key)
    [retest] = check(client, project, base_url="http://127.0.0.1:9")["retests"]
    done = finished(client, project, retest["id"])
    assert done["outcome"] == "could_not_check" and "step 1" in done["detail"]
    assert "couldn't tell whether the bug is fixed" in github().comments(key)[0]


def test_deleting_the_bug_takes_its_retests(client, session, mocked):
    bug = reviewed_bug(client, session)
    github().set_closed(file(client, bug)["issue_key"])
    check(client, mocked)
    assert client.delete(f"/bugs/{bug['id']}").status_code == 200
    assert client.get(f"/projects/{mocked['id']}/retests").json() == []


def test_comment_for_a_fix_lists_what_was_checked():
    replay = {"base_url": "https://staging.example.com", "steps": [],
              "assertions": [{"kind": "no_failed_request", "why": "Verify succeeds", "status": "passed", "detail": ""}]}
    text = comment_blocks("fixed", replay, "passport.spec.ts", False, "")
    assert "on https://staging.example.com" in text[0].text and text[1].items == ["Verify succeeds: passed"]


def test_real_trackers_read_one_issue(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/addisverify/kyc/issues/7":
            return httpx.Response(200, json={"number": 7, "title": "T", "html_url": "u", "state": "closed", "body": ""})
        if request.url.path == "/rest/api/3/issue/KYC-7":
            assert request.url.params["fields"] == "summary,status,description"
            return httpx.Response(200, json={"key": "KYC-7", "fields": {
                "summary": "T", "status": {"name": "Done", "statusCategory": {"key": "done"}}}})
        return httpx.Response(404, json={"message": "nope"})

    transport = httpx.MockTransport(handler)
    gh = asyncio.run(GitHubTracker("addisverify/kyc", "t", transport=transport).issue("#7"))
    jira = asyncio.run(JiraTracker("https://x.atlassian.net", "e", "t", "KYC", "Bug", transport=transport).issue("KYC-7"))
    assert (gh.closed, gh.status) == (True, "closed")
    assert (jira.closed, jira.status) == (True, "Done")


def test_mock_issues_can_be_closed_through_the_api_only_when_mocked(client, session, mocked, monkeypatch):
    key = file(client, reviewed_bug(client, session))["issue_key"]
    body = {"tracker": "github", "target": "addisverify/kyc", "issue_key": key}
    assert client.post("/mock-trackers/issue-state", json=body).status_code == 200
    assert github()._get(key).closed
    assert client.post("/mock-trackers/issue-state", json=body | {"issue_key": "#99"}).status_code == 404
    monkeypatch.setattr(settings, "mock_trackers", False)
    assert client.post("/mock-trackers/issue-state", json=body).status_code == 404
