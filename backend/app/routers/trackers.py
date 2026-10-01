"""Filing reviewed bugs to GitHub Issues or Jira, after a duplicate check.

The tester decides: the duplicate check only rates candidates, and filing is
always an explicit choice (a new issue, or a new occurrence added to an
existing one). Only reviewed bugs and questions can be filed, and each bug at
most once per tracker.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import get_provider
from app.config import settings
from app.database import get_db
from app.evidence_text import _defang as defang
from app.routers import runner as runner_api
from app.trackers import TRACKER_NAMES, TrackerError, get_tracker, keywords, missing_settings
from app.trackers.report import build_report, to_markdown

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trackers"])

VERDICT_ORDER = {"likely_duplicate": 0, "related": 1, "different": 2, "not_assessed": 3}


def _bug_or_404(db: DBSession, bug_id: str) -> models.Bug:
    bug = db.get(models.Bug, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    return bug


def _reviewed(bug: models.Bug) -> None:
    if not bug.kind:
        raise HTTPException(409, "Review this bug first (save it as a bug or a question); only reviewed reports are filed.")


def _product(db: DBSession, bug: models.Bug) -> models.Product:
    return db.get(models.Product, bug.charter.product_id)


def _issue_title(bug: models.Bug) -> str:
    if bug.kind == "question" and not bug.title.rstrip().endswith("?"):
        return f"Question: {bug.title}"
    return bug.title


def _screenshots(evidence: list[models.Evidence]) -> list[models.Evidence]:
    return [e for e in evidence if e.kind == "screenshot" and e.file_path and Path(e.file_path).is_file()]


def _labels(tracker: str, bug: models.Bug) -> list[str]:
    if tracker == "github":
        return [label.strip() for label in settings.github_labels.split(",") if label.strip()]
    return ["mirmera"] + (["question"] if bug.kind == "question" else [])


@router.get("/products/{product_id}/trackers", response_model=list[schemas.TrackerStatus])
def tracker_status(product_id: str, db: DBSession = Depends(get_db)):
    """Which trackers this product can file to, and what is missing for the others."""
    product = db.get(models.Product, product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    out = []
    for name in TRACKER_NAMES:
        missing = missing_settings(name, product)
        out.append(schemas.TrackerStatus(
            name=name, configured=not missing, missing=missing,
            target=product.github_repo if name == "github" else product.jira_project_key,
            supports_attachments=name == "jira", is_mock=settings.mock_trackers,
        ))
    return out


@router.post("/bugs/{bug_id}/duplicates", response_model=schemas.DuplicateCheckOut)
async def check_duplicates(bug_id: str, payload: schemas.TrackerRequest, db: DBSession = Depends(get_db)):
    """Search the tracker (open and closed issues) and have the AI rate each match."""
    bug = _bug_or_404(db, bug_id)
    _reviewed(bug)
    product = _product(db, bug)
    tracker = get_tracker(payload.tracker, product)
    words = keywords(bug.title)
    issues = await tracker.search(words) if words else []
    out = schemas.DuplicateCheckOut(tracker=tracker.name, target=tracker.target, searched_for=words, candidates=[])
    if not issues:
        return out

    provider = get_provider(payload.provider or product.default_ai_provider)
    # Issue text comes from the tracker and can be written by anyone: data only, and tag-proof.
    result = await tasks.check_duplicates(
        provider,
        report={"kind": bug.kind, "title": bug.title, "steps": bug.steps_to_reproduce or [],
                "expected": bug.expected, "actual": bug.actual},
        candidates=[{"key": i.key, "title": defang(i.title), "status": "closed" if i.closed else "open",
                     "snippet": defang(i.snippet)} for i in issues],
    )
    verdicts = {v.key: v for v in result.verdicts}
    out.candidates = sorted(
        [schemas.DuplicateCandidate(
            key=i.key, title=i.title, url=i.url, status=i.status, closed=i.closed, snippet=i.snippet,
            verdict=verdicts[i.key].verdict if i.key in verdicts else "not_assessed",
            reason=verdicts[i.key].reason if i.key in verdicts else "The AI gave no verdict for this issue; judge it yourself.",
        ) for i in issues],
        key=lambda c: VERDICT_ORDER[c.verdict],  # stable: keeps search order within a verdict
    )
    out.ai_provider, out.ai_model = provider.name, provider.model
    out.prompt_version = tasks.PROMPT_VERSIONS["duplicate_check"]
    return out


@router.get("/bugs/{bug_id}/filing-preview", response_model=schemas.FilingPreview)
def filing_preview(bug_id: str, tracker: str, db: DBSession = Depends(get_db)):
    """Exactly what will be filed, as Markdown (Jira gets the same content as rich text)."""
    if tracker not in TRACKER_NAMES:
        raise HTTPException(400, f"Unknown tracker '{tracker}'. Use one of: {', '.join(TRACKER_NAMES)}")
    bug = _bug_or_404(db, bug_id)
    _reviewed(bug)
    evidence = runner_api.evidence_for_bug(db, bug)
    attaches = tracker == "jira"
    shots = _screenshots(evidence)
    return schemas.FilingPreview(
        tracker=tracker, title=_issue_title(bug),
        markdown=to_markdown(build_report(bug, evidence, attaches_files=attaches)),
        attachments=[Path(e.file_path).name for e in shots] if attaches else [],
        attachments_note=None if attaches or not shots else
        "GitHub's API cannot attach files to issues, so the screenshot stays in Mirmera.",
    )


@router.post("/bugs/{bug_id}/file", response_model=schemas.FileResult)
async def file_bug(bug_id: str, payload: schemas.FileRequest, db: DBSession = Depends(get_db)):
    bug = _bug_or_404(db, bug_id)
    _reviewed(bug)
    done = next((f for f in bug.filings if f.tracker == payload.tracker), None)
    if done:
        raise HTTPException(409, f"Already filed to {payload.tracker} as {done.issue_key}: {done.issue_url}")

    tracker = get_tracker(payload.tracker, _product(db, bug))
    evidence = runner_api.evidence_for_bug(db, bug)
    blocks = build_report(bug, evidence, attaches_files=tracker.supports_attachments,
                          note=payload.note, occurrence=payload.mode == "comment")

    if payload.mode == "new":
        issue = await tracker.create(_issue_title(bug), blocks, _labels(tracker.name, bug))
        key, url = issue.key, issue.url
    else:
        key = payload.issue_key.strip()
        if tracker.name == "github" and not key.startswith("#"):
            key = f"#{key}"
        url = await tracker.comment(key, blocks)

    # The issue exists now, so attachment problems are warnings, never a failure.
    warnings, attached = [], []
    if tracker.supports_attachments:
        for n, shot in enumerate(_screenshots(evidence), 1):
            name = f"mirmera-{bug.id[:8]}-{n}.png"
            try:
                await tracker.attach(key, name, Path(shot.file_path).read_bytes(), "image/png")
                attached.append(name)
            except TrackerError as err:
                warnings.append(f"Screenshot {n} was not attached: {err}")
                logger.warning("Bug %s: screenshot not attached to %s: %s", bug.id, key, err)

    filing = models.BugFiling(bug_id=bug.id, tracker=tracker.name, target=tracker.target, mode=payload.mode,
                              issue_key=key, issue_url=url, attachments=attached)
    db.add(filing)
    verb = "Filed to" if payload.mode == "new" else "Added as a new occurrence to"
    bug.last_change = f"{verb} {tracker.name} {key}"
    db.commit()
    db.refresh(filing)
    return schemas.FileResult(filing=schemas.BugFilingOut.model_validate(filing), warnings=warnings)
