"""In-memory GitHub and Jira for tests, demos and trying the flow without
accounts. Each (tracker, target) starts with two issues so the duplicate
check has something to find. Everything is lost when the server restarts."""
from app.trackers.base import IssueTracker, TrackerError, TrackerIssue, keywords
from app.trackers.report import Block, to_markdown

SEED = [
    ("Upload widget fails to load on Safari", False, "The ID upload widget shows a spinner forever on Safari 17."),
    ("Expired national ID accepted at upload", True, "Fixed in 2.3: expiry dates are now checked."),
]
_STORE: dict[tuple[str, str], dict] = {}


def reset() -> None:
    _STORE.clear()


class MockTracker(IssueTracker):
    is_mock = True

    def __init__(self, name: str, target: str):
        self.name = name
        self.target = target
        self.supports_attachments = name == "jira"  # behaves like the real one
        if (name, target) not in _STORE:
            _STORE[(name, target)] = {"issues": [], "comments": {}, "attachments": {}}
            for title, closed, body in SEED:
                self._add(title, body, closed)

    @property
    def _db(self) -> dict:
        return _STORE[(self.name, self.target)]

    def _key(self, n: int) -> str:
        return f"#{n}" if self.name == "github" else f"{self.target}-{n}"

    def _add(self, title: str, body: str, closed: bool = False) -> TrackerIssue:
        n = len(self._db["issues"]) + 1
        key = self._key(n)
        issue = TrackerIssue(key=key, title=title, url=f"https://mock-{self.name}.local/{self.target}/{n}",
                             status="closed" if closed else "open", closed=closed, snippet=body[:300])
        self._db["issues"].append(issue)
        return issue

    def _get(self, key: str) -> TrackerIssue:
        for issue in self._db["issues"]:
            if issue.key == key:
                return issue
        raise TrackerError(f"{self.name} answered 404 (check the repo / project name): no issue {key}")

    async def search(self, words: list[str], limit: int = 8) -> list[TrackerIssue]:
        def overlap(issue: TrackerIssue) -> int:
            return len(set(words) & set(keywords(issue.title + " " + issue.snippet, limit=50)))
        hits = [i for i in self._db["issues"] if overlap(i) > 0]
        return sorted(hits, key=overlap, reverse=True)[:limit]

    async def create(self, title: str, blocks: list[Block], labels: list[str]) -> TrackerIssue:
        return self._add(title, to_markdown(blocks))

    async def comment(self, key: str, blocks: list[Block]) -> str:
        issue = self._get(key)
        self._db["comments"].setdefault(key, []).append(to_markdown(blocks))
        return issue.url

    async def attach(self, key: str, filename: str, content: bytes, mime: str) -> None:
        if not self.supports_attachments:
            await super().attach(key, filename, content, mime)
        self._get(key)
        self._db["attachments"].setdefault(key, []).append(filename)

    # for tests
    def comments(self, key: str) -> list[str]:
        return self._db["comments"].get(key, [])

    def attachments(self, key: str) -> list[str]:
        return self._db["attachments"].get(key, [])
