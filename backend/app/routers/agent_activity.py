"""What Mirmera's agents are doing: for the progress in the portal (sidebar, activity page, stories)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import agent_activity, auth, models, schemas
from app.database import get_db

router = APIRouter(tags=["agent activity"])


@router.get("/workspaces/{workspace_id}/agent-activity", response_model=schemas.AgentActivityList)
def workspace_activity(workspace_id: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    """Any member may watch: running work first, then the latest finished."""
    if workspace_id not in auth.roles_of(db, user):
        raise HTTPException(404, "Workspace not found")
    ids = [p.id for p in db.query(models.Project.id).filter(models.Project.org_id == workspace_id)]
    items = agent_activity.activity(db, ids)
    return {"items": items, "running": sum(1 for i in items if i["state"] == "running")}


@router.get("/stories/{story_id}/agent-activity", response_model=schemas.AgentActivityList)
def story_activity(story_id: str, db: DBSession = Depends(get_db)):
    """Work on this story that is running or finished in the last 15 minutes."""
    story = db.get(models.Story, story_id)
    if not story:
        raise HTTPException(404, "Story not found")
    items = agent_activity.activity(db, [story.project_id], story_id=story_id, recent_minutes=15)
    return {"items": items, "running": sum(1 for i in items if i["state"] == "running")}
