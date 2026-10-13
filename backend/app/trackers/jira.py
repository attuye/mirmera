"""Jira Cloud through the REST API v3 (descriptions in Atlassian Document Format).

Search uses POST /rest/api/3/search/jql: the older /rest/api/3/search was
removed from Jira Cloud, and the new one returns only the fields requested.

The sprint is a custom field whose id differs between sites (customfield_10020 on most
Cloud sites). The client asks Jira once which field is the sprint (its schema is
"com.pyxis.greenhopper.jira:gh-sprint") and reads that one."""
import base64
import re

import httpx

from app.trackers.base import TIMEOUT, IssueTracker, TrackerError, TrackerIssue, failure_message
from app.trackers.report import Block, adf_text, to_adf


SPRINT_SCHEMA = "com.pyxis.greenhopper.jira:gh-sprint"
DEFAULT_SPRINT_FIELD = "customfield_10020"
_SPRINT_FIELDS: dict[str, str] = {}  # site address -> its sprint field id, asked once per server start
_ORDER = {"active": 0, "future": 1, "closed": 2}


def _legacy_sprint(text: str) -> dict:
    """Jira Server/Data Center's older form: "com.atlassian.greenhopper.service.sprint.Sprint@1a[id=7,state=ACTIVE,name=Sprint 14,...]"."""
    inside = text[text.find("[") + 1:text.rfind("]")] if "[" in text else text
    out = {}
    for m in re.finditer(r"(\w+)=(.*?)(?=,\w+=|$)", inside):
        out[m.group(1)] = None if m.group(2) == "<null>" else m.group(2)
    return out


def _day(value) -> str | None:
    """'2026-10-01T08:00:00.000+03:00' -> '2026-10-01' (the day as Jira wrote it)."""
    return value[:10] if isinstance(value, str) and re.match(r"\d{4}-\d{2}-\d{2}", value) else None


def pick_sprint(value) -> dict | None:
    """The sprint an issue is in, from Jira's sprint field. An issue carried over keeps its old, closed
    sprints in the list too, so: the active one, else the next future one, else the last closed one."""
    items = value if isinstance(value, list) else [value] if value else []
    sprints = []
    for i, raw in enumerate(items):
        item = _legacy_sprint(raw) if isinstance(raw, str) else raw if isinstance(raw, dict) else None
        if not item or not item.get("name"):
            continue
        state = (item.get("state") or "").lower()
        sprints.append((_ORDER.get(state, 1), i, {
            "id": str(item["id"]) if item.get("id") is not None else None, "name": str(item["name"]).strip()[:100],
            "state": state or None, "starts_on": _day(item.get("startDate")), "ends_on": _day(item.get("endDate")),
            "goal": (item.get("goal") or "").strip() or None}))
    if not sprints:
        return None
    best = min(s[0] for s in sprints)
    same = [s for s in sprints if s[0] == best]
    # several closed: the one that ended last (Jira lists them oldest first); several future: the first planned
    return (max(same, key=lambda s: (s[2]["ends_on"] or "", s[1])) if best == 2 else min(same, key=lambda s: s[1]))[2]


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

    def _issue(self, item: dict, sprint_field: str | None = None) -> TrackerIssue:
        f = item.get("fields", {})
        status = f.get("status") or {}
        return TrackerIssue(
            key=item["key"], title=f.get("summary", ""), url=self._url(item["key"]),
            status=status.get("name", "unknown"),
            closed=(status.get("statusCategory") or {}).get("key") == "done",
            snippet=adf_text(f.get("description")).strip()[:300], body=adf_text(f.get("description")).strip(),
            sprint=pick_sprint(f.get(sprint_field)) if sprint_field else None,
        )

    async def sprint_field(self) -> str:
        """This site's sprint field id. Asked once; a site that won't say gets the usual Cloud id."""
        if self.base_url not in _SPRINT_FIELDS:
            try:
                fields = await self._call("GET", "/rest/api/3/field")
            except TrackerError:
                return DEFAULT_SPRINT_FIELD  # not remembered: ask again next time
            found = next((f["id"] for f in fields if isinstance(f, dict)
                          and (f.get("schema") or {}).get("custom") == SPRINT_SCHEMA), None) if isinstance(fields, list) else None
            _SPRINT_FIELDS[self.base_url] = found or DEFAULT_SPRINT_FIELD
        return _SPRINT_FIELDS[self.base_url]

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
        sprint = await self.sprint_field()
        return self._issue(await self._call("GET", f"/rest/api/3/issue/{key}",
                                            params={"fields": f"summary,status,description,{sprint}"}), sprint)

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
