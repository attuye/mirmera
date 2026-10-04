import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import ProviderError
from app.ai.routing import provider_for
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/artifacts", tags=["artifacts"])


@router.post("", response_model=schemas.ArtifactOut)
async def add_artifact(project_id: str, payload: schemas.ArtifactCreate, db: DBSession = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    artifact = models.Artifact(project_id=project_id, **payload.model_dump())
    db.add(artifact)
    db.commit()
    db.refresh(artifact)

    # Cache an AI summary up front so charter suggestion doesn't re-summarize every call.
    # A provider problem (missing key, model down) must never block saving the artifact:
    # the summary stays empty and charter suggestion falls back to the raw content.
    # The reason is logged, so a misconfiguration doesn't go unnoticed.
    if artifact.raw_content:
        try:
            provider = provider_for(project, "artifact_summary")
            artifact.ai_summary = await tasks.summarize_artifact(
                provider, raw_content=artifact.raw_content, artifact_type=artifact.type,
            )
            db.commit()
            db.refresh(artifact)
        except ProviderError as err:
            logger.warning("Artifact %s saved without AI summary: %s", artifact.id, err)

    return artifact


@router.get("", response_model=list[schemas.ArtifactOut])
def list_artifacts(project_id: str, db: DBSession = Depends(get_db)):
    return db.query(models.Artifact).filter(models.Artifact.project_id == project_id).all()
