"""Importing pull requests, past bugs and local diffs as artifacts."""
import asyncio
import subprocess

import httpx
import pytest

from app.config import settings
from app.importers import ImportProblem, parse_pr
from app.trackers import mock as mock_trackers
from app.trackers.github import GitHubTracker
from app.trackers.jira import JiraTracker


@pytest.fixture
def mocked(monkeypatch, client, project):
    monkeypatch.setattr(settings, "mock_trackers", True)
    mock_trackers.reset()
    client.patch(f"/projects/{project['id']}", json={"github_repo": "addisverify/kyc", "jira_project_key": "KYC"})
    return project


def imp(client, project, **body):
    return client.post(f"/projects/{project['id']}/artifacts/import", json=body)


def test_pull_request_references():
    assert parse_pr("42", "a/b") == ("a/b", 42) and parse_pr("#42", "a/b") == ("a/b", 42)
    assert parse_pr("x/y#7", "a/b") == ("x/y", 7)
    assert parse_pr("https://github.com/x/y/pull/9/files", None) == ("x/y", 9)
    with pytest.raises(ImportProblem, match="Set the project's GitHub repo"):
        parse_pr("42", None)
    with pytest.raises(ImportProblem, match="Write the pull request as"):
        parse_pr("the login fix", "a/b")


def test_a_pull_request_becomes_a_diff_artifact(client, mocked):
    r = imp(client, mocked, source="github_pr", ref="#12")
    assert r.status_code == 200, r.text
    a = r.json()
    assert (a["type"], a["label"]) == ("diff", "PR #12: Check passports against the MRZ (mock PR 12)")
    assert a["file_ref"] == "https://mock-github.local/addisverify/kyc/pull/12"
    assert a["ai_summary"]  # summarised like a pasted one
    assert imp(client, mocked, source="github_pr", ref="#5000").status_code == 502  # the tracker's 404, explained


def test_past_bugs_from_github_and_jira(client, mocked):
    gh = imp(client, mocked, source="github_issues", labels="bug").json()
    assert gh["type"] == "bug_import" and gh["label"] == "GitHub issues: addisverify/kyc (bug), 2"
    jira = imp(client, mocked, source="jira_issues").json()
    assert jira["label"].startswith('Jira issues: project = "KYC" AND issuetype = Bug') and jira["label"].endswith(", 2")
    from app.database import SessionLocal
    from app import models
    with SessionLocal() as db:
        content = db.get(models.Artifact, jira["id"]).raw_content
    assert "KYC-2 [closed] Expired national ID accepted at upload — Fixed in 2.3" in content


def test_targets_must_be_set_up(client, project, monkeypatch):
    monkeypatch.setattr(settings, "mock_trackers", True)
    r = imp(client, project, source="github_issues")
    assert r.status_code == 400 and "GitHub repo in Settings" in r.json()["detail"]
    assert "Jira project key" in imp(client, project, source="jira_issues").json()["detail"]


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "T")
    (tmp_path / "verify.py").write_text("def verify():\n    return True\n")
    git("add", "."); git("commit", "-q", "-m", "first")
    git("checkout", "-q", "-b", "passport-mrz")
    (tmp_path / "verify.py").write_text("def verify(doc):\n    return doc.mrz is not None\n")
    git("commit", "-q", "-am", "check the MRZ")
    return tmp_path


def test_a_local_git_diff(client, project, repo):
    a = imp(client, project, source="git_diff", repo_path=str(repo), base="main", head="passport-mrz").json()
    assert (a["type"], a["label"]) == ("diff", f"git diff main...passport-mrz ({repo.name})")
    from app.database import SessionLocal
    from app import models
    with SessionLocal() as db:
        assert "+    return doc.mrz is not None" in db.get(models.Artifact, a["id"]).raw_content
    same = imp(client, project, source="git_diff", repo_path=str(repo), base="passport-mrz", head="passport-mrz")
    assert same.status_code == 400 and "No changes" in same.json()["detail"]
    assert "not a branch" in imp(client, project, source="git_diff", repo_path=str(repo), base="--output=x").json()["detail"]
    assert "No folder" in imp(client, project, source="git_diff", repo_path=str(repo / "nope")).json()["detail"]
    bad = imp(client, project, source="git_diff", repo_path=str(repo), base="no-such-branch")
    assert bad.status_code == 400 and "git diff failed" in bad.json()["detail"]


def test_real_trackers_fetch_a_pull_request_and_issues():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/a/b/pulls/3" and "diff" in request.headers["accept"]:
            return httpx.Response(200, text="diff --git a/x b/x\n+new line\n")
        if path == "/repos/a/b/pulls/3":
            return httpx.Response(200, json={"title": "Fix", "body": None, "html_url": "u", "state": "closed", "merged_at": "2026-10-01"})
        if path == "/repos/a/b/issues":
            assert request.url.params["labels"] == "bug" and request.url.params["state"] == "all"
            return httpx.Response(200, json=[
                {"number": 1, "title": "Bug", "state": "open", "body": "b", "html_url": "u1", "labels": [{"name": "bug"}]},
                {"number": 2, "title": "A PR", "state": "open", "body": "", "html_url": "u2", "labels": [], "pull_request": {}}])
        if path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": [{"key": "KYC-1", "fields": {
                "summary": "S", "status": {"name": "Done"}, "description": {"type": "doc", "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": "Steps"}]}]}}}]})
        return httpx.Response(404, json={"message": "nope"})

    t = httpx.MockTransport(handler)
    pr = asyncio.run(GitHubTracker("a/b", "t", transport=t).pull_request(3))
    assert (pr["state"], pr["body"], pr["diff"].splitlines()[1]) == ("merged", "", "+new line")
    issues = asyncio.run(GitHubTracker("a/b", "t", transport=t).list_issues(["bug"]))
    assert [i["key"] for i in issues] == ["#1"]  # pull requests left out
    jira = asyncio.run(JiraTracker("https://x.atlassian.net", "e", "t", "KYC", transport=t).list_issues("project = KYC"))
    assert jira == [{"key": "KYC-1", "title": "S", "status": "Done", "body": "Steps", "url": "https://x.atlassian.net/browse/KYC-1", "labels": []}]
