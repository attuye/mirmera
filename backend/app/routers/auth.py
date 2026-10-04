"""Sign up, sign in and out, your account, API tokens for the Chrome extension, and workspaces."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession

from app import auth, models, schemas
from app.config import settings
from app.database import get_db
from app.timeutil import utcnow

router = APIRouter(tags=["auth"])


def me(db: DBSession, user: models.User) -> dict:
    rows = (db.query(models.Membership, models.Workspace).join(models.Workspace)
            .filter(models.Membership.user_id == user.id).order_by(models.Workspace.created_at).all())
    return {"user": {"id": user.id, "name": user.name, "email": user.email},
            "workspaces": [{"id": w.id, "name": w.name, "role": m.role} for m, w in rows]}


@router.get("/auth/config", response_model=schemas.AuthConfig)
def auth_config(db: DBSession = Depends(get_db)):
    """What the sign-in page needs before anyone is signed in."""
    has_users = db.query(models.User.id).first() is not None
    unowned = 0 if has_users else db.query(models.Project.id).count()
    return {"allow_signups": settings.allow_signups or not has_users, "has_users": has_users, "unowned_projects": unowned}


@router.post("/auth/signup", response_model=schemas.Me)
def signup(payload: schemas.SignUp, response: Response, db: DBSession = Depends(get_db)):
    first = db.query(models.User.id).first() is None
    if not settings.allow_signups and not first:
        raise HTTPException(403, "New accounts are turned off here. Ask a workspace admin to invite you.")
    email = payload.email.strip().lower()
    if problem := auth.password_problem(payload.password, email):
        raise HTTPException(422, problem)
    if db.query(models.User).filter(models.User.email == email).first():
        raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    user = models.User(email=email, name=payload.name.strip(), password_hash=auth.hash_password(payload.password),
                       last_login_at=utcnow())
    workspace = models.Workspace(name=(payload.workspace_name or "").strip() or f"{user.name}'s workspace")
    db.add_all([user, workspace])
    db.flush()
    db.add(models.Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    if first:
        auth.claim_unowned(db, workspace)  # projects from before accounts existed
    token = auth.new_token(db, user)
    db.commit()
    auth.set_cookie(response, token)
    return me(db, user)


@router.post("/auth/login", response_model=schemas.Me)
def login(payload: schemas.SignIn, request: Request, response: Response, db: DBSession = Depends(get_db)):
    email = payload.email.strip().lower()
    keys = [f"email:{email}", f"ip:{request.client.host if request.client else '?'}"]
    if any(auth.too_many_attempts(k) for k in keys):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes and try again.")
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user or not auth.check_password(payload.password, user.password_hash):
        for k in keys:
            auth.failed_attempt(k)
        raise HTTPException(401, "That email and password don't match an account.")
    user.last_login_at = utcnow()
    token = auth.new_token(db, user)
    db.commit()
    auth.set_cookie(response, token)
    return me(db, user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: DBSession = Depends(get_db)):
    token = request.cookies.get(auth.COOKIE)
    if token:
        auth.revoke(db, token)
        db.commit()
    response.delete_cookie(auth.COOKIE, path="/")


@router.get("/auth/me", response_model=schemas.Me)
def get_me(user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    return me(db, user)


@router.patch("/auth/me", response_model=schemas.Me)
def update_me(payload: schemas.AccountUpdate, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    user = db.merge(user)
    user.name = payload.name.strip()
    db.commit()
    return me(db, user)


@router.post("/auth/password", status_code=204)
def change_password(payload: schemas.PasswordChange, request: Request, response: Response,
                    user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    """Other browsers are signed out; this one gets a fresh session. API tokens stay."""
    user = db.merge(user)
    if not auth.check_password(payload.current_password, user.password_hash):
        raise HTTPException(401, "Your current password is wrong.")
    if problem := auth.password_problem(payload.new_password, user.email):
        raise HTTPException(422, problem)
    user.password_hash = auth.hash_password(payload.new_password)
    db.query(models.AuthToken).filter(models.AuthToken.user_id == user.id, models.AuthToken.kind == "session").delete()
    token = auth.new_token(db, user)
    db.commit()
    auth.set_cookie(response, token)


@router.get("/auth/tokens", response_model=list[schemas.ApiTokenOut])
def list_tokens(user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    return (db.query(models.AuthToken).filter(models.AuthToken.user_id == user.id, models.AuthToken.kind == "api")
            .order_by(models.AuthToken.created_at.desc()).all())


@router.post("/auth/tokens", response_model=schemas.ApiTokenCreated)
def create_token(payload: schemas.ApiTokenCreate, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    """An API token for the Chrome extension. Shown once; only its hash is kept."""
    token = auth.new_token(db, db.merge(user), kind="api", label=payload.label.strip())
    db.commit()
    row = db.query(models.AuthToken).filter(models.AuthToken.token_hash == auth._hash_token(token)).one()
    return {"id": row.id, "label": row.label, "created_at": row.created_at, "last_used_at": None, "token": token}


@router.delete("/auth/tokens/{token_id}", status_code=204)
def delete_token(token_id: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    row = db.get(models.AuthToken, token_id)
    if not row or row.user_id != user.id or row.kind != "api":
        raise HTTPException(404, "Token not found")
    db.delete(row)
    db.commit()


@router.post("/workspaces", response_model=schemas.Me)
def create_workspace(payload: schemas.WorkspaceIn, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    """Another workspace (for another team or client), with you as its owner."""
    workspace = models.Workspace(name=payload.name.strip())
    db.add(workspace)
    db.flush()
    db.add(models.Membership(workspace_id=workspace.id, user_id=user.id, role="owner"))
    db.commit()
    return me(db, user)


@router.patch("/workspaces/{workspace_id}", response_model=schemas.Me)
def rename_workspace(workspace_id: str, payload: schemas.WorkspaceIn, user: models.User = Depends(auth.current_user),
                     db: DBSession = Depends(get_db)):
    role = auth.roles_of(db, user).get(workspace_id)
    if role is None:
        raise HTTPException(404, "Workspace not found")
    if auth.RANK[role] < auth.RANK["admin"]:
        raise HTTPException(403, "Only a workspace admin can rename it.")
    db.get(models.Workspace, workspace_id).name = payload.name.strip()
    db.commit()
    return me(db, user)
