"""Workspace members and invitations, and password resets.

Invitations: an admin invites an email with a role; the link (emailed, or shown to the
admin to send) lets that person join, signing up if they have no account yet. Only
someone signed in with the invited email can accept, so a forwarded link is useless.
"""
import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession

from app import auth, mailer, models, schemas
from app.config import settings
from app.database import get_db
from app.routers.auth import me
from app.timeutil import utcnow

log = logging.getLogger(__name__)
router = APIRouter(tags=["members"])
INVITE_DAYS = 7
RESET_HOURS = 1


def app_link(request: Request, path: str) -> str:
    """An address in the app: PUBLIC_APP_URL, or the frontend this request came from."""
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    origin = request.headers.get("origin")
    base = settings.public_app_url or (origin if origin in origins else None) or (origins[0] if origins else "")
    return f"{base.rstrip('/')}{path}"


def _role_in(db: DBSession, user: models.User, workspace_id: str, at_least: str = "viewer") -> str:
    role = auth.roles_of(db, user).get(workspace_id)
    if role is None:
        raise HTTPException(404, "Workspace not found")
    if auth.RANK[role] < auth.RANK[at_least]:
        raise HTTPException(403, f"Your role here ({role}) can't do this; ask a workspace admin.")
    return role


def _owners(db: DBSession, workspace_id: str) -> int:
    return db.query(models.Membership).filter(models.Membership.workspace_id == workspace_id,
                                              models.Membership.role == "owner").count()


def _members(db: DBSession, workspace_id: str) -> dict:
    ws = db.get(models.Workspace, workspace_id)
    rows = (db.query(models.Membership, models.User).join(models.User)
            .filter(models.Membership.workspace_id == workspace_id).order_by(models.Membership.created_at).all())
    invites = (db.query(models.Invitation).filter(models.Invitation.workspace_id == workspace_id,
                                                  models.Invitation.accepted_at.is_(None))
               .order_by(models.Invitation.created_at.desc()).all())
    now = utcnow()
    return {
        "workspace": {"id": ws.id, "name": ws.name},
        "members": [{"user_id": u.id, "name": u.name, "email": u.email, "role": m.role, "joined_at": m.created_at}
                    for m, u in rows],
        "invitations": [{"id": i.id, "email": i.email, "role": i.role, "created_at": i.created_at, "expires_at": i.expires_at,
                         "expired": i.expires_at < now, "invited_by": i.inviter.name if i.inviter else None} for i in invites],
        "email_configured": mailer.configured(),
    }


@router.get("/workspaces/{workspace_id}/members", response_model=schemas.MembersOut)
def list_members(workspace_id: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    _role_in(db, user, workspace_id)
    return _members(db, workspace_id)


@router.post("/workspaces/{workspace_id}/invitations", response_model=schemas.InvitationCreated)
def invite(workspace_id: str, payload: schemas.InvitationIn, request: Request,
           user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    mine = _role_in(db, user, workspace_id, "admin")
    if payload.role == "owner" and mine != "owner":
        raise HTTPException(403, "Only an owner can invite another owner.")
    email = payload.email.strip().lower()
    existing = db.query(models.User).filter(models.User.email == email).first()
    if existing and workspace_id in auth.roles_of(db, existing):
        raise HTTPException(409, f"{email} is already a member of this workspace.")
    db.query(models.Invitation).filter(models.Invitation.workspace_id == workspace_id, models.Invitation.email == email,
                                       models.Invitation.accepted_at.is_(None)).delete()  # a new invite replaces the old
    token = secrets.token_urlsafe(32)
    invitation = models.Invitation(workspace_id=workspace_id, email=email, role=payload.role, token_hash=auth._hash_token(token),
                                   invited_by=user.id, expires_at=utcnow() + timedelta(days=INVITE_DAYS))
    db.add(invitation)
    db.commit()
    link = app_link(request, f"/invite/{token}")
    ws = db.get(models.Workspace, workspace_id)
    emailed = mailer.send(email, f"{user.name} invited you to {ws.name} on Mirmera",
                          f"{user.name} invited you to join the workspace \"{ws.name}\" on Mirmera as a {payload.role}.\n\n"
                          f"Open this link to accept (it works for {INVITE_DAYS} days):\n{link}\n\n"
                          "If you weren't expecting this, you can ignore it.")
    return {"id": invitation.id, "email": email, "role": payload.role, "link": link, "emailed": emailed}


@router.delete("/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(workspace_id: str, invitation_id: str, user: models.User = Depends(auth.current_user),
                      db: DBSession = Depends(get_db)):
    _role_in(db, user, workspace_id, "admin")
    invitation = db.get(models.Invitation, invitation_id)
    if not invitation or invitation.workspace_id != workspace_id:
        raise HTTPException(404, "Invitation not found")
    db.delete(invitation)
    db.commit()


@router.patch("/workspaces/{workspace_id}/members/{user_id}", response_model=schemas.MembersOut)
def change_role(workspace_id: str, user_id: str, payload: schemas.RoleChange, user: models.User = Depends(auth.current_user),
                db: DBSession = Depends(get_db)):
    mine = _role_in(db, user, workspace_id, "admin")
    member = db.query(models.Membership).filter(models.Membership.workspace_id == workspace_id,
                                                models.Membership.user_id == user_id).first()
    if not member:
        raise HTTPException(404, "Member not found")
    if "owner" in (member.role, payload.role) and mine != "owner":
        raise HTTPException(403, "Only an owner can make or change an owner.")
    if member.role == "owner" and payload.role != "owner" and _owners(db, workspace_id) == 1:
        raise HTTPException(409, "A workspace needs at least one owner. Make someone else an owner first.")
    member.role = payload.role
    db.commit()
    return _members(db, workspace_id)


@router.delete("/workspaces/{workspace_id}/members/{user_id}", response_model=schemas.MembersOut)
def remove_member(workspace_id: str, user_id: str, user: models.User = Depends(auth.current_user),
                  db: DBSession = Depends(get_db)):
    """An admin removes someone. Their sessions, notes and bugs stay in the projects."""
    if user_id == user.id:
        raise HTTPException(400, "To leave the workspace yourself, use Leave.")
    mine = _role_in(db, user, workspace_id, "admin")
    member = db.query(models.Membership).filter(models.Membership.workspace_id == workspace_id,
                                                models.Membership.user_id == user_id).first()
    if not member:
        raise HTTPException(404, "Member not found")
    if member.role == "owner" and mine != "owner":
        raise HTTPException(403, "Only an owner can remove an owner.")
    db.delete(member)
    db.commit()
    return _members(db, workspace_id)


@router.post("/workspaces/{workspace_id}/leave", response_model=schemas.Me)
def leave(workspace_id: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    mine = _role_in(db, user, workspace_id)
    if mine == "owner" and _owners(db, workspace_id) == 1:
        raise HTTPException(409, "You're the only owner. Make someone else an owner first.")
    db.query(models.Membership).filter(models.Membership.workspace_id == workspace_id,
                                       models.Membership.user_id == user.id).delete()
    db.commit()
    return me(db, user)


def _invitation(db: DBSession, token: str) -> models.Invitation:
    invitation = db.query(models.Invitation).filter(models.Invitation.token_hash == auth._hash_token(token)).first()
    if not invitation or invitation.accepted_at:
        raise HTTPException(404, "This invitation doesn't exist or was already used. Ask for a new one.")
    if invitation.expires_at < utcnow():
        raise HTTPException(410, "This invitation has expired. Ask a workspace admin for a new one.")
    return invitation


@router.get("/invitations/{token}", response_model=schemas.InvitationView)
def view_invitation(token: str, db: DBSession = Depends(get_db)):
    """What the invitation page shows before anyone is signed in."""
    i = _invitation(db, token)
    has_account = db.query(models.User.id).filter(models.User.email == i.email).first() is not None
    return {"workspace_name": i.workspace.name, "email": i.email, "role": i.role,
            "invited_by": i.inviter.name if i.inviter else None, "has_account": has_account}


def _join(db: DBSession, invitation: models.Invitation, user: models.User) -> None:
    if invitation.workspace_id not in auth.roles_of(db, user):
        db.add(models.Membership(workspace_id=invitation.workspace_id, user_id=user.id, role=invitation.role))
    invitation.accepted_at = utcnow()


@router.post("/invitations/{token}/accept", response_model=schemas.Me)
def accept_invitation(token: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    invitation = _invitation(db, token)
    if user.email != invitation.email:
        raise HTTPException(403, f"This invitation is for {invitation.email}. Sign in with that email to accept it.")
    _join(db, invitation, user)
    db.commit()
    return me(db, user)


@router.post("/invitations/{token}/signup", response_model=schemas.Me)
def signup_by_invitation(token: str, payload: schemas.InvitedSignUp, response: Response, db: DBSession = Depends(get_db)):
    """A new account for the invited email, straight into the workspace (works with ALLOW_SIGNUPS=false)."""
    invitation = _invitation(db, token)
    if db.query(models.User).filter(models.User.email == invitation.email).first():
        raise HTTPException(409, "An account with this email already exists. Sign in to accept.")
    if problem := auth.password_problem(payload.password, invitation.email):
        raise HTTPException(422, problem)
    user = models.User(email=invitation.email, name=payload.name.strip(), password_hash=auth.hash_password(payload.password),
                       last_login_at=utcnow())
    db.add(user)
    db.flush()
    _join(db, invitation, user)
    session_token = auth.new_token(db, user)
    db.commit()
    auth.set_cookie(response, session_token)
    return me(db, user)


@router.post("/auth/forgot", status_code=204)
def forgot_password(payload: schemas.ForgotPassword, request: Request, db: DBSession = Depends(get_db)):
    """Always answers the same, so it can't be used to find out who has an account."""
    email = payload.email.strip().lower()
    key = f"forgot:{request.client.host if request.client else '?'}"
    if auth.too_many_attempts(key):
        raise HTTPException(429, "Too many requests. Wait 15 minutes and try again.")
    auth.failed_attempt(key)  # counts every request, not just failures
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        return
    token = secrets.token_urlsafe(32)
    db.add(models.PasswordReset(user_id=user.id, token_hash=auth._hash_token(token),
                                expires_at=utcnow() + timedelta(hours=RESET_HOURS)))
    db.commit()
    link = app_link(request, f"/reset/{token}")
    sent = mailer.send(email, "Reset your Mirmera password",
                       f"Someone (hopefully you) asked to reset the password for {email} on Mirmera.\n\n"
                       f"Choose a new password here (the link works for an hour, once):\n{link}\n\n"
                       "If it wasn't you, ignore this email; your password stays the same.")
    if not sent:  # no email set up: the person running the server can pass the link on
        log.warning("Email isn't set up (SMTP_HOST), so the password reset link for %s is only here: %s", email, link)


@router.post("/auth/reset", response_model=schemas.Me)
def reset_password(payload: schemas.ResetPassword, response: Response, db: DBSession = Depends(get_db)):
    """A new password from a reset link: every browser is signed out, then this one signed in."""
    row = db.query(models.PasswordReset).filter(models.PasswordReset.token_hash == auth._hash_token(payload.token)).first()
    if not row or row.expires_at < utcnow():
        raise HTTPException(400, "This reset link has expired or was already used. Ask for a new one.")
    user = row.user
    if problem := auth.password_problem(payload.password, user.email):
        raise HTTPException(422, problem)
    user.password_hash = auth.hash_password(payload.password)
    db.query(models.PasswordReset).filter(models.PasswordReset.user_id == user.id).delete()  # this link and any others
    db.query(models.AuthToken).filter(models.AuthToken.user_id == user.id, models.AuthToken.kind == "session").delete()
    token = auth.new_token(db, user)
    db.commit()
    auth.set_cookie(response, token)
    return me(db, user)


@router.get("/mock-email")
def mock_outbox(user: models.User = Depends(auth.current_user)):
    """With MOCK_EMAIL=true only: the emails that would have been sent, newest last (for tests)."""
    if not settings.mock_email:
        raise HTTPException(404, "Only available with MOCK_EMAIL=true.")
    return mailer.SENT
