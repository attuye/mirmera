"""Feedback loop: what a session found feeds back into the risk scores.

The AI suggests re-scores for the risks behind the session's charter, each
with a reason and quoted evidence. The tester accepts (optionally with their
own numbers) or rejects each one. Every decision, and every manual re-score,
is kept as the risk's history.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.routing import provider_for
from app.database import get_db
from app.evidence_text import browser_context
from app.routers import runner as runner_api
from app.session_text import bug_line, note_line, riskiest_first
from app.timeutil import utcnow

router = APIRouter(tags=["risk feedback"])

MAX_STEP = 2  # points per dimension per session; one session is limited evidence


def _feedback(db: DBSession, session: models.Session, warnings: list[str] | None = None) -> schemas.SessionRiskFeedback:
    changes = (
        db.query(models.RiskChange)
        .filter(models.RiskChange.session_id == session.id)
        .order_by(models.RiskChange.created_at)
        .all()
    )
    # Suggestions needing a decision first, then decided ones, then "no change".
    order = {"proposed": 0, "accepted": 1, "rejected": 1, "no_change": 2}
    changes.sort(key=lambda c: (order.get(c.status, 3), c.risk.ref))
    unreviewed = db.query(models.Bug).filter(models.Bug.session_id == session.id, models.Bug.kind.is_(None)).count()
    return schemas.SessionRiskFeedback(
        changes=[schemas.RiskChangeOut.of(c) for c in changes],
        risk_count=len(session.charter.risks),
        unreviewed_bugs=unreviewed,
        warnings=warnings or [],
    )


def _session_or_404(db: DBSession, session_id: str) -> models.Session:
    session = db.get(models.Session, session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


def _change_or_404(db: DBSession, change_id: str) -> models.RiskChange:
    change = db.get(models.RiskChange, change_id)
    if not change:
        raise HTTPException(404, "Suggestion not found")
    return change


def _clamp(current: int, wanted: int) -> int:
    return max(current - MAX_STEP, min(current + MAX_STEP, wanted))


@router.get("/sessions/{session_id}/risk-updates", response_model=schemas.SessionRiskFeedback)
def get_suggestions(session_id: str, db: DBSession = Depends(get_db)):
    return _feedback(db, _session_or_404(db, session_id))


@router.post("/sessions/{session_id}/risk-updates", response_model=schemas.SessionRiskFeedback)
async def suggest(session_id: str, payload: schemas.RiskSuggestRequest, db: DBSession = Depends(get_db)):
    """Ask for suggestions. Asking again replaces the undecided ones; risks the
    tester already decided on for this session are left alone."""
    session = _session_or_404(db, session_id)
    if not session.ended_at:
        raise HTTPException(409, "End the session first: suggestions are based on the whole session.")
    charter = session.charter
    if not charter.risks:
        raise HTTPException(409, "This session's charter has no linked risks, so there is nothing to re-score.")

    previous = db.query(models.RiskChange).filter(models.RiskChange.session_id == session_id).all()
    decided = {c.risk_id for c in previous if c.status in ("accepted", "rejected")}
    for c in previous:
        if c.status in ("proposed", "no_change"):
            db.delete(c)
    risks = [r for r in riskiest_first(charter.risks) if r.id not in decided]
    if not risks:
        db.commit()
        return _feedback(db, session, ["Every risk already has a decision for this session."])

    notes = db.query(models.Note).filter(models.Note.session_id == session_id).order_by(models.Note.timestamp).all()
    bugs = db.query(models.Bug).filter(models.Bug.session_id == session_id).order_by(models.Bug.created_at).all()
    recorded = (
        db.query(models.Evidence).filter(models.Evidence.session_id == session_id)
        .order_by(models.Evidence.captured_at).all()
    )
    debrief = session.debrief
    project = db.get(models.Project, charter.project_id)
    provider = provider_for(project, "risk_update", payload.provider)

    result = await tasks.suggest_risk_updates(
        provider,
        charter_mission=charter.mission_statement,
        risks=[{"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode,
                "quality_criterion": r.quality_criterion, "impact": r.impact, "likelihood": r.likelihood,
                "rationale": r.rationale, "tester_notes": r.tester_notes} for r in risks],
        notes=[note_line(n, session.started_at) for n in notes],
        bugs=[bug_line(b) for b in bugs],
        browser=browser_context(recorded, session.started_at, used=bool(recorded)),
        debrief={"results": debrief.results or [], "obstacles": debrief.obstacles or []} if debrief else None,
    )

    # The model may only touch the risks we sent, once each, within the step limit.
    by_ref = {r.ref: r for r in risks}
    seen: set[str] = set()
    warnings: list[str] = []
    provenance = {"ai_provider": provider.name, "ai_model": provider.model,
                  "prompt_version": tasks.PROMPT_VERSIONS["risk_update"]}

    def record(risk: models.Risk, status: str, impact: int, likelihood: int, reason: str, evidence: list[str]):
        db.add(models.RiskChange(
            risk_id=risk.id, session_id=session_id, source="ai", status=status,
            from_impact=risk.impact, from_likelihood=risk.likelihood, to_impact=impact, to_likelihood=likelihood,
            reason=reason, evidence=evidence, **provenance,
        ))

    for u in result.updates:
        risk = by_ref.get(u.risk_ref)
        if not risk:
            warnings.append(f"Ignored a suggestion for unknown risk {u.risk_ref}")
            continue
        if u.risk_ref in seen:
            warnings.append(f"Ignored a second suggestion for {u.risk_ref}")
            continue
        seen.add(u.risk_ref)
        impact, likelihood = _clamp(risk.impact, u.impact), _clamp(risk.likelihood, u.likelihood)
        reason = u.reason
        if (impact, likelihood) != (u.impact, u.likelihood):
            reason += f" (capped: at most {MAX_STEP} points per session)"
        if (impact, likelihood) == (risk.impact, risk.likelihood):
            record(risk, "no_change", impact, likelihood, reason, u.evidence)
        else:
            record(risk, "proposed", impact, likelihood, reason, u.evidence)

    for u in result.unchanged:
        risk = by_ref.get(u.risk_ref)
        if risk and u.risk_ref not in seen:
            seen.add(u.risk_ref)
            record(risk, "no_change", risk.impact, risk.likelihood, u.reason, [])
    for ref, risk in by_ref.items():
        if ref not in seen:
            record(risk, "no_change", risk.impact, risk.likelihood, "No suggestion was made for this risk.", [])

    db.commit()
    return _feedback(db, session, warnings)


@router.post("/risk-changes/{change_id}/accept", response_model=schemas.RiskChangeOut)
def accept(change_id: str, payload: schemas.RiskChangeDecision, db: DBSession = Depends(get_db)):
    change = _change_or_404(db, change_id)
    if change.status != "proposed":
        raise HTTPException(409, f"This suggestion is already {change.status.replace('_', ' ')}.")
    risk = change.risk
    if (risk.impact, risk.likelihood) != (change.from_impact, change.from_likelihood):
        raise HTTPException(
            409,
            f"{risk.ref} was re-scored since this suggestion (now I{risk.impact}xL{risk.likelihood}). "
            "Ask for suggestions again.",
        )
    impact = payload.impact or change.to_impact
    likelihood = payload.likelihood or change.to_likelihood
    change.status = "accepted"
    change.final_impact, change.final_likelihood = impact, likelihood
    change.tester_note = payload.note
    change.decided_at = utcnow()
    adjusted = "" if (impact, likelihood) == (change.to_impact, change.to_likelihood) else " (adjusted by tester)"
    risk.last_change = (
        f"Re-scored I{risk.impact}xL{risk.likelihood} -> I{impact}xL{likelihood} after a session{adjusted}"
    )
    risk.impact, risk.likelihood = impact, likelihood
    db.commit()
    db.refresh(change)
    return schemas.RiskChangeOut.of(change)


@router.post("/risk-changes/{change_id}/reject", response_model=schemas.RiskChangeOut)
def reject(change_id: str, payload: schemas.RiskChangeDecision, db: DBSession = Depends(get_db)):
    change = _change_or_404(db, change_id)
    if change.status != "proposed":
        raise HTTPException(409, f"This suggestion is already {change.status.replace('_', ' ')}.")
    change.status = "rejected"
    change.tester_note = payload.note
    change.decided_at = utcnow()
    db.commit()
    db.refresh(change)
    return schemas.RiskChangeOut.of(change)


@router.get("/risks/{risk_id}/history", response_model=list[schemas.RiskChangeOut])
def history(risk_id: str, db: DBSession = Depends(get_db)):
    """Newest first: suggestions and decisions from sessions, and manual re-scores."""
    risk = db.get(models.Risk, risk_id)
    if not risk:
        raise HTTPException(404, "Risk not found")
    return [schemas.RiskChangeOut.of(c) for c in sorted(risk.changes, key=lambda c: c.created_at, reverse=True)]
