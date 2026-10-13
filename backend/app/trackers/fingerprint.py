"""Knowing a problem is already filed, before filing it again, without asking an AI.

Every issue Mirmera files carries a fingerprint ("Mirmera fingerprint: mfp3fa9c0b21d7e"): the
problem as a key (a failing request by its path, a wrong answer by the rule and the words on
the page, or the title when nothing else is known), hashed. So before filing, Mirmera can:
- look at its own filings: another bug of the project, the same problem, already filed;
- search the tracker for the fingerprint: an issue filed from Mirmera by a teammate, in another
  project or workspace, open or closed (closed: the problem may be back, a regression).
Both are exact; the AI's rating of word matches comes after, for everything else.
"""
import hashlib
import re
from urllib.parse import urlsplit

from app import models
from app.trackers.base import keywords

PREFIX = "mfp"
FOUND = re.compile(rf"\b{PREFIX}[0-9a-f]{{12}}\b")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", (text or "").lower())).strip()


def basis(bug: models.Bug) -> str:
    """What makes two reports the same problem."""
    return basis_of(bug.signature, bug.symptom, bug.title)


def basis_of(signature: str | None, symptom: dict | None, title: str) -> str:
    if signature:
        return signature
    s = symptom or {}
    if s.get("kind") and s.get("value"):
        value = urlsplit(s["value"]).path or s["value"] if s["kind"] == "failed_request" else s["value"]
        return f"{s['kind']}:{_norm(value)}"
    return f"title:{_norm(title)}"


def fingerprint(bug: models.Bug) -> str:
    return PREFIX + hashlib.sha256(basis(bug).encode()).hexdigest()[:12]


def fingerprint_of(signature: str | None, symptom: dict | None, title: str) -> str:
    return PREFIX + hashlib.sha256(basis_of(signature, symptom, title).encode()).hexdigest()[:12]


def symptom_words(bug: models.Bug) -> list[str]:
    """Search words from what shows the bug: a request's path, or the words on the page."""
    s = bug.symptom or {}
    if not s.get("value"):
        return []
    if s.get("kind") == "failed_request":
        path = urlsplit(s["value"]).path or s["value"]
        return [w for w in re.split(r"[/_.-]+", path.lower()) if len(w) >= 3 and w not in ("api", "www")][:4]
    return keywords(s["value"], limit=5)


def same_problem(db, bug: models.Bug, tracker: str) -> list[tuple[models.BugFiling, models.Bug]]:
    """Mirmera's own filings, to this tracker, of other bugs of the project that are the same problem."""
    mine = fingerprint(bug)
    project_id = bug.charter.project_id
    rows = (db.query(models.BugFiling, models.Bug).join(models.Bug, models.BugFiling.bug_id == models.Bug.id)
            .join(models.Charter, models.Bug.charter_id == models.Charter.id)
            .filter(models.Charter.project_id == project_id, models.Bug.id != bug.id,
                    models.BugFiling.tracker == tracker).all())
    return [(f, b) for f, b in rows if fingerprint(b) == mine]
