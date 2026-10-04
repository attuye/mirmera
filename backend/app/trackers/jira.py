"""Jira Cloud through the REST API v3 (descriptions in Atlassian Document Format).

Search uses POST /rest/api/3/search/jql: the older /rest/api/3/search was
removed from Jira Cloud, and the new one returns only the fields requested."""
import base64
import re

import httpx

from app.trackers.base import TIMEOUT, IssueTracker, TrackerError, TrackerIssue, failure_message
from app.trackers.report import Block, adf_text, to_adf


class JiraTracker(IssueTracker):
    name = "jira"
    supports_attachments = True

    def __init__(self, base_url: str, email: str, token: str, project_key: str, issue_type: str = "Bug",
                 transport: httpx.AsyncBaseTransport | None = None):
        self.target = project_key
        self.base_url = base_url.rstrip("/")
        self.issue_type = issue_type
        auth = base64.b64encode(f"{email}:{token}".encode()).decode()
        self._client_args = dict(
            base_url=self.base_url, timeout=TIMEOUT, transport=transport,
            headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
        )

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        async with httpx.AsyncClient(**self._client_args) as client:
            try:
                r = await client.request(method, path, **kwargs)
            except httpx.HTTPError as err:
                raise TrackerError(f"Could not reach Jira: {err}") from err
        if r.status_code >= 400:
            raise TrackerError(failure_message("Jira", r))
        return r.json() if r.content else {}

    def _url(self, key: str) -> str:
        return f"{self.base_url}/browse/{key}"

    def _check_key(self, key: str) -> str:
        if not re.fullmatch(rf"{re.escape(self.target)}-\d+", key):
            raise TrackerError(f"'{key}' is not an issue in Jira project {self.target}")
        return key

    async def search(self, words: list[str], limit: int = 8) -> list[TrackerIssue]:
        if not words:
            return []
        terms = " ".join(re.sub(r'["\\]', "", w) for w in words)
        jql = f'project = "{self.target}" AND text ~ "{terms}"'
        data = await self._call("POST", "/rest/api/3/search/jql", json={
            "jql": jql, "maxResults": limit, "fields": ["summary", "status", "description"],
        })
        return [self._issue(item) for item in data.get("issues", [])]

    def _issue(self, item: dict) -> TrackerIssue:
        f = item.get("fields", {})
        status = f.get("status") or {}
        return TrackerIssue(
            key=item["key"], title=f.get("summary", ""), url=self._url(item["key"]),
            status=status.get("name", "unknown"),
            closed=(status.get("statusCategory") or {}).get("key") == "done",
            snippet=adf_text(f.get("description")).strip()[:300],
        )

    async def list_issues(self, jql: str, limit: int = 50) -> list[dict]:
        """Issues matching a JQL query, with their description as plain text."""
        data = await self._call("POST", "/rest/api/3/search/jql", json={
            "jql": jql, "maxResults": min(limit, 100), "fields": ["summary", "status", "description", "issuetype"],
        })
        out = []
        for item in data.get("issues", []):
            f = item.get("fields", {})
            out.append({"key": item["key"], "title": f.get("summary", ""), "status": (f.get("status") or {}).get("name", ""),
                        "body": adf_text(f.get("description")).strip(), "url": self._url(item["key"]), "labels": []})
        return out

    async def issue(self, key: str) -> TrackerIssue:
        key = self._check_key(key)
        return self._issue(await self._call("GET", f"/rest/api/3/issue/{key}",
                                            params={"fields": "summary,status,description"}))

    async def create(self, title: str, blocks: list[Block], labels: list[str]) -> TrackerIssue:
        data = await self._call("POST", "/rest/api/3/issue", json={"fields": {
            "project": {"key": self.target}, "issuetype": {"name": self.issue_type},
            "summary": title[:254], "description": to_adf(blocks), "labels": labels,
        }})
        return TrackerIssue(key=data["key"], title=title, url=self._url(data["key"]), status="new", closed=False)

    async def comment(self, key: str, blocks: list[Block]) -> str:
        key = self._check_key(key)
        await self._call("POST", f"/rest/api/3/issue/{key}/comment", json={"body": to_adf(blocks)})
        return self._url(key)

    async def attach(self, key: str, filename: str, content: bytes, mime: str) -> None:
        key = self._check_key(key)
        await self._call("POST", f"/rest/api/3/issue/{key}/attachments",
                         headers={"X-Atlassian-Token": "no-check"}, files={"file": (filename, content, mime)})
