"""Issue tracker interface. Implementations: GitHub Issues, Jira Cloud, and a
mock kept in memory. Same shape as the AI providers: swap without touching
the rest of the app."""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.trackers.report import Block

TRACKER_NAMES = ("github", "jira")
TIMEOUT = httpx.Timeout(20.0)

STOPWORDS = set("""a an and are as at be but by can cannot does doesn't don't for from has have
in into is isn't it its no not of on or so that the then there this to was when where which while
with without after before during should would could will page""".split())


class TrackerError(Exception):
    """The tracker refused or failed a request (502). The message is shown to the tester."""


class TrackerNotConfigured(TrackerError):
    """Credentials or the project's target are missing (503)."""


class UnknownTracker(TrackerError):
    """Not one of TRACKER_NAMES (400)."""


@dataclass
class TrackerIssue:
    key: str  # "#42" or "KYC-42"
    title: str
    url: str
    status: str
    closed: bool
    snippet: str = ""
    body: str = ""  # the whole description, when the tracker gave it


def keywords(title: str, limit: int = 6) -> list[str]:
    """Search words from a bug title: no stopwords or short words, no repeats."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*", title.lower())
    picked: list[str] = []
    for w in words:
        w = w.strip(".-")
        if len(w) >= 3 and w not in STOPWORDS and w not in picked:
            picked.append(w)
    return picked[:limit]


def failure_message(tracker: str, response: httpx.Response) -> str:
    """A short, token-free explanation of a failed tracker call."""
    detail = ""
    try:
        body = response.json()
        detail = body.get("message") or "; ".join(body.get("errorMessages", [])) or \
            "; ".join(f"{k}: {v}" for k, v in (body.get("errors") or {}).items())
    except Exception:
        detail = response.text[:200]
    hint = {401: "check the token", 403: "the token lacks permission", 404: "check the repo / project name",
            410: "this API endpoint is gone", 422: "the tracker rejected a field"}.get(response.status_code, "")
    return f"{tracker} answered {response.status_code}" + (f" ({hint})" if hint else "") + (f": {detail}" if detail else "")


class IssueTracker(ABC):
    name: str
    target: str  # repo or project key
    supports_attachments: bool = False
    is_mock: bool = False

    @abstractmethod
    async def search(self, words: list[str], limit: int = 8) -> list[TrackerIssue]:
        """Open and closed issues matching any of the words, best first."""

    @abstractmethod
    async def create(self, title: str, blocks: list[Block], labels: list[str]) -> TrackerIssue: ...

    @abstractmethod
    async def comment(self, key: str, blocks: list[Block]) -> str:
        """Adds a comment to an existing issue; returns the issue URL."""

    @abstractmethod
    async def issue(self, key: str) -> TrackerIssue:
        """One issue as it is now, with whether it is closed."""

    async def attach(self, key: str, filename: str, content: bytes, mime: str) -> None:
        raise TrackerError(f"{self.name} cannot receive attachments through its API")
