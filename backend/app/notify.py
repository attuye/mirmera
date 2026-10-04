"""Notifications to Telegram or Slack when something needs a tester's attention.

Each project picks its channels and events in Settings; the bot token and the
webhook stay in backend/.env. Every message is logged with whether it arrived.
Sending never breaks the work that triggered it: failures are logged, not raised.
"""
import asyncio
import logging

import httpx

from app import models
from app.keys import Keys, keys_for
from app.config import settings
from app.database import SessionLocal

logger = logging.getLogger(__name__)
EVENTS = {
    "fix_failed": "A fix didn't hold: the issue was closed, but the bug still reproduces",
    "fix_verified": "A fix was verified after its issue closed",
    "suite_problem": "A regression run found something: a test broke, failed, or a known bug now passes",
    "high_bug": "A bug was reviewed as high severity",
}
DEFAULT_EVENTS = ["fix_failed", "suite_problem", "high_bug"]
MOCK_SENT: list[dict] = []  # with MOCK_NOTIFICATIONS=true: {"channel", "to", "text"}
TIMEOUT = httpx.Timeout(10.0)


def prefs(project: models.Project) -> dict:
    p = project.notify or {}
    return {"telegram_chat_id": p.get("telegram_chat_id") or "", "slack": bool(p.get("slack")),
            "events": p.get("events", DEFAULT_EVENTS)}


def channel_status(k: Keys) -> dict:
    """Whether each channel can send at all (the workspace has its secret), and what to do if not."""
    mock = settings.mock_notifications
    return {
        "telegram": {"configured": mock or bool(k.telegram_bot_token),
                     "problem": None if mock or k.telegram_bot_token else "Add a Telegram bot token (from @BotFather) under Workspace keys."},
        "slack": {"configured": mock or bool(k.slack_webhook_url),
                  "problem": None if mock or k.slack_webhook_url else "Add a Slack incoming webhook under Workspace keys."},
        "mock": mock,
    }


def _clean(text: str, k: Keys) -> str:
    """Never let a secret into an error message or the log."""
    for secret in (k.telegram_bot_token, k.slack_webhook_url):
        if secret:
            text = text.replace(secret, "•••")
    return text


def _telegram(token: str, chat_id: str, text: str) -> None:
    r = httpx.post(f"{settings.telegram_api_url.rstrip('/')}/bot{token}/sendMessage",
                   json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=TIMEOUT)
    if r.status_code >= 400:
        try:
            why = r.json().get("description", "")
        except Exception:
            why = r.text[:200]
        hint = " (start a chat with the bot first, and check the chat id)" if r.status_code in (400, 403) else ""
        raise RuntimeError(f"Telegram answered {r.status_code}: {why}{hint}")


def _slack(webhook: str, text: str) -> None:
    r = httpx.post(webhook, json={"text": text}, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"Slack answered {r.status_code}: {r.text[:200]}")


def send(project_id: str, event: str, title: str, detail: str = "", to: str | None = None) -> list[dict]:
    """Send to every channel the project set up, if it wants this event ("test" always goes).
    to: a page of the project ("/bugs"), linked when PUBLIC_APP_URL is set. Returns the log rows."""
    with SessionLocal() as db:
        project = db.get(models.Project, project_id)
        if not project:
            return []
        k = keys_for(project)
        p, status = prefs(project), channel_status(k)
        if event != "test" and event not in p["events"]:
            return []
        link = f"{settings.public_app_url.rstrip('/')}/projects/{project_id}{to}" if to and settings.public_app_url else ""
        text = "\n".join(x for x in (f"Mirmera · {project.name}: {title}", detail, link) if x)
        targets = []
        if p["telegram_chat_id"] and status["telegram"]["configured"]:
            targets.append(("telegram", p["telegram_chat_id"]))
        if p["slack"] and status["slack"]["configured"]:
            targets.append(("slack", "webhook"))
        logs = []
        for channel, address in targets:
            row = models.NotificationLog(project_id=project_id, event=event, channel=channel, text=text, status="sent")
            try:
                if settings.mock_notifications:
                    MOCK_SENT.append({"channel": channel, "to": address, "text": text})
                    row.status = "mock"
                elif channel == "telegram":
                    _telegram(k.telegram_bot_token, address, text)
                else:
                    _slack(k.slack_webhook_url, text)
            except Exception as err:
                row.status, row.error = "failed", _clean(str(err), k)[:400]
                logger.warning("Notification to %s failed: %s", channel, row.error)
            db.add(row)
            logs.append(row)
        db.commit()
        return [{"channel": r.channel, "status": r.status, "error": r.error, "text": r.text} for r in logs]


async def send_async(project_id: str, event: str, title: str, detail: str = "", to: str | None = None) -> None:
    """From the runner's background work: send without blocking the loop, and never raise."""
    try:
        await asyncio.to_thread(send, project_id, event, title, detail, to)
    except Exception:
        logger.exception("Notification %s failed", event)
