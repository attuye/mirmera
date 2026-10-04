import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import models, schemas
from app.ai import tasks
from app.ai.base import ProviderError
from app.ai.routing import provider_for
from app.database import get_db
from app import importers

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/artifacts", tags=["artifacts"])


def _project_or_404(db: DBSession, project_id: str) -> models.Project:
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.post("", response_model=schemas.ArtifactOut)
async def add_artifact(project_id: str, payload: schemas.ArtifactCreate, db: DBSession = Depends(get_db)):
    project = _project_or_404(db, project_id)
    return await _save(db, project, models.Artifact(project_id=project_id, **payload.model_dump()))


@router.post("/import", response_model=schemas.ArtifactOut)
async def import_artifact(project_id: str, payload: schemas.ArtifactImport, db: DBSession = Depends(get_db)):
    """A pull request, past bugs from GitHub or Jira, or a local git diff, saved as an artifact."""
    project = _project_or_404(db, project_id)
    try:
        if payload.source == "github_pr":
            kind, label, content, source = await importers.github_pr(project, payload.ref)
        elif payload.source == "github_issues":
            kind, label, content, source = await importers.github_issues(project, payload.labels, payload.limit)
        elif payload.source == "jira_issues":
            kind, label, content, source = await importers.jira_issues(project, payload.jql, payload.limit)
        else:
            kind, label, content, source = importers.git_diff(payload.repo_path, payload.base, payload.head)
    except importers.ImportProblem as err:
        raise HTTPException(400, str(err))
    return await _save(db, project, models.Artifact(project_id=project_id, type=kind, label=label,
                                                    raw_content=content, file_ref=source))


async def _save(db: DBSession, project: models.Project, artifact: models.Artifact) -> models.Artifact:
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
