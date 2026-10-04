"""Endpoints for the Mirmera Chrome extension, which records a session in the
tester's everyday browser instead of the recorded browser.

The everyday browser carries real logins, so the server applies the same
rules whatever the extension sends: actions through the shared sanitizer
(secrets never stored), lengths capped, and query-string values removed from
every URL (they often carry tokens). Only sessions that are still running
accept events.
"""
import base64
import uuid
from datetime import timedelta
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app import auth, models, schemas
from app.database import get_db
from app.routers.runner import session_folder, store_signals
from app.runner import Signal, _cut, describe_action, sanitize_action
from app.timeutil import utcnow

router = APIRouter(tags=["chrome extension"])

ACTIVE_WINDOW = timedelta(minutes=2)
LAST_SEEN: dict[str, object] = {}  # session id -> time of the extension's last event
MAX_SCREENSHOT_BYTES = 5_000_000
IMAGE_TYPES = {"data:image/png;base64,": (b"\x89PNG\r\n\x1a\n", "png"), "data:image/jpeg;base64,": (b"\xff\xd8\xff", "jpg")}


def redact(url: str | None) -> str | None:
    """Keeps the query's parameter names, drops their values: ?token=abc -> ?token=…"""
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return _cut(url)
    query = "&".join(f"{quote(k, safe='')}=…" for k, _ in parse_qsl(parts.query, keep_blank_values=True))
    return _cut(urlunsplit((parts.scheme, parts.netloc, parts.path, query, "")))  # fragments go too


def extension_active(session_id: str) -> bool:
    seen = LAST_SEEN.get(session_id)
    return bool(seen and utcnow() - seen < ACTIVE_WINDOW)


def _running_session(db: DBSession, session_id: str) -> models.Session:
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session.ended_at:
        raise HTTPException(409, "This session has ended; start a new one in Mirmera to record again.")
    return session


def to_signal(e: schemas.ExtensionEvent) -> Signal | None:
    now, url = utcnow(), redact(e.url)
    if e.kind == "navigation":
        return Signal("navigation", url or "", url, now) if url and url.startswith(("http://", "https://")) else None
    if e.kind == "action":
        action = sanitize_action(e.action)
        return Signal("action", describe_action(action), url, now, action) if action else None
    if e.kind in ("console_error", "page_error"):
        message = (e.message or "").strip()
        if not message or (e.kind == "console_error" and message.startswith("Failed to load resource")):
            return None  # Chrome repeats every failed request as a console error; it is recorded once, below
        return Signal(e.kind, _cut(message), url, now)
    if e.kind == "failed_request":
        if not url or not (e.status and e.status >= 400 or e.error):
            return None
        if e.error and "ERR_ABORTED" in e.error:
            return None  # cancelled by navigation or the page itself
        outcome = str(e.status) if e.status and e.status >= 400 else _cut(e.error)
        return Signal("failed_request", f"{(e.method or 'GET').upper()[:10]} {url} → {outcome}", url, now)
    return None


@router.get("/sessions/active", response_model=list[schemas.ActiveSessionOut])
def active_sessions(request: Request, db: DBSession = Depends(get_db)):
    """Running sessions in your workspaces, newest first, for the extension's session picker."""
    workspaces = list(auth.roles_of(db, auth.current_user(request, db)))
    rows = (db.query(models.Session, models.Charter, models.Project)
            .join(models.Charter, models.Session.charter_id == models.Charter.id)
            .join(models.Project, models.Charter.project_id == models.Project.id)
            .filter(models.Session.ended_at.is_(None), models.Project.org_id.in_(workspaces))
            .order_by(models.Session.started_at.desc()).all())
    return [schemas.ActiveSessionOut(id=s.id, charter_mission=c.mission_statement, started_at=s.started_at,
                                     paused=bool(s.paused_at), project_id=p.id, project_name=p.name)
            for s, c, p in rows]


@router.post("/sessions/{session_id}/extension/events")
def ingest(session_id: str, payload: schemas.ExtensionEvents, db: DBSession = Depends(get_db)):
    _running_session(db, session_id)
    signals = [s for s in (to_signal(e) for e in payload.events) if s]
    store_signals(db, session_id, signals)
    LAST_SEEN[session_id] = utcnow()
    return {"stored": len(signals), "ignored": len(payload.events) - len(signals)}


@router.post("/sessions/{session_id}/extension/screenshot", response_model=schemas.EvidenceOut)
def screenshot(session_id: str, payload: schemas.ExtensionScreenshot, db: DBSession = Depends(get_db)):
    _running_session(db, session_id)
    prefix = next((p for p in IMAGE_TYPES if payload.image.startswith(p)), None)
    if not prefix:
        raise HTTPException(422, "The screenshot must be a PNG or JPEG data URL")
    try:
        data = base64.b64decode(payload.image[len(prefix):], validate=True)
    except ValueError as err:
        raise HTTPException(422, "The screenshot is not valid base64") from err
    magic, ext = IMAGE_TYPES[prefix]
    if not data.startswith(magic):
        raise HTTPException(422, "The screenshot's content does not match its type")
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise HTTPException(413, "The screenshot is larger than 5 MB")
    if payload.bug_id:
        bug = db.get(models.Bug, payload.bug_id)
        if not bug or bug.session_id != session_id:
            raise HTTPException(404, "Bug not found in this session")
    path = session_folder(session_id) / f"{utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    caption = (payload.caption or "").strip() or "Screenshot from Chrome"
    evidence = models.Evidence(session_id=session_id, bug_id=payload.bug_id, kind="screenshot", summary=_cut(caption),
                               url=redact(payload.url), file_path=str(path))
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    LAST_SEEN[session_id] = utcnow()
    return evidence


@router.post("/sessions/{session_id}/extension/stop")
def stopped(session_id: str):
    """The extension stopped recording: the session screen stops showing it as active."""
    LAST_SEEN.pop(session_id, None)
    return {"ok": True}
