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
from app.ai.routing import provider_for
from app.config import settings
from app.database import get_db
from app.evidence_text import _defang as defang
from app.routers import runner as runner_api
from app.trackers import TRACKER_NAMES, TrackerError, get_tracker, keywords, missing_settings
from app.trackers.fingerprint import FOUND, fingerprint, same_problem, symptom_words
from app.trackers.report import build_report, to_markdown

logger = logging.getLogger(__name__)
router = APIRouter(tags=["trackers"])

VERDICT_ORDER = {"same_problem": -1, "likely_duplicate": 0, "related": 1, "different": 2, "not_assessed": 3}


def _bug_or_404(db: DBSession, bug_id: str) -> models.Bug:
    bug = db.get(models.Bug, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    return bug


def _reviewed(bug: models.Bug) -> None:
    if not bug.kind:
        raise HTTPException(409, "Review this bug first (save it as a bug or a question); only reviewed reports are filed.")


def _project(db: DBSession, bug: models.Bug) -> models.Project:
    return db.get(models.Project, bug.charter.project_id)


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


@router.get("/projects/{project_id}/trackers", response_model=list[schemas.TrackerStatus])
def tracker_status(project_id: str, db: DBSession = Depends(get_db)):
    """Which trackers this project can file to, and what is missing for the others."""
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    out = []
    for name in TRACKER_NAMES:
        missing = missing_settings(name, project)
        out.append(schemas.TrackerStatus(
            name=name, configured=not missing, missing=missing,
            target=project.github_repo if name == "github" else project.jira_project_key,
            supports_attachments=name == "jira", is_mock=settings.mock_trackers,
        ))
    return out


@router.post("/bugs/{bug_id}/duplicates", response_model=schemas.DuplicateCheckOut)
async def check_duplicates(bug_id: str, payload: schemas.TrackerRequest, db: DBSession = Depends(get_db)):
    """Search the tracker (open and closed issues) and have the AI rate each match."""
    bug = _bug_or_404(db, bug_id)
    _reviewed(bug)
    project = _project(db, bug)
    tracker = get_tracker(payload.tracker, project)
    exact = await _already_filed(db, bug, tracker)
    words = keywords(bug.title)
    words += [w for w in symptom_words(bug) if w not in words]  # what shows the bug: its request, its words
    seen = {c.key for c in exact}
    issues = []
    for query in (keywords(bug.title), symptom_words(bug)):
        for i in (await tracker.search(query) if query else []):
            if i.key not in seen:
                seen.add(i.key)
                issues.append(i)
    out = schemas.DuplicateCheckOut(tracker=tracker.name, target=tracker.target, searched_for=words,
                                    candidates=exact, fingerprint=fingerprint(bug))
    if not issues:
        return out

    provider = provider_for(project, "duplicate_check", payload.provider)
    # Issue text comes from the tracker and can be written by anyone: data only, and tag-proof.
    result = await tasks.check_duplicates(
        provider,
        report={"kind": bug.kind, "title": bug.title, "steps": bug.steps_to_reproduce or [],
                "expected": bug.expected, "actual": bug.actual},
        candidates=[{"key": i.key, "title": defang(i.title), "status": "closed" if i.closed else "open",
                     "snippet": defang(i.snippet)} for i in issues],
    )
    verdicts = {v.key: v for v in result.verdicts}
    out.candidates = exact + sorted(
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


async def _already_filed(db: DBSession, bug: models.Bug, tracker) -> list[schemas.DuplicateCandidate]:
    """The same problem, already filed, known without AI: Mirmera's own filing of another bug of the
    project with the same fingerprint, or an issue in the tracker that carries the fingerprint."""
    fp = fingerprint(bug)
    out: dict[str, schemas.DuplicateCandidate] = {}

    def closed_note(closed: bool) -> str:
        return (" It's closed: if the problem is back, add this as a new occurrence (a possible regression)."
                if closed else "")

    for filing, other in same_problem(db, bug, tracker.name):
        try:
            issue = await tracker.issue(filing.issue_key)
            title, url, status, closed = issue.title, issue.url, issue.status, issue.closed
        except TrackerError:
            title, url, status, closed = other.title, filing.issue_url, "unknown", False
        out[filing.issue_key] = schemas.DuplicateCandidate(
            key=filing.issue_key, title=title, url=url, status=status, closed=closed,
            verdict="same_problem",
            reason=f'Mirmera already filed this problem, from the bug "{other.title[:120]}" (fingerprint {fp}).'
                   + closed_note(closed))
    for issue in await tracker.search([fp]):
        text = (issue.body or "") + " " + (issue.snippet or "")
        if issue.key in out or (FOUND.search(text) and fp not in text):
            continue  # another fingerprint: the search matched something else
        out[issue.key] = schemas.DuplicateCandidate(
            key=issue.key, title=issue.title, url=issue.url, status=issue.status, closed=issue.closed,
            snippet=issue.snippet, verdict="same_problem",
            reason=f"This issue carries the same Mirmera fingerprint ({fp}): the same problem, filed from Mirmera."
                   + closed_note(issue.closed))
    return list(out.values())


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

    tracker = get_tracker(payload.tracker, _project(db, bug))
    if payload.mode == "new" and not payload.file_anyway:
        # The last check before a duplicate lands in the tracker: exact matches only, no AI.
        exact = await _already_filed(db, bug, tracker)
        if exact:
            c = exact[0]
            raise HTTPException(409, f"Already filed: {c.key} ({'closed' if c.closed else c.status}) is the same problem "
                                     f"(Mirmera fingerprint {fingerprint(bug)}). Add this as a new occurrence to {c.key}, "
                                     "or file a new issue anyway.")
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
