"""AI tasks and providers: what each project can route where, and checking a model.
With a project, providers are checked with that project's workspace keys."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app import auth, keys, models, schemas
from app.ai import check as model_check
from app.ai.base import PROVIDER_NAMES
from app.ai.routing import AI_TASKS, TASK_KEYS, provider_status
from app.database import get_db

router = APIRouter(prefix="/ai", tags=["ai"])


def _keys(request: Request, db: DBSession, project_id: str | None):
    if not project_id:
        return None if keys.settings.server_keys_for_all else keys.Keys()
    auth.check_project(request, db, project_id, role="viewer")
    return keys.keys_for(db.get(models.Project, project_id))


@router.get("", response_model=schemas.AIOverview)
def overview(request: Request, project_id: str | None = None, db: DBSession = Depends(get_db)):
    """The AI tasks a project can route, and every provider with whether it is set up."""
    return {"tasks": AI_TASKS, "providers": provider_status(_keys(request, db, project_id))}


@router.post("/check", response_model=schemas.AICheckResult)
async def check(payload: schemas.AICheckRequest, request: Request, db: DBSession = Depends(get_db)):
    """Run one task once on one provider with made-up input. Call it per task to
    check a whole model and show progress as it goes."""
    if payload.provider not in PROVIDER_NAMES:
        raise HTTPException(400, f"Unknown AI provider '{payload.provider}'. Use one of: {', '.join(PROVIDER_NAMES)}")
    if payload.task not in TASK_KEYS:
        raise HTTPException(400, f"Unknown AI task '{payload.task}'")
    return await model_check.check(payload.provider, payload.task, _keys(request, db, payload.project_id))
