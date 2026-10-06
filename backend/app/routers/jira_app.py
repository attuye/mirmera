"""The Mirmera app for Jira.

Two kinds of caller:
- Mirmera users (signed in, workspace admins): see which Jira sites are linked, make a
  one-time link code, unlink a site.
- The Jira app itself (requests from Atlassian's servers carrying a Forge invocation
  token, see app/jira_app.py): link with a code, ask for its status, unlink, and fetch the
  panel for an issue, and talk to the Mirmera agent (an issue assigned to it, a mention).
"""
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session as DBSession

from app import auth, jira_agent, jira_app, models, schemas
from app.config import settings
from app.database import get_db
from app.routers.workspace_keys import _admin

router = APIRouter(tags=["jira app"])

NOT_LINKED = ("This Jira site isn't linked to a Mirmera workspace yet. A Jira admin can link it in "
              "Apps → Manage apps → Mirmera, with a code from Mirmera (Account → your workspace → Jira).")


# ---------- for Mirmera users ----------

def _installation_out(row: models.JiraInstallation) -> dict:
    return {"id": row.id, "site_url": row.site_url, "cloud_id": row.cloud_id, "environment": row.environment,
            "linked_by": row.linker.name if row.linker else None, "linked_at": row.linked_at, "last_seen_at": row.last_seen_at}


@router.get("/workspaces/{workspace_id}/jira-app", response_model=schemas.JiraAppOverview)
def overview(workspace_id: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    _admin(db, user, workspace_id)
    rows = (db.query(models.JiraInstallation).filter(models.JiraInstallation.workspace_id == workspace_id)
            .order_by(models.JiraInstallation.linked_at).all())
    agent_tasks = (db.query(models.JiraAgentTask).filter(models.JiraAgentTask.workspace_id == workspace_id)
                   .order_by(models.JiraAgentTask.created_at.desc()).limit(20).all())
    return {"configured": bool(settings.jira_app_id), "installations": [_installation_out(r) for r in rows],
            "agent_tasks": agent_tasks}


@router.post("/workspaces/{workspace_id}/jira-app/codes", response_model=schemas.JiraLinkCodeOut)
def make_code(workspace_id: str, user: models.User = Depends(auth.current_user), db: DBSession = Depends(get_db)):
    """A code to paste into the Mirmera app in Jira. Works once, for 30 minutes; only its hash is kept."""
    _admin(db, user, workspace_id)
    code, row = jira_app.new_code(db, workspace_id, user)
    db.commit()
    return {"code": code, "expires_at": row.expires_at}


@router.delete("/workspaces/{workspace_id}/jira-app/installations/{installation_id}", status_code=204)
def unlink_site(workspace_id: str, installation_id: str, user: models.User = Depends(auth.current_user),
                db: DBSession = Depends(get_db)):
    _admin(db, user, workspace_id)
    row = db.get(models.JiraInstallation, installation_id)
    if not row or row.workspace_id != workspace_id:
        raise HTTPException(404, "Jira site not found")
    db.delete(row)
    db.commit()


# ---------- for the Jira app (Forge invocation token) ----------

def invocation(authorization: str | None = Header(default=None)) -> jira_app.Invocation:
    try:
        return jira_app.verify(authorization)
    except jira_app.FitProblem as err:
        raise HTTPException(401, str(err))


def _status(inst: models.JiraInstallation | None) -> dict:
    if not inst:
        return {"linked": False}
    return {"linked": True, "workspace": inst.workspace.name, "site_url": inst.site_url, "linked_at": inst.linked_at,
            "app_url": settings.public_app_url.rstrip("/") if settings.public_app_url else None}


@router.get("/jira-app/status", response_model=schemas.JiraAppStatus)
def status(inv: jira_app.Invocation = Depends(invocation), db: DBSession = Depends(get_db)):
    inst = jira_app.installation(db, inv)
    db.commit()
    return _status(inst)


@router.post("/jira-app/link", response_model=schemas.JiraAppStatus)
def link(payload: schemas.JiraLinkIn, inv: jira_app.Invocation = Depends(invocation), db: DBSession = Depends(get_db)):
    """Only the app's admin page sends this, and Jira shows that page to Jira admins only."""
    try:
        inst = jira_app.link(db, inv, payload.code, payload.site_url)
    except ValueError as err:
        raise HTTPException(400, str(err))
    db.commit()
    db.refresh(inst)
    return _status(inst)


@router.delete("/jira-app/link", response_model=schemas.JiraAppStatus)
def unlink(inv: jira_app.Invocation = Depends(invocation), db: DBSession = Depends(get_db)):
    inst = jira_app.installation(db, inv)
    if inst:
        db.delete(inst)
        db.commit()
    return _status(None)


@router.post("/jira-app/issue-panel", response_model=schemas.JiraIssuePanel)
def issue_panel(payload: schemas.JiraIssuePanelIn, inv: jira_app.Invocation = Depends(invocation),
                db: DBSession = Depends(get_db)):
    inst = jira_app.installation(db, inv)
    if not inst:
        raise HTTPException(409, NOT_LINKED)
    out = jira_app.issue_panel(db, inst, payload.issue_key, payload.project_key)
    db.commit()
    return out


# ---------- the Mirmera agent (Rovo, A2A JSON-RPC) ----------

def _rpc_error(rpc_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


@router.post("/jira-app/a2a")
async def agent(request: Request, background: BackgroundTasks, inv: jira_app.Invocation = Depends(invocation),
                db: DBSession = Depends(get_db)):
    """Jira's calls to the agent: SendMessage (assigned or mentioned), GetTask (polling), CancelTask.
    Errors are JSON-RPC errors in a 200 response, as JSON-RPC expects."""
    try:
        body = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "Parse error")
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or not isinstance(body.get("method"), str):
        return _rpc_error(body.get("id") if isinstance(body, dict) else None, -32600, "Invalid request")
    rpc_id, method, params = body.get("id"), body["method"], body.get("params") or {}
    try:
        if method == "SendMessage":
            task, run = jira_agent.send_message(db, inv, params)
            db.commit()
            if run:
                background.add_task(jira_agent.run_plan, task.id)
        elif method == "GetTask":
            task = jira_agent.get_task(db, inv, params)
        elif method == "CancelTask":
            task = jira_agent.get_task(db, inv, params)
            if task.state in jira_agent.ACTIVE:
                jira_agent._set(task, "TASK_STATE_CANCELED", "Canceled. Anything already proposed stays in Mirmera for a tester.")
                db.commit()
        else:
            return _rpc_error(rpc_id, -32601, f"Method not found: {method}")
    except jira_agent.RpcError as err:
        db.rollback()
        return _rpc_error(rpc_id, err.code, err.message)
    return {"jsonrpc": "2.0", "id": rpc_id, "result": {"task": jira_agent.task_json(task)}}


# ---------- "Plan testing with Mirmera" on the panel (no Rovo needed) ----------

@router.post("/jira-app/plan", response_model=schemas.JiraPlanOut)
def plan_from_panel(payload: schemas.JiraPlanIn, background: BackgroundTasks,
                    inv: jira_app.Invocation = Depends(invocation), db: DBSession = Depends(get_db)):
    """The panel's button: the same planning as assigning the work item to the Mirmera agent.
    The app reads the summary and description from Jira and sends them along."""
    if not jira_app.installation(db, inv):
        raise HTTPException(409, NOT_LINKED)
    params = {"message": {"role": "ROLE_USER", "messageId": "panel", "parts": [{"data": {
        "invocationType": "ISSUE_MANUAL_TRIGGER", "userAccountId": inv.account_id,
        "issue": {"fields": {"key": payload.issue_key, "summary": payload.summary, "description": payload.description}},
    }}]}}
    task, run = jira_agent.send_message(db, inv, params)
    db.commit()
    if run:
        background.add_task(jira_agent.run_plan, task.id)
    return _plan_out(task)


@router.get("/jira-app/tasks/{task_id}", response_model=schemas.JiraPlanOut)
def plan_status(task_id: str, inv: jira_app.Invocation = Depends(invocation), db: DBSession = Depends(get_db)):
    try:
        task = jira_agent.get_task(db, inv, {"taskId": task_id})
    except jira_agent.RpcError:
        raise HTTPException(404, "Task not found")
    return _plan_out(task)


def _plan_out(t: models.JiraAgentTask) -> dict:
    done = t.state not in jira_agent.ACTIVE
    return {"id": t.id, "state": t.state, "done": done, "ok": t.state == "TASK_STATE_COMPLETED", "message": t.message,
            "story_url": jira_app.app_link(t.project_id, f"/stories/{t.story_id}") if t.project_id and t.story_id else None}
