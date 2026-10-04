"""A workspace's keys for GitHub, Jira, AI providers, Telegram and Slack (admins only).
Secrets go in and never come back out: the page shows whether each is set and how it ends."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session as DBSession

from app import auth, keys, models, schemas
from app.config import settings
from app.database import get_db

router = APIRouter(tags=["workspace keys"])


def _admin(db: DBSession, user: models.User, workspace_id: str) -> None:
    role = auth.roles_of(db, user).get(workspace_id)
    if role is None:
        raise HTTPException(404, "Workspace not found")
    if auth.RANK[role] < auth.RANK["admin"]:
        raise HTTPException(403, "Only a workspace admin can see or change its keys.")


def _out(db: DBSession, workspace_id: str) -> dict:
    return {"keys": keys.overview(db, workspace_id), "server_keys_for_all": settings.server_keys_for_all}


@router.get("/workspaces/{workspace_id}/keys", response_model=schemas.WorkspaceKeysOut)
def list_keys(workspace_id: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    _admin(db, user, workspace_id)
    return _out(db, workspace_id)


@router.put("/workspaces/{workspace_id}/keys", response_model=schemas.WorkspaceKeysOut)
def save_keys(workspace_id: str, payload: schemas.WorkspaceKeysIn, user: models.User = Depends(auth.current_user),
              db: DBSession = Depends(get_db)):
    """Only the keys named are changed; an empty value removes that key."""
    _admin(db, user, workspace_id)
    unknown = sorted(set(payload.values) - set(keys.NAMES))
    if unknown:
        raise HTTPException(400, f"Unknown keys: {', '.join(unknown)}")
    for name in ("jira_base_url", "openai_base_url"):
        value = (payload.values.get(name) or "").strip()
        if value and not value.startswith(("http://", "https://")):
            raise HTTPException(422, f"{name.replace('_', ' ')} must start with http:// or https://")
    keys.save(db, workspace_id, payload.values, user.id)
    db.commit()
    return _out(db, workspace_id)
