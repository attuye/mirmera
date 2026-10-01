"""Bug reports: the AI drafts, the tester reviews.

A bug logged during a session is "unreviewed" (kind is empty). The tester can
ask the AI for a draft report, which is stored as-is in `ai_draft` and never
overwrites anything, then saves their own version as a bug or a question.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import get_provider
from app.database import get_db
from app.evidence_text import browser_context
from app.routers import runner as runner_api
from app.timeutil import utcnow

router = APIRouter(prefix="/bugs", tags=["bugs"])


def _bug_or_404(db: DBSession, bug_id: str) -> models.Bug:
    bug = db.get(models.Bug, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    return bug


@router.get("/{bug_id}", response_model=schemas.BugOut)
def get_bug(bug_id: str, db: DBSession = Depends(get_db)):
    return _bug_or_404(db, bug_id)


@router.post("/{bug_id}/draft", response_model=schemas.BugOut)
async def draft_report(bug_id: str, payload: schemas.BugDraftRequest, db: DBSession = Depends(get_db)):
    """Ask the AI for a draft report. It is saved in `ai_draft` only; the
    reviewed fields stay untouched until the tester saves their version."""
    bug = _bug_or_404(db, bug_id)
    charter = bug.charter
    product = db.get(models.Product, charter.product_id)

    provider = get_provider(payload.provider or product.default_ai_provider)
    runner_api.sync_signals(db, bug.session_id)  # include anything the open browser saw since the last poll
    browser = browser_context(
        runner_api.evidence_for_bug(db, bug), bug.session.started_at,
        used=runner_api.browser_used(db, bug.session_id),
    )
    draft = await tasks.draft_bug_report(
        provider,
        charter_mission=charter.mission_statement,
        risks=[
            {"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode,
             "quality_criterion": r.quality_criterion, "impact": r.impact, "likelihood": r.likelihood}
            for r in charter.risks
        ],
        note=bug.title + (f"\n{bug.description}" if bug.description else ""),
        trail=bug.reproduction_trail or [],
        environment=bug.environment or {},
        browser=browser,
    )
    bug.ai_draft = draft.model_dump()
    bug.ai_provider = provider.name
    bug.ai_model = provider.model
    bug.prompt_version = tasks.PROMPT_VERSIONS["bug_report"]
    db.commit()
    db.refresh(bug)
    return bug


@router.patch("/{bug_id}", response_model=schemas.BugOut)
def review_bug(bug_id: str, payload: schemas.BugReview, db: DBSession = Depends(get_db)):
    """Save the tester's version of the report."""
    bug = _bug_or_404(db, bug_id)
    changes = payload.model_dump(exclude_unset=True)

    # Check the rule against the report as it will be after this change.
    kind = changes.get("kind", bug.kind)
    heuristic = changes.get("oracle_heuristic", bug.oracle_heuristic)
    explanation = changes.get("oracle_explanation", bug.oracle_explanation)
    if kind == "bug" and not (heuristic and (explanation or "").strip()):
        raise HTTPException(
            400,
            "A bug must name the oracle that makes it a problem (a heuristic and why it applies). "
            "If you can't say why it's wrong yet, save it as a question.",
        )

    for field, value in changes.items():
        setattr(bug, field, value)
    if "kind" in changes:
        bug.reviewed_at = utcnow()
        bug.last_change = f"Reviewed by tester as a {kind}"
    elif changes:
        bug.last_change = "Edited by tester"
    db.commit()
    db.refresh(bug)
    return bug
