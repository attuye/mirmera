"""Mirmera as a Rovo agent in Jira: assign an issue to Mirmera, or mention it in a comment.

Jira calls the app's agent endpoint with Agent2Agent (A2A 1.0) JSON-RPC, signed with a
Forge invocation token like every other call from the app (app/jira_app.py):

- SendMessage: new work. An assignment makes Mirmera plan testing for the issue: the
  issue becomes a story in the project with that Jira key, and the AI proposes risks
  for it and charters for those risks. That runs in the background; the reply is a task
  in TASK_STATE_WORKING. A mention that asks for the status gets what's left at once.
- GetTask: Jira polls the task until it is completed, failed or rejected. The final
  message is shown in Jira, where the user can draft a comment from it.
- CancelTask: stops caring about a task. Proposals already saved stay, for the tester.

Everything the agent makes is a proposal: risks stay "proposed" and charters "proposed"
until a tester accepts them in Mirmera. The agent never decides what's tested.
"""
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session as DBSession

from app import jira_app, models, stories
from app.ai import tasks as ai_tasks
from app.ai.base import ProviderError
from app.ai.routing import provider_for
from app.database import SessionLocal
from app.timeutil import utcnow

log = logging.getLogger(__name__)

ACTIVE = ("TASK_STATE_SUBMITTED", "TASK_STATE_WORKING")
TERMINAL = ("TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED")
PLAN_INVOCATIONS = ("ISSUE_ASSIGNMENT", "ISSUE_WORKFLOW_ASSIGNMENT", "ISSUE_MANUAL_TRIGGER", "AUTOMATION")
PLAN_WORDS = re.compile(r"\b(plan|risks?|charters?|start|analy[sz]e)\b", re.I)
MAX_RISKS = 5
MAX_CHARTERS = 3
MAX_TEXT = 12_000


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------- reading what Jira sent ----------

def _adf_text(node) -> str:
    """Jira descriptions may come as Atlassian Document Format; keep the words."""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_text(n) for n in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    inner = _adf_text(node.get("content") or [])
    return inner + ("\n" if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock") else "")


def parse_message(params: dict) -> dict:
    message = (params or {}).get("message") or {}
    parts = message.get("parts") or []
    if not isinstance(parts, list):
        raise RpcError(-32602, "message.parts must be a list")
    data = next((p["data"] for p in parts if isinstance(p, dict) and isinstance(p.get("data"), dict)), {})
    text = "\n".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str))
    issue = data.get("issue") or {}
    fields = issue.get("fields") or {}
    comment = data.get("comment") or {}
    return {
        "task_id": message.get("taskId"), "context_id": message.get("contextId"),
        "invocation": data.get("invocationType"),
        "issue_key": (fields.get("key") or issue.get("key") or "").strip().upper() or None,
        "issue_title": (fields.get("summary") or "").strip()[:300] or None,
        "issue_text": _adf_text(fields.get("description") or "").strip()[:MAX_TEXT] or None,
        "request": (_adf_text(comment.get("body") or "").strip() or None) if comment else None,
        "text": text.strip(),
        "jira_account": data.get("userAccountId"),
    }


# ---------- task bookkeeping ----------

def _ts(when: datetime | None) -> str:
    return (when or utcnow()).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_json(t: models.JiraAgentTask) -> dict:
    return {
        "id": t.id, "contextId": t.context_id,
        "status": {
            "state": t.state,
            "message": {"role": "ROLE_AGENT", "parts": [{"text": t.message or ""}], "messageId": f"{t.id}-{t.state.lower()}",
                        "taskId": t.id, "contextId": t.context_id},
            "timestamp": _ts(t.updated_at),
        },
    }


def _set(t: models.JiraAgentTask, state: str, message: str) -> models.JiraAgentTask:
    t.state, t.message, t.updated_at = state, message, utcnow()
    return t


def get_task(db: DBSession, inv: jira_app.Invocation, params: dict) -> models.JiraAgentTask:
    task_id = (params or {}).get("taskId") or (params or {}).get("id")
    t = db.get(models.JiraAgentTask, task_id) if task_id else None
    if not t or t.installation_id != inv.installation_id:  # another site's task doesn't exist for this one
        raise RpcError(-32001, "Task not found")
    return t


# ---------- new work ----------

def _project_for(db: DBSession, workspace_id: str, issue_key: str) -> models.Project | None:
    key = issue_key.rsplit("-", 1)[0]
    return (db.query(models.Project)
            .filter(models.Project.org_id == workspace_id, models.Project.jira_project_key == key,
                    models.Project.archived_at.is_(None))
            .order_by(models.Project.created_at).first())


def send_message(db: DBSession, inv: jira_app.Invocation, params: dict) -> tuple[models.JiraAgentTask, bool]:
    """Records the work and answers what can be answered now. Returns the task and whether
    planning should run in the background."""
    m = parse_message(params)
    previous = None
    if m["context_id"]:  # a follow-up in the Rovo chat: same conversation, new task
        previous = (db.query(models.JiraAgentTask)
                    .filter(models.JiraAgentTask.context_id == m["context_id"],
                            models.JiraAgentTask.installation_id == inv.installation_id)
                    .order_by(models.JiraAgentTask.created_at.desc()).first())
    t = models.JiraAgentTask(
        context_id=m["context_id"] or models.gen_id(), installation_id=inv.installation_id,
        invocation=m["invocation"] or ("CHAT" if previous else None),
        issue_key=m["issue_key"] or (previous.issue_key if previous else None),
        issue_title=m["issue_title"] or (previous.issue_title if previous else None),
        issue_text=m["issue_text"] or (previous.issue_text if previous else None),
        request=m["request"] or (m["text"] if previous else None),
        jira_account=m["jira_account"] or inv.account_id,
    )
    db.add(t)
    inst = jira_app.installation(db, inv)
    if not inst:
        return _set(t, "TASK_STATE_REJECTED", jira_app_not_linked()), False
    t.workspace_id = inst.workspace_id
    if not t.issue_key or not re.fullmatch(r"[A-Z][A-Z0-9_]+-\d+", t.issue_key):
        return _set(t, "TASK_STATE_REJECTED", "Assign me a Jira work item, or mention me in one of its comments."), False

    project = _project_for(db, inst.workspace_id, t.issue_key)
    if not project:
        key = t.issue_key.rsplit("-", 1)[0]
        return _set(t, "TASK_STATE_FAILED",
                    f"No Mirmera project in {inst.workspace.name} uses the Jira project key {key}. "
                    f"Set it in the project's Settings in Mirmera, then assign {t.issue_key} to me again."), False
    t.project_id = project.id

    wants_plan = t.invocation in PLAN_INVOCATIONS or bool(t.request and PLAN_WORDS.search(t.request))
    t.action = "plan" if wants_plan else "status"
    if not wants_plan:
        return _set(t, "TASK_STATE_COMPLETED", status_text(db, inst, t.issue_key)), False
    _set(t, "TASK_STATE_WORKING", f"Reading {t.issue_key} and proposing risks and charters for it in Mirmera ({project.name}).")
    return t, True


def jira_app_not_linked() -> str:
    from app.routers.jira_app import NOT_LINKED
    return NOT_LINKED


# ---------- what's left ----------

def status_text(db: DBSession, inst: models.JiraInstallation, issue_key: str) -> str:
    panel = jira_app.issue_panel(db, inst, issue_key, None)
    lines: list[str] = []
    for s in panel["stories"]:
        lines.append(f"{issue_key} in Mirmera ({s['project']}): {s['risks']} risks, {s['charters']} charters, "
                     f"{s['sessions']} sessions ({s['minutes']} min), {s['bugs']} bugs.")
        if s["left"]:
            lines.append("Not looked at yet:")
            lines += [f"- {line}" for line in s["left"][:8]]
            if len(s["left"]) > 8:
                lines.append(f"- and {len(s['left']) - 8} more")
        else:
            lines.append("Nothing left that Mirmera knows of. Whether that's enough testing is the team's call.")
        if s["url"]:
            lines.append(f"Open in Mirmera: {s['url']}")
    for b in panel["bugs"]:
        retest = f", latest retest: {b['retest'].replace('_', ' ')}" if b["retest"] else ""
        lines.append(f"Bug from Mirmera: {b['title']} ({b['severity']}{retest}), found in {b['project']}.")
    if not lines:
        lines.append(f"Mirmera has no testing on {issue_key} yet. Assign it to me and I'll propose risks and charters.")
    return "\n".join(lines)


# ---------- planning (in the background) ----------

def _story(db: DBSession, project: models.Project, t: models.JiraAgentTask, site_url: str | None) -> models.Story:
    story = (db.query(models.Story)
             .filter(models.Story.project_id == project.id, models.Story.key == t.issue_key).first())
    if not story:
        story = models.Story(project_id=project.id, key=t.issue_key, tracker="jira",
                             title=t.issue_title or t.issue_key,
                             url=f"{site_url}/browse/{t.issue_key}" if site_url else None)
        db.add(story)
    if t.issue_title:
        story.title = t.issue_title
    if t.issue_text and not story.description:
        story.description = t.issue_text
    db.flush()
    return story


async def plan(db: DBSession, t: models.JiraAgentTask) -> str:
    """Story → (story-only product model, not saved) → proposed risks → proposed charters."""
    from app.routers.risks import _next_ref_number
    project = db.get(models.Project, t.project_id)
    inst = (db.query(models.JiraInstallation)
            .filter(models.JiraInstallation.installation_id == t.installation_id).first())
    story = _story(db, project, t, inst.site_url if inst else None)
    t.story_id = story.id
    link = jira_app.app_link(project.id, f"/stories/{story.id}")
    if any(r.status != "retired" for r in story.risks) or story.charters:
        return (f"{t.issue_key} is already planned in Mirmera: {len(story.risks)} risks and {len(story.charters)} charters. "
                f"I left them as they are.\n\n" + (status_text(db, inst, t.issue_key) if inst else ""))

    # A product model of the story alone. Not saved: the project's own model stays as it is.
    model, _ = await ai_tasks.build_product_model(
        provider_for(project, "product_model"), project_name=project.name, testing_mission=project.testing_mission,
        artifacts=[("story", f"{story.key}: {story.title}", stories.artifact_text(story))])
    existing = db.query(models.Risk).filter(models.Risk.project_id == project.id, models.Risk.status != "retired").all()
    risk_provider = provider_for(project, "risk_analysis")
    proposed = await ai_tasks.analyze_risks(
        risk_provider, project_name=project.name, testing_mission=project.testing_mission,
        product_model={"summary": model.summary, "elements": [e.model_dump() for e in model.elements], "unknowns": model.unknowns},
        max_risks=MAX_RISKS, existing_titles=[r.title for r in existing])
    proposed = sorted(proposed, key=lambda r: r.impact * r.likelihood, reverse=True)
    number = _next_ref_number(db, project.id)
    risks = []
    for i, r in enumerate(proposed):
        risk = models.Risk(project_id=project.id, ref=f"R{number + i}", **r.model_dump(), status="proposed", source="ai",
                           ai_provider=risk_provider.name, ai_model=risk_provider.model,
                           prompt_version=ai_tasks.PROMPT_VERSIONS["risk_analysis"],
                           tester_notes=f"Proposed by the Mirmera agent when {t.issue_key} was assigned to it in Jira.")
        db.add(risk)
        story.risks.append(risk)
        risks.append(risk)
    db.flush()

    charters = []
    if risks:
        charter_provider = provider_for(project, "risk_charters")
        drafts = await ai_tasks.charters_from_risks(
            charter_provider, project_name=project.name, testing_mission=project.testing_mission,
            product_summary=model.summary,
            risks=[{"ref": r.ref, "title": r.title, "failure_mode": r.failure_mode, "quality_criterion": r.quality_criterion,
                    "score": r.score, "evidence": r.evidence, "tester_notes": None} for r in risks],
            max_charters=MAX_CHARTERS)
        by_ref = {r.ref: r for r in risks}
        for d in drafts:
            covered = [by_ref[ref] for ref in d.risk_refs if ref in by_ref]
            if not covered:
                continue  # the model may only use the risks it was given
            charter = models.Charter(
                project_id=project.id, mission_statement=f"Explore {d.target} with {d.resources} to discover {d.information}",
                rationale=d.rationale, priority=d.priority, source="risk-based", status="proposed",
                risk_areas=[r.title for r in covered], test_ideas=d.test_ideas, oracles=list(d.oracles))
            charter.risks = covered
            db.add(charter)
            story.charters.append(charter)
            charters.append(charter)

    lines = [f"I planned testing for {t.issue_key} in Mirmera ({project.name}). Everything below is a proposal: "
             f"a tester accepts, changes or drops it in Mirmera."]
    if risks:
        lines.append(f"\nRisks ({len(risks)}):")
        lines += [f"- {r.ref} {r.title} (impact {r.impact} × likelihood {r.likelihood})" for r in risks]
    else:
        lines.append("\nI found no risks in the description alone. Add acceptance criteria or detail, then assign it again.")
    if charters:
        lines.append(f"\nCharters ({len(charters)}):")
        lines += [f"- {c.mission_statement}" for c in charters]
    if link:
        lines.append(f"\nReview them: {link}")
    return "\n".join(lines)


async def run_plan(task_id: str) -> None:
    """Background job: its own database session, and every outcome ends in a terminal state."""
    with SessionLocal() as db:
        t = db.get(models.JiraAgentTask, task_id)
        if not t or t.state not in ACTIVE:
            return
        try:
            message = await plan(db, t)
            db.refresh(t)
            if t.state == "TASK_STATE_CANCELED":  # canceled while it ran: keep the proposals, say nothing more
                db.commit()
                return
            _set(t, "TASK_STATE_COMPLETED", message)
            db.commit()
        except ProviderError as err:
            db.rollback()
            _fail(db, task_id, f"The AI couldn't plan {t.issue_key}: {err}. Check the project's AI settings in Mirmera.")
        except Exception:
            db.rollback()
            log.exception("Jira agent task %s failed", task_id)
            _fail(db, task_id, f"Mirmera couldn't plan this work item because of an error on the server. "
                               f"The server log has the details.")


def _fail(db: DBSession, task_id: str, message: str) -> None:
    t = db.get(models.JiraAgentTask, task_id)
    if t and t.state in ACTIVE:
        _set(t, "TASK_STATE_FAILED", message)
        db.commit()
