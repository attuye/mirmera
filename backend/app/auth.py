"""Accounts: passwords, sign-in tokens, and the access check every request goes through.

- Passwords are hashed with scrypt (Python's standard library), a random salt each.
- Signing in creates a random token. The browser keeps it in an httpOnly cookie; the
  Chrome extension sends an API token as "Authorization: Bearer ...". Only a sha256 of
  each token is stored, so a copy of the database can't be used to sign in.
- guard() runs before every project route: it finds the project behind whatever id is
  in the path (a session, a bug, a story...), then the project's workspace, and checks
  the caller is a member with a role allowed to do this. Not a member: 404, so ids
  from other workspaces don't even reveal that they exist.
"""
import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app import models
from app.config import settings
from app.database import SessionLocal, get_db
from app.timeutil import utcnow

COOKIE = "mirmera_session"
ROLES = ("viewer", "tester", "admin", "owner")
RANK = {r: i for i, r in enumerate(ROLES)}
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
MIN_PASSWORD = 10


# ---------- passwords ----------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return f"scrypt${SCRYPT['n']}${SCRYPT['r']}${SCRYPT['p']}${salt.hex()}${digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32, n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def password_problem(password: str, email: str = "") -> str | None:
    """Why a password is too weak, or None. Length matters most; no composition rules."""
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if len(password) > 200:
        return "Use at most 200 characters."
    if email and password.lower() == email.lower():
        return "Don't use your email address as the password."
    if len(set(password)) < 4:
        return "That password is too easy to guess."
    return None


# ---------- tokens ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(db: DBSession, user: models.User, kind: str = "session", label: str | None = None) -> str:
    token = ("mm_" if kind == "api" else "") + secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(days=settings.session_days) if kind == "session" else None
    db.add(models.AuthToken(user_id=user.id, token_hash=_hash_token(token), kind=kind, label=label, expires_at=expires))
    return token


def revoke(db: DBSession, token: str) -> None:
    db.query(models.AuthToken).filter(models.AuthToken.token_hash == _hash_token(token)).delete()


def _token_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


def user_for(db: DBSession, token: str | None) -> models.User | None:
    if not token:
        return None
    row = db.query(models.AuthToken).filter(models.AuthToken.token_hash == _hash_token(token)).first()
    now = utcnow()
    if not row or (row.expires_at and row.expires_at < now):
        return None
    if not row.last_used_at or now - row.last_used_at > timedelta(minutes=5):  # don't write on every request
        row.last_used_at = now
        if row.kind == "session":  # sliding: stays signed in while used
            row.expires_at = now + timedelta(days=settings.session_days)
        db.commit()
    return row.user


def set_cookie(response, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=settings.session_days * 86400, httponly=True, samesite="lax",
                        secure=settings.cookie_secure, path="/")


# ---------- sign-in attempts ----------

_ATTEMPTS: dict[str, deque] = defaultdict(deque)
MAX_ATTEMPTS, WINDOW = 10, 15 * 60


def too_many_attempts(key: str) -> bool:
    q = _ATTEMPTS[key]
    while q and q[0] < time.monotonic() - WINDOW:
        q.popleft()
    return len(q) >= MAX_ATTEMPTS


def failed_attempt(key: str) -> None:
    _ATTEMPTS[key].append(time.monotonic())


def reset_attempts() -> None:  # for tests
    _ATTEMPTS.clear()


# ---------- who is calling ----------

def current_user(request: Request, db: DBSession = Depends(get_db)) -> models.User:
    user = getattr(request.state, "user", None) or user_for(db, _token_from(request))
    if not user:
        raise HTTPException(401, "Sign in first.")
    request.state.user = user
    return user


def roles_of(db: DBSession, user: models.User) -> dict[str, str]:
    """{workspace_id: role} for every workspace the user belongs to."""
    return {m.workspace_id: m.role for m in db.query(models.Membership).filter(models.Membership.user_id == user.id)}


def _via_bug(db, bug_id):
    bug = db.get(models.Bug, bug_id)
    return bug.charter.project_id if bug else None


def _via_session(db, session_id):
    s = db.get(models.Session, session_id)
    return s.charter.project_id if s and s.charter else None


def _project(db, model, key):
    row = db.get(model, key)
    return row.project_id if row else None


RUN_MODELS = {"reproduction-runs": models.ReproductionRun, "isolation-runs": models.IsolationRun}

RESOLVE = {
    "project_id": lambda db, v, path: v if db.get(models.Project, v) else None,
    "charter_id": lambda db, v, path: _project(db, models.Charter, v),
    "risk_id": lambda db, v, path: _project(db, models.Risk, v),
    "story_id": lambda db, v, path: _project(db, models.Story, v),
    "sprint_id": lambda db, v, path: _project(db, models.Sprint, v),
    "session_id": lambda db, v, path: _via_session(db, v),
    "bug_id": lambda db, v, path: _via_bug(db, v),
    "change_id": lambda db, v, path: (lambda c: c.risk.project_id if c else None)(db.get(models.RiskChange, v)),
    "test_id": lambda db, v, path: (lambda t: _via_bug(db, t.bug_id) if t else None)(db.get(models.RegressionTest, v)),
    "evidence_id": lambda db, v, path: (lambda e: _via_session(db, e.session_id) if e else None)(db.get(models.Evidence, v)),
    "run_id": lambda db, v, path: _run_project(db, v, path),
    "job_id": lambda db, v, path: _project(db, models.AiJob, v),
    "dismissal_id": lambda db, v, path: _project(db, models.Dismissal, v),
    "comparison_id": lambda db, v, path: (lambda c: _via_session(db, c.session_id) if c else None)(db.get(models.Comparison, v)),
}


def _run_project(db, run_id, path):
    if path.startswith("/suite-runs/"):
        return _project(db, models.RegressionSuiteRun, run_id)
    if path.startswith("/exploration-runs/"):
        return _project(db, models.ExplorationRun, run_id)
    model = RUN_MODELS.get(path.strip("/").split("/")[0])
    run = db.get(model, run_id) if model else None
    return _via_bug(db, run.bug_id) if run else None


# Changing a project itself (settings, AI routing, notifications, archive, delete) is for admins.
ADMIN_ROUTES = [
    ("PATCH", re.compile(r"^/projects/[^/]+$")), ("DELETE", re.compile(r"^/projects/[^/]+$")),
    ("POST", re.compile(r"^/projects/[^/]+/(archive|unarchive)$")),
    ("PUT", re.compile(r"^/projects/[^/]+/notifications$")), ("POST", re.compile(r"^/projects/[^/]+/notifications/test$")),
]


def needed_role(method: str, path: str) -> str:
    if method in ("GET", "HEAD", "OPTIONS"):
        return "viewer"
    if any(method == m and rx.match(path) for m, rx in ADMIN_ROUTES):
        return "admin"
    return "tester"


def check_project(request: Request, db: DBSession, project_id: str, role: str | None = None) -> None:
    """For ids that arrive in a request body: 404 unless the caller may act on that project."""
    project = db.get(models.Project, project_id)
    user = current_user(request, db)
    have = roles_of(db, user).get(project.org_id) if project else None
    if have is None:
        raise HTTPException(404, "Not found")
    want = role or needed_role(request.method, request.url.path)
    if RANK[have] < RANK[want]:
        raise HTTPException(403, f"Your role here ({have}) can't do this; ask a workspace admin.")


def guard(request: Request) -> None:
    """Runs before every project route (see app/main.py)."""
    with SessionLocal() as db:
        user = user_for(db, _token_from(request))
        if not user:
            raise HTTPException(401, "Sign in first.")
        request.state.user_id = user.id
        params = request.path_params
        path = request.url.path
        project_id = None
        for name, resolve in RESOLVE.items():
            if name in params:
                project_id = resolve(db, params[name], path)
                if project_id is None:
                    return  # unknown id: the route answers 404 itself
                break
        if project_id is None:
            return  # no project in the path: the route checks what it needs
        project = db.get(models.Project, project_id)
        have = roles_of(db, user).get(project.org_id)
        if have is None:
            raise HTTPException(404, "Not found")
        want = needed_role(request.method, path)
        if RANK[have] < RANK[want]:
            raise HTTPException(403, f"Your role here ({have}) can't do this; ask a workspace admin.")


def claim_unowned(db: DBSession, workspace: models.Workspace) -> int:
    """The first account takes over projects made before accounts existed."""
    known = {w.id for w in db.query(models.Workspace.id)}
    rows = [p for p in db.query(models.Project).all() if p.org_id not in known]
    for p in rows:
        p.org_id = workspace.id
    return len(rows)
