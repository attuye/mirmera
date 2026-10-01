"""Browser session runner endpoints, and the evidence it records.

The runner itself (app/runner.py) only buffers what it sees; these endpoints
move that buffer into the database whenever the tester's screen asks for the
status or the evidence, and when the browser stops.
"""
import logging
import uuid
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.config import settings
from app.database import get_db
from app.runner import BrowserRunner, Signal, manager
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["runner"])

# Signals this long before a bug was logged are shown with the bug.
BUG_SIGNAL_WINDOW = timedelta(minutes=2)
FILE_KINDS = {"screenshot": "image/png", "trace": "application/zip", "har": "application/json"}


# ---------- helpers shared with the sessions router ----------

def evidence_root() -> Path:
    return Path(settings.evidence_dir).resolve()


def session_folder(session_id: str) -> Path:
    return evidence_root() / session_id


def _session_or_404(db: DBSession, session_id: str) -> models.Session:
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


def store_signals(db: DBSession, session_id: str, signals: list[Signal]) -> None:
    """Save signals; a repeat of the same signal bumps the count on its row.
    Pages visited are all kept, in order: they are the tester's trail."""
    seen: dict[tuple[str, str], models.Evidence] = {}  # rows added in this batch aren't queryable yet
    for s in signals:
        if s.kind != "navigation":
            key = (s.kind, s.summary)
            existing = seen.get(key) or (
                db.query(models.Evidence)
                .filter_by(session_id=session_id, kind=s.kind, summary=s.summary)
                .first()
            )
            if existing:
                existing.count = (existing.count or 1) + 1
                existing.last_seen_at = s.at
                seen[key] = existing
                continue
        row = models.Evidence(session_id=session_id, kind=s.kind, summary=s.summary, url=s.url,
                              captured_at=s.at, last_seen_at=s.at, count=1)
        db.add(row)
        if s.kind != "navigation":
            seen[(s.kind, s.summary)] = row
    db.commit()


def sync_signals(db: DBSession, session_id: str) -> BrowserRunner | None:
    runner = manager.get(session_id)
    if runner:
        store_signals(db, session_id, runner.drain())
    return runner


def take_screenshot(db: DBSession, session_id: str, runner: BrowserRunner, *,
                    caption: str | None = None, bug_id: str | None = None) -> models.Evidence:
    path = session_folder(session_id) / f"{utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}.png"
    url, title = manager.loop.run(runner.screenshot(path), timeout=30)
    evidence = models.Evidence(
        session_id=session_id, bug_id=bug_id, kind="screenshot",
        summary=(caption or "").strip() or f"Screenshot of {title or url}", url=url, file_path=str(path),
    )
    db.add(evidence)
    db.commit()
    db.refresh(evidence)
    return evidence


def _store_files(db: DBSession, session_id: str, files: dict[str, Path]) -> None:
    labels = {"trace": "Playwright trace of the session", "har": "Network log (HAR) of the session"}
    for kind, path in files.items():
        db.add(models.Evidence(session_id=session_id, kind=kind, summary=labels[kind], file_path=str(path)))
    db.commit()


def stop_runner(db: DBSession, session_id: str) -> bool:
    """Stop the browser if it runs, saving its last signals, trace and HAR."""
    runner = manager.pop(session_id)
    if not runner:
        return False
    store_signals(db, session_id, runner.drain())
    files = manager.loop.run(runner.stop(), timeout=60)
    store_signals(db, session_id, runner.drain())
    _store_files(db, session_id, files)
    return True


async def stop_runner_async(db: DBSession, session_id: str) -> bool:
    """stop_runner for async endpoints (ending a session)."""
    runner = manager.pop(session_id)
    if not runner:
        return False
    store_signals(db, session_id, runner.drain())
    files = await manager.loop.arun(runner.stop())
    store_signals(db, session_id, runner.drain())
    _store_files(db, session_id, files)
    return True


def _status(db: DBSession, session_id: str, runner: BrowserRunner | None) -> schemas.RunnerStatus:
    counts = dict(
        db.query(models.Evidence.kind, func.sum(models.Evidence.count))
        .filter(models.Evidence.session_id == session_id)
        .group_by(models.Evidence.kind)
        .all()
    )
    if not runner:
        return schemas.RunnerStatus(running=False, counts=counts)
    live = manager.loop.run(runner.status(), timeout=15)
    return schemas.RunnerStatus(running=True, start_url=runner.start_url, started_at=runner.started_at,
                                counts=counts, dropped=runner.dropped, **live)


# ---------- endpoints ----------

@router.post("/sessions/{session_id}/runner", response_model=schemas.RunnerStatus)
def start_runner(session_id: str, payload: schemas.RunnerStart, db: DBSession = Depends(get_db)):
    session = _session_or_404(db, session_id)
    if session.ended_at:
        raise HTTPException(409, "This session has ended; start a new session to test again.")
    runner = manager.start(session_id, payload.url, session_folder(session_id),
                           headless=settings.runner_headless, executable_path=settings.runner_chromium_path)
    store_signals(db, session_id, runner.drain())
    return _status(db, session_id, runner)


@router.get("/sessions/{session_id}/runner", response_model=schemas.RunnerStatus)
def runner_status(session_id: str, db: DBSession = Depends(get_db)):
    _session_or_404(db, session_id)
    return _status(db, session_id, sync_signals(db, session_id))


@router.post("/sessions/{session_id}/runner/screenshot", response_model=schemas.EvidenceOut)
def screenshot(session_id: str, payload: schemas.ScreenshotRequest, db: DBSession = Depends(get_db)):
    _session_or_404(db, session_id)
    runner = sync_signals(db, session_id)
    if not runner:
        raise HTTPException(409, "The browser is not running. Open it first.")
    return take_screenshot(db, session_id, runner, caption=payload.caption)


@router.post("/sessions/{session_id}/runner/stop", response_model=schemas.RunnerStatus)
def stop(session_id: str, db: DBSession = Depends(get_db)):
    _session_or_404(db, session_id)
    if not stop_runner(db, session_id):
        raise HTTPException(409, "The browser is not running.")
    return _status(db, session_id, None)


@router.get("/sessions/{session_id}/evidence", response_model=list[schemas.EvidenceOut])
def list_evidence(session_id: str, kind: str | None = None, db: DBSession = Depends(get_db)):
    _session_or_404(db, session_id)
    sync_signals(db, session_id)
    query = db.query(models.Evidence).filter(models.Evidence.session_id == session_id)
    if kind:
        query = query.filter(models.Evidence.kind.in_(kind.split(",")))
    return query.order_by(models.Evidence.captured_at).all()


@router.get("/bugs/{bug_id}/evidence", response_model=list[schemas.EvidenceOut])
def bug_evidence(bug_id: str, db: DBSession = Depends(get_db)):
    """The bug's screenshot(s), plus every signal seen in the two minutes
    before the bug was logged: what the page was doing when it went wrong."""
    bug = db.get(models.Bug, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    sync_signals(db, bug.session_id)
    return evidence_for_bug(db, bug)


def evidence_for_bug(db: DBSession, bug: models.Bug) -> list[models.Evidence]:
    """Screenshots taken with the bug, then signals from the window before it.
    If no page was visited inside the window, the last page before it is
    included: it is the page the tester was on when the bug happened."""
    window_start = bug.created_at - BUG_SIGNAL_WINDOW
    signals = (
        db.query(models.Evidence)
        .filter(models.Evidence.session_id == bug.session_id,
                models.Evidence.kind.notin_(list(FILE_KINDS)),
                models.Evidence.last_seen_at >= window_start,
                models.Evidence.captured_at <= bug.created_at + timedelta(seconds=5))
        .order_by(models.Evidence.captured_at)
        .all()
    )
    if not any(e.kind == "navigation" for e in signals):
        last_page = (
            db.query(models.Evidence)
            .filter(models.Evidence.session_id == bug.session_id, models.Evidence.kind == "navigation",
                    models.Evidence.captured_at < window_start)
            .order_by(models.Evidence.captured_at.desc())
            .first()
        )
        if last_page:
            signals.insert(0, last_page)
    return list(bug.evidence) + signals


def browser_used(db: DBSession, session_id: str) -> bool:
    return db.query(models.Evidence.id).filter(models.Evidence.session_id == session_id).first() is not None


@router.get("/evidence/{evidence_id}/file")
def evidence_file(evidence_id: str, db: DBSession = Depends(get_db)):
    evidence = db.get(models.Evidence, evidence_id)
    if not evidence or not evidence.file_path:
        raise HTTPException(404, "No file for this evidence")
    path = Path(evidence.file_path).resolve()
    if not path.is_relative_to(evidence_root()) or not path.is_file():
        raise HTTPException(404, "The file is no longer on disk")
    filename = {"trace": f"mirmera-trace-{evidence.session_id[:8]}.zip",
                "har": f"mirmera-network-{evidence.session_id[:8]}.har"}.get(evidence.kind)
    return FileResponse(path, media_type=FILE_KINDS.get(evidence.kind, "application/octet-stream"),
                        filename=filename, content_disposition_type="attachment" if filename else "inline")
