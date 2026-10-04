"""AI tasks and providers: what each project can route where, and checking a model."""
from fastapi import APIRouter, HTTPException

from app import schemas
from app.ai import check as model_check
from app.ai.base import PROVIDER_NAMES
from app.ai.routing import AI_TASKS, TASK_KEYS, provider_status

router = APIRouter(prefix="/ai", tags=["ai"])


@router.get("", response_model=schemas.AIOverview)
def overview():
    """The AI tasks a project can route, and every provider with whether it is set up."""
    return {"tasks": AI_TASKS, "providers": provider_status()}


@router.post("/check", response_model=schemas.AICheckResult)
async def check(payload: schemas.AICheckRequest):
    """Run one task once on one provider with made-up input. Call it per task to
    check a whole model and show progress as it goes."""
    if payload.provider not in PROVIDER_NAMES:
        raise HTTPException(400, f"Unknown AI provider '{payload.provider}'. Use one of: {', '.join(PROVIDER_NAMES)}")
    if payload.task not in TASK_KEYS:
        raise HTTPException(400, f"Unknown AI task '{payload.task}'")
    return await model_check.check(payload.provider, payload.task)
