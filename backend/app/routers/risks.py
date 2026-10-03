"""The risk model: product model (SFDIPOT) -> risks -> charters from risks.

The AI proposes; the tester decides. AI risks start as "proposed" and only
accepted risks can be turned into charters.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import get_provider
from app.database import get_db
from app.timeutil import utcnow

router = APIRouter(tags=["risks"])


def _project_or_404(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _latest_model(db: DBSession, project_id: str) -> models.ProductModel | None:
    return (
        db.query(models.ProductModel)
        .filter(models.ProductModel.project_id == project_id)
        .order_by(models.ProductModel.created_at.desc())
        .first()
    )


def _next_ref_number(db: DBSession, project_id: str) -> int:
    refs = db.query(models.Risk.ref).filter(models.Risk.project_id == project_id).all()
    numbers = [int(r[1:]) for (r,) in refs if r[1:].isdigit()]
    return max(numbers, default=0) + 1


# ---------- product model ----------

@router.post("/projects/{project_id}/product-model", response_model=schemas.ProductModelOut)
async def build_product_model(project_id: str, payload: schemas.ProductModelRequest, db: DBSession = Depends(get_db)):
    project = _project_or_404(db, project_id)
    artifacts = (
        db.query(models.Artifact)
        .filter(models.Artifact.id.in_(payload.artifact_ids), models.Artifact.project_id == project_id)
        .all()
    )
    if not artifacts:
        raise HTTPException(400, "No matching artifacts found for this project")

    provider = get_provider(payload.provider or project.default_ai_provider)
    result, truncated = await tasks.build_product_model(
        provider,
        project_name=project.name,
        testing_mission=project.testing_mission,
        # The full content, not the cached summary: the model needs the detail.
        artifacts=[(a.type, a.label, a.raw_content or a.ai_summary or "") for a in artifacts],
    )
    snapshot = models.ProductModel(
        project_id=project_id,
        summary=result.summary,
        elements=[e.model_dump() for e in result.elements],
        unknowns=result.unknowns,
        artifact_ids=[a.id for a in artifacts],
        ai_provider=provider.name,
        ai_model=provider.model,
        prompt_version=tasks.PROMPT_VERSIONS["product_model"],
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    out = schemas.ProductModelOut.model_validate(snapshot)
    out.truncated_artifacts = truncated
    return out


@router.get("/projects/{project_id}/product-model", response_model=schemas.ProductModelOut)
def get_product_model(project_id: str, db: DBSession = Depends(get_db)):
    _project_or_404(db, project_id)
    snapshot = _latest_model(db, project_id)
    if not snapshot:
        raise HTTPException(404, "No product model yet. Build one from artifacts first.")
    return snapshot


# ---------- risks ----------

@router.post("/projects/{project_id}/risks/analyze", response_model=list[schemas.RiskOut])
async def analyze_risks(project_id: str, payload: schemas.RiskAnalyzeRequest, db: DBSession = Depends(get_db)):
    project = _project_or_404(db, project_id)
    snapshot = _latest_model(db, project_id)
    if not snapshot:
        raise HTTPException(409, "Build a product model first: POST /projects/{id}/product-model")

    existing = (
        db.query(models.Risk)
        .filter(models.Risk.project_id == project_id, models.Risk.status != "retired")
        .all()
    )
    provider = get_provider(payload.provider or project.default_ai_provider)
    proposed = await tasks.analyze_risks(
        provider,
        project_name=project.name,
        testing_mission=project.testing_mission,
        product_model={"summary": snapshot.summary, "elements": snapshot.elements, "unknowns": snapshot.unknowns},
        max_risks=payload.max_risks,
        existing_titles=[r.title for r in existing],
    )

    # Number by score, so the riskiest new item gets the lowest ref.
    proposed = sorted(proposed, key=lambda r: r.impact * r.likelihood, reverse=True)
    next_number = _next_ref_number(db, project_id)
    created = []
    for offset, r in enumerate(proposed):
        risk = models.Risk(
            project_id=project_id,
            ref=f"R{next_number + offset}",
            **r.model_dump(),
            status="proposed",
            source="ai",
            ai_provider=provider.name,
            ai_model=provider.model,
            prompt_version=tasks.PROMPT_VERSIONS["risk_analysis"],
        )
        db.add(risk)
        created.append(risk)
    db.commit()
    for risk in created:
        db.refresh(risk)
    return created


@router.get("/projects/{project_id}/risks", response_model=list[schemas.RiskOut])
def list_risks(project_id: str, status: schemas.RiskStatus | None = None, db: DBSession = Depends(get_db)):
    """Highest score first. Retired risks are left out unless asked for."""
    _project_or_404(db, project_id)
    query = db.query(models.Risk).filter(models.Risk.project_id == project_id)
    query = query.filter(models.Risk.status == status) if status else query.filter(models.Risk.status != "retired")
    risks = query.all()
    return sorted(risks, key=lambda r: (-r.score, int(r.ref[1:]) if r.ref[1:].isdigit() else 0))


@router.post("/projects/{project_id}/risks", response_model=schemas.RiskOut)
def create_risk(project_id: str, payload: schemas.RiskCreate, db: DBSession = Depends(get_db)):
    _project_or_404(db, project_id)
    risk = models.Risk(
        project_id=project_id,
        ref=f"R{_next_ref_number(db, project_id)}",
        **payload.model_dump(),
        evidence="tester",
        status="accepted",
        source="manual",
        last_change="Added by tester",
    )
    db.add(risk)
    db.commit()
    db.refresh(risk)
    return risk


@router.patch("/risks/{risk_id}", response_model=schemas.RiskOut)
def update_risk(risk_id: str, payload: schemas.RiskUpdate, db: DBSession = Depends(get_db)):
    risk = db.get(models.Risk, risk_id)
    if not risk:
        raise HTTPException(404, "Risk not found")

    changes = payload.model_dump(exclude_unset=True)
    notes = []
    if "status" in changes and changes["status"] != risk.status:
        notes.append({"accepted": "Accepted", "retired": "Retired", "proposed": "Reopened"}[changes["status"]] + " by tester")
    new_impact = changes.get("impact", risk.impact)
    new_likelihood = changes.get("likelihood", risk.likelihood)
    if (new_impact, new_likelihood) != (risk.impact, risk.likelihood):
        notes.append(f"Re-scored I{risk.impact}xL{risk.likelihood} -> I{new_impact}xL{new_likelihood}")
    if any(k in changes for k in ("title", "failure_mode", "quality_criterion")):
        notes.append("Edited by tester")
    if "tester_notes" in changes and changes["tester_notes"] != risk.tester_notes:
        notes.append("Notes updated")

    if (new_impact, new_likelihood) != (risk.impact, risk.likelihood):
        # A manual re-score goes into the risk's history next to session suggestions.
        db.add(models.RiskChange(
            risk_id=risk.id, source="tester", status="accepted",
            from_impact=risk.impact, from_likelihood=risk.likelihood,
            to_impact=new_impact, to_likelihood=new_likelihood,
            final_impact=new_impact, final_likelihood=new_likelihood,
            reason="Re-scored by tester", tester_note=changes.get("tester_notes"), decided_at=utcnow(),
        ))

    for field, value in changes.items():
        setattr(risk, field, value)
    if notes:
        risk.last_change = "; ".join(notes)
    db.commit()
    db.refresh(risk)
    return risk


# ---------- charters from risks ----------

@router.post("/projects/{project_id}/charters/from-risks", response_model=schemas.ChartersFromRisksOut)
async def charters_from_risks(project_id: str, payload: schemas.ChartersFromRisksRequest, db: DBSession = Depends(get_db)):
    """Drafts charters for accepted risks. Nothing is saved: accept a draft by
    posting it to /projects/{id}/charters, like the other suggestions."""
    project = _project_or_404(db, project_id)
    query = db.query(models.Risk).filter(models.Risk.project_id == project_id, models.Risk.status == "accepted")
    if payload.risk_ids is not None:
        query = query.filter(models.Risk.id.in_(payload.risk_ids))
    risks = sorted(query.all(), key=lambda r: -r.score)
    if not risks:
        raise HTTPException(400, "No accepted risks to plan from. Accept at least one risk first.")

    snapshot = _latest_model(db, project_id)
    provider = get_provider(payload.provider or project.default_ai_provider)
    drafts = await tasks.charters_from_risks(
        provider,
        project_name=project.name,
        testing_mission=project.testing_mission,
        product_summary=snapshot.summary if snapshot else "(no product model yet)",
        risks=[
            {
                "ref": r.ref, "title": r.title, "failure_mode": r.failure_mode,
                "quality_criterion": r.quality_criterion, "score": r.score,
                "evidence": r.evidence, "tester_notes": r.tester_notes,
            }
            for r in risks
        ],
        max_charters=payload.max_charters,
    )

    # The model may only reference risks we sent; anything else is dropped.
    by_ref = {r.ref: r for r in risks}
    charters, warnings = [], []
    for d in drafts:
        refs = [ref for ref in d.risk_refs if ref in by_ref]
        unknown = [ref for ref in d.risk_refs if ref not in by_ref]
        if unknown:
            warnings.append(f"Ignored unknown risk refs {unknown} in charter on '{d.target}'")
        if not refs:
            warnings.append(f"Dropped charter on '{d.target}': it covers none of the given risks")
            continue
        charters.append(schemas.RiskCharterDraft(
            mission_statement=f"Explore {d.target} with {d.resources} to discover {d.information}",
            rationale=d.rationale,
            priority=d.priority,
            risk_ids=[by_ref[ref].id for ref in refs],
            risk_refs=refs,
            test_ideas=d.test_ideas,
            oracles=list(d.oracles),
        ))

    covered = {ref for c in charters for ref in c.risk_refs}
    return schemas.ChartersFromRisksOut(
        charters=charters,
        uncovered_risk_refs=[r.ref for r in risks if r.ref not in covered],
        warnings=warnings,
    )
