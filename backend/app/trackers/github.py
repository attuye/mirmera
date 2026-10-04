"""GitHub Issues through the REST API.

GitHub's API cannot attach files to issues, so screenshots stay in Mirmera
and the issue says so."""
import httpx

from app.trackers.base import TIMEOUT, IssueTracker, TrackerError, TrackerIssue, failure_message
from app.trackers.report import Block, to_markdown


class GitHubTracker(IssueTracker):
    name = "github"
    supports_attachments = False

    def __init__(self, repo: str, token: str, api_url: str = "https://api.github.com",
                 transport: httpx.AsyncBaseTransport | None = None):
        self.target = repo
        self._client_args = dict(
            base_url=api_url.rstrip("/"), timeout=TIMEOUT, transport=transport,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "mirmera"},
        )

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        async with httpx.AsyncClient(**self._client_args) as client:
            try:
                r = await client.request(method, path, **kwargs)
            except httpx.HTTPError as err:
                raise TrackerError(f"Could not reach GitHub: {err}") from err
        if r.status_code >= 400:
            raise TrackerError(failure_message("GitHub", r))
        return r.json()

    async def _text(self, path: str, accept: str) -> str:
        async with httpx.AsyncClient(**self._client_args) as client:
            try:
                r = await client.get(path, headers={"Accept": accept})
            except httpx.HTTPError as err:
                raise TrackerError(f"Could not reach GitHub: {err}") from err
        if r.status_code >= 400:
            raise TrackerError(failure_message("GitHub", r))
        return r.text

    async def pull_request(self, number: int) -> dict:
        """Title, description, address and unified diff of one pull request."""
        pr = await self._call("GET", f"/repos/{self.target}/pulls/{number}")
        diff = await self._text(f"/repos/{self.target}/pulls/{number}", "application/vnd.github.v3.diff")
        return {"title": pr["title"], "body": pr.get("body") or "", "url": pr["html_url"], "diff": diff,
                "state": "merged" if pr.get("merged_at") else pr["state"]}

    async def list_issues(self, labels: list[str], limit: int = 50) -> list[dict]:
        """Open and closed issues with these labels (any state), newest first; pull requests left out."""
        params = {"state": "all", "per_page": min(limit, 100), "sort": "created", "direction": "desc"}
        if labels:
            params["labels"] = ",".join(labels)
        items = await self._call("GET", f"/repos/{self.target}/issues", params=params)
        return [{"key": f"#{i['number']}", "title": i["title"], "status": i["state"], "body": i.get("body") or "",
                 "url": i["html_url"], "labels": [l["name"] for l in i.get("labels", [])]}
                for i in items if "pull_request" not in i][:limit]

    @staticmethod
    def _issue(item: dict) -> TrackerIssue:
        return TrackerIssue(
            key=f"#{item['number']}", title=item["title"], url=item["html_url"], status=item["state"],
            closed=item["state"] == "closed", snippet=(item.get("body") or "")[:300],
        )

    async def search(self, words: list[str], limit: int = 8) -> list[TrackerIssue]:
        if not words:
            return []
        # Search terms are ANDed by default; OR finds issues sharing any of the words.
        query = f"repo:{self.target} is:issue (" + " OR ".join(words[:5]) + ")"
        data = await self._call("GET", "/search/issues", params={"q": query, "per_page": limit})
        return [self._issue(i) for i in data.get("items", []) if "pull_request" not in i][:limit]

    async def create(self, title: str, blocks: list[Block], labels: list[str]) -> TrackerIssue:
        body = {"title": title, "body": to_markdown(blocks)}
        if labels:
            body["labels"] = labels
        return self._issue(await self._call("POST", f"/repos/{self.target}/issues", json=body))

    @staticmethod
    def _number(key: str) -> str:
        number = key.lstrip("#")
        if not number.isdigit():
            raise TrackerError(f"'{key}' is not a GitHub issue number")
        return number

    async def issue(self, key: str) -> TrackerIssue:
        return self._issue(await self._call("GET", f"/repos/{self.target}/issues/{self._number(key)}"))

    async def comment(self, key: str, blocks: list[Block]) -> str:
        number = self._number(key)
        await self._call("POST", f"/repos/{self.target}/issues/{number}/comments", json={"body": to_markdown(blocks)})
        return f"https://github.com/{self.target}/issues/{number}"
