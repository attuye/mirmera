"""The Mirmera app for Jira: the Mirmera side of jira-app/ (an Atlassian Forge app).

How a request gets here: the panel on a Jira issue calls the app's resolver function, which
runs on Atlassian's servers and calls Mirmera with invokeRemote(). Atlassian adds a Forge
Invocation Token (FIT) to that call: a JWT signed with Atlassian's keys, naming the app
installation (one Jira site) and the site's cloud id. Mirmera accepts a request only when:

- the token's signature checks out against Atlassian's published keys (JWKS),
- it was issued by "forge/invocation-token" for this app (audience = JIRA_APP_ID), and
- it hasn't expired.

An installation sees nothing until it is linked to a workspace: a workspace admin makes a
one-time code in Mirmera, a Jira admin pastes it into the app's admin page. Then everyone
who can open an issue on that site sees its Mirmera panel: counts, titles and what's left
for the story, never evidence, notes or keys.
"""
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
import jwt
from sqlalchemy.orm import Session as DBSession

from app import models, stories
from app.config import settings
from app.project_report import tested_seconds
from app.timeutil import utcnow

ISSUER = "forge/invocation-token"
CODE_PREFIX = "mjl_"
CODE_MINUTES = 30
JWKS_SECONDS = 3600  # Atlassian rotates keys rarely
REFETCH_SECONDS = 60  # an unknown key id refetches, at most this often
LEEWAY_SECONDS = 30


class FitProblem(Exception):
    """The request's Forge token is missing, malformed, expired or not for this app."""


@dataclass
class Invocation:
    installation_id: str
    cloud_id: str | None
    environment: str | None
    account_id: str | None  # the Jira user the app acts for, when there is one


# ---------- Atlassian's signing keys ----------

_jwks: dict = {"set": None, "at": 0.0}
_jwks_lock = threading.Lock()


def fetch_jwks() -> dict:
    """Atlassian's public keys for invocation tokens. Replaced in tests."""
    r = httpx.get(settings.forge_jwks_url, timeout=10)
    r.raise_for_status()
    return r.json()


def _load() -> None:
    try:
        _jwks["set"] = jwt.PyJWKSet.from_dict(fetch_jwks())
    except (httpx.HTTPError, ValueError, jwt.PyJWTError) as err:
        raise FitProblem(f"Atlassian's signing keys couldn't be fetched ({type(err).__name__}).")
    _jwks["at"] = time.monotonic()


def _find(kid: str | None) -> jwt.PyJWK | None:
    return next((k for k in _jwks["set"].keys if k.key_id == kid), None) if _jwks["set"] else None


def _signing_key(kid: str | None) -> jwt.PyJWK:
    """The published key with this id. Keys are cached for an hour; an unknown id refetches
    them, but at most once a minute, so made-up ids can't turn Mirmera into a fetch loop."""
    with _jwks_lock:
        age = time.monotonic() - _jwks["at"]
        if _jwks["set"] is None or age > JWKS_SECONDS:
            _load()
        elif _find(kid) is None and age > REFETCH_SECONDS:
            _load()
        key = _find(kid)
    if key is None:
        raise FitProblem("The token was signed with a key Atlassian doesn't publish.")
    return key


def forget_jwks() -> None:
    _jwks.update(set=None, at=0.0)


def verify(authorization: str | None) -> Invocation:
    """The installation behind a request from the Jira app, or FitProblem."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise FitProblem("No Forge invocation token.")
    token = authorization[7:].strip()
    if not settings.jira_app_id:
        raise FitProblem("The Jira app isn't set up on this Mirmera: set JIRA_APP_ID in backend/.env.")
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError:
        raise FitProblem("The Forge invocation token isn't a JWT.")
    key = _signing_key(kid)
    try:
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=settings.jira_app_id, issuer=ISSUER,
                            leeway=LEEWAY_SECONDS, options={"require": ["exp", "iss", "aud"]})
    except jwt.ExpiredSignatureError:
        raise FitProblem("The Forge invocation token has expired.")
    except jwt.InvalidAudienceError:
        raise FitProblem("The token is for a different Forge app than JIRA_APP_ID.")
    except jwt.PyJWTError as err:
        raise FitProblem(f"The Forge invocation token isn't valid ({type(err).__name__}).")
    app = claims.get("app") or {}
    installation = app.get("installationId")
    if not installation:
        raise FitProblem("The token names no app installation.")
    principal = claims.get("principal")
    return Invocation(installation_id=str(installation), cloud_id=(claims.get("context") or {}).get("cloudId"),
                      environment=(app.get("environment") or {}).get("type"),
                      account_id=principal if isinstance(principal, str) else None)


# ---------- linking a Jira site to a workspace ----------

def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


def new_code(db: DBSession, workspace_id: str, user: models.User) -> tuple[str, models.JiraLinkCode]:
    db.query(models.JiraLinkCode).filter(models.JiraLinkCode.expires_at < utcnow()).delete()
    code = CODE_PREFIX + secrets.token_urlsafe(24)
    row = models.JiraLinkCode(workspace_id=workspace_id, code_hash=_hash(code), created_by=user.id,
                              expires_at=utcnow() + timedelta(minutes=CODE_MINUTES))
    db.add(row)
    return code, row


def installation(db: DBSession, inv: Invocation) -> models.JiraInstallation | None:
    row = db.query(models.JiraInstallation).filter(models.JiraInstallation.installation_id == inv.installation_id).first()
    if row:
        # At most one write a minute: every panel view comes through here.
        if not row.last_seen_at or utcnow() - row.last_seen_at > timedelta(minutes=1):
            row.last_seen_at = utcnow()
        if inv.cloud_id and not row.cloud_id:
            row.cloud_id = inv.cloud_id
    return row


def link(db: DBSession, inv: Invocation, code: str, site_url: str | None) -> models.JiraInstallation:
    """Links (or re-links) the installation to the code's workspace. The code works once."""
    row = db.query(models.JiraLinkCode).filter(models.JiraLinkCode.code_hash == _hash(code)).first()
    if not row or row.expires_at < utcnow():
        raise ValueError("That code is wrong or has expired. Make a new one in Mirmera: Account → your workspace → Jira.")
    inst = db.query(models.JiraInstallation).filter(models.JiraInstallation.installation_id == inv.installation_id).first()
    if not inst:
        inst = models.JiraInstallation(installation_id=inv.installation_id)
        db.add(inst)
    inst.workspace_id = row.workspace_id
    inst.cloud_id = inv.cloud_id
    inst.environment = inv.environment
    inst.site_url = (site_url or "").strip().rstrip("/") or None
    inst.linked_by = row.created_by
    inst.linked_by_jira = inv.account_id
    inst.linked_at = utcnow()
    inst.last_seen_at = utcnow()
    db.delete(row)
    return inst


# ---------- the issue panel ----------

def app_link(project_id: str, to: str = "") -> str | None:
    if not settings.public_app_url:
        return None
    return f"{settings.public_app_url.rstrip('/')}/projects/{project_id}{to}"


def _story(story: models.Story, project: models.Project, now) -> dict:
    sessions = stories._sessions(story)
    bugs = stories._bugs(sessions)
    return {
        "id": story.id, "project_id": story.project_id, "project": project.name, "title": story.title,
        "risks": len([r for r in story.risks if r.status != "retired"]), "charters": len(story.charters),
        "sessions": len(sessions), "minutes": round(sum(tested_seconds(s, now) for s in sessions) / 60),
        "bugs": len([b for b in bugs if b.kind != "question"]),
        "high_bugs": len([b for b in bugs if b.kind == "bug" and b.severity == "high"]),
        "left": [item["text"] for item in stories._left(story, sessions, bugs)],
        "sprint": story.sprint.name if story.sprint else None,
        "url": app_link(story.project_id, f"/stories/{story.id}"),
    }


def _bug(bug: models.Bug, filing: models.BugFiling, project: models.Project, retest: models.Retest | None) -> dict:
    return {
        "id": bug.id, "project_id": project.id, "project": project.name, "title": bug.title,
        "kind": bug.kind, "severity": bug.severity,
        "filed_as": "new issue" if filing.mode == "new" else "a new occurrence (comment)",
        "found_in": bug.charter.mission_statement if bug.charter else None,
        "retest": retest.outcome if retest else None,
        "url": app_link(project.id, "/bugs"),
    }


def issue_panel(db: DBSession, inst: models.JiraInstallation, issue_key: str, project_key: str | None) -> dict:
    """What Mirmera knows about one Jira issue, within the linked workspace."""
    now = utcnow()
    issue_key = issue_key.strip().upper()
    project_key = (project_key or issue_key.rsplit("-", 1)[0]).strip().upper()
    projects = db.query(models.Project).filter(models.Project.org_id == inst.workspace_id).all()
    ids = {p.id: p for p in projects}
    matching = [p for p in projects if (p.jira_project_key or "").upper() == project_key and not p.archived_at]

    story_rows = (db.query(models.Story).filter(models.Story.project_id.in_(ids), models.Story.key == issue_key).all()
                  if ids else [])
    filings = (db.query(models.BugFiling).join(models.Bug).join(models.Charter)
               .filter(models.Charter.project_id.in_(ids), models.BugFiling.tracker == "jira",
                       models.BugFiling.issue_key == issue_key).all() if ids else [])
    bugs = []
    for f in sorted(filings, key=lambda f: f.created_at):
        retest = (db.query(models.Retest).filter(models.Retest.filing_id == f.id)
                  .order_by(models.Retest.created_at.desc()).first())
        bugs.append(_bug(f.bug, f, ids[f.bug.charter.project_id], retest))

    return {
        "issue_key": issue_key,
        "workspace": inst.workspace.name,
        "stories": [_story(st, ids[st.project_id], now) for st in sorted(story_rows, key=lambda s: s.created_at)],
        "bugs": bugs,
        "projects": [{"id": p.id, "name": p.name, "stories_url": app_link(p.id, "/stories")} for p in matching],
        "app_url": settings.public_app_url.rstrip("/") if settings.public_app_url else None,
    }
