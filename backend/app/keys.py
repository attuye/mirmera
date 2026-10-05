"""Per-workspace keys: GitHub, Jira, AI providers, Telegram and Slack.

Each workspace enters its own keys in the app. They are encrypted (Fernet: AES-128-CBC
with an HMAC) with the server's key before they reach the database, and the API never
sends a secret back: only whether it's set and its last four characters.

The server key comes from MIRMERA_SECRET_KEY, or else from a file created on first use
(secret.key next to the backend). Losing it means re-entering every workspace's keys,
so back it up with the database.

Until a workspace sets a key, the server's own value from backend/.env is used, unless
SERVER_KEYS_FOR_ALL=false (do that when other companies use your Mirmera: then each
workspace must bring its own keys).
"""
import base64
import hashlib
import logging
import os
from dataclasses import dataclass, fields
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session as DBSession, object_session

from app import models
from app.config import settings

log = logging.getLogger(__name__)

# name, label, group, secret (never shown back), env setting it falls back to
KEY_DEFS: list[dict] = [
    {"name": "github_token", "label": "GitHub token", "group": "GitHub", "secret": True,
     "hint": "Fine-grained token with Issues: read and write (and Pull requests: read for imports)"},
    {"name": "jira_base_url", "label": "Jira site", "group": "Jira", "secret": False, "hint": "e.g. https://acme.atlassian.net"},
    {"name": "jira_email", "label": "Jira account email", "group": "Jira", "secret": False, "hint": "The Atlassian account the token belongs to"},
    {"name": "jira_api_token", "label": "Jira API token", "group": "Jira", "secret": True, "hint": "id.atlassian.com → Security → API tokens"},
    {"name": "anthropic_api_key", "label": "Claude API key", "group": "AI", "secret": True, "hint": "console.anthropic.com → API keys"},
    {"name": "openai_api_key", "label": "OpenAI API key", "group": "AI", "secret": True, "hint": "Or any key your OpenAI-compatible server accepts"},
    {"name": "openai_base_url", "label": "OpenAI-compatible address", "group": "AI", "secret": False,
     "hint": "Leave empty for OpenAI itself; e.g. http://localhost:1234/v1 for LM Studio"},
    {"name": "telegram_bot_token", "label": "Telegram bot token", "group": "Notifications", "secret": True, "hint": "From @BotFather"},
    {"name": "slack_webhook_url", "label": "Slack webhook", "group": "Notifications", "secret": True, "hint": "A Slack incoming webhook for the channel"},
]
NAMES = tuple(k["name"] for k in KEY_DEFS)
SECRET = {k["name"] for k in KEY_DEFS if k["secret"]}


@dataclass
class Keys:
    """The keys a workspace's work uses: its own, else the server's (when shared)."""
    github_token: str | None = None
    jira_base_url: str | None = None
    jira_email: str | None = None
    jira_api_token: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    telegram_bot_token: str | None = None
    slack_webhook_url: str | None = None

    def secrets(self) -> list[str]:
        """Every secret value, to scrub from error messages."""
        return [v for f in fields(self) if f.name in SECRET and (v := getattr(self, f.name))]


def server_keys() -> Keys:
    """The keys in backend/.env."""
    return Keys(**{n: getattr(settings, n) or None for n in NAMES})


# ---------- encryption ----------

_fernet: Fernet | None = None


BACKEND_DIR = Path(__file__).resolve().parent.parent
startup_log = logging.getLogger("uvicorn.error")  # shown in the terminal running the backend


def key_file() -> Path:
    """SECRET_KEY_FILE, with a relative path taken inside the backend folder (not the folder the
    server was started from, which would make a second key and leave saved keys unreadable)."""
    path = Path(settings.secret_key_file).expanduser()
    return path if path.is_absolute() else BACKEND_DIR / path


def ensure_server_key() -> str:
    """Called when the backend starts: makes the key file if needed, and says where the key is,
    so it can be backed up before any workspace saves a key."""
    if settings.mirmera_secret_key:
        startup_log.info("Workspace keys are encrypted with MIRMERA_SECRET_KEY from backend/.env.")
        return "MIRMERA_SECRET_KEY"
    path = key_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(Fernet.generate_key().decode())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows: no POSIX modes
        startup_log.warning("Created %s to encrypt workspace keys. Back it up with the database; "
                            "without it, saved keys can't be read.", path)
    else:
        startup_log.info("Workspace keys are encrypted with %s (back it up with the database).", path)
    return str(path)


def _server_key() -> bytes:
    raw = settings.mirmera_secret_key
    if not raw:
        ensure_server_key()  # normally done at startup already
        raw = key_file().read_text().strip()
    # Any passphrase works: it is stretched into a Fernet key.
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())


def _box() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_server_key())
    return _fernet


def reset_box() -> None:  # tests change the key
    global _fernet
    _fernet = None


def encrypt(value: str) -> str:
    return _box().encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    try:
        return _box().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("A saved workspace key can't be decrypted: MIRMERA_SECRET_KEY (or secret.key) changed.")
        return None


# ---------- reading and writing ----------

def stored(db: DBSession, workspace_id: str) -> dict[str, models.WorkspaceKey]:
    return {r.name: r for r in db.query(models.WorkspaceKey).filter(models.WorkspaceKey.workspace_id == workspace_id)}


def keys_for_workspace(db: DBSession, workspace_id: str | None) -> Keys:
    own = {n: decrypt(r.value) for n, r in stored(db, workspace_id).items()} if workspace_id else {}
    base = server_keys() if settings.server_keys_for_all else Keys()
    return Keys(**{n: own.get(n) or getattr(base, n) for n in NAMES})


def keys_for(project: models.Project) -> Keys:
    """The keys for work in this project."""
    db = object_session(project)
    if db is None:
        from app.database import SessionLocal
        with SessionLocal() as s:
            return keys_for_workspace(s, project.org_id)
    return keys_for_workspace(db, project.org_id)


def overview(db: DBSession, workspace_id: str) -> list[dict]:
    """What the Keys page shows: never a secret, only whether it's set and how it ends."""
    rows = stored(db, workspace_id)
    server = server_keys()
    out = []
    for d in KEY_DEFS:
        row = rows.get(d["name"])
        value = decrypt(row.value) if row else None
        shown = None
        if value:
            shown = f"…{value[-4:]}" if d["secret"] and len(value) > 8 else ("set" if d["secret"] else value)
        out.append({**d, "set": bool(value), "shown": shown, "unreadable": bool(row) and value is None,
                    "updated_at": row.updated_at if row else None,
                    "updated_by": row.updater.name if row and row.updater else None,
                    "server_fallback": settings.server_keys_for_all and bool(getattr(server, d["name"]))})
    return out


def save(db: DBSession, workspace_id: str, values: dict[str, str | None], user_id: str) -> None:
    """Set or clear keys; an empty value clears. Unknown names are refused by the caller."""
    rows = stored(db, workspace_id)
    for name, value in values.items():
        value = (value or "").strip()
        row = rows.get(name)
        if not value:
            if row:
                db.delete(row)
            continue
        if row is None:
            row = models.WorkspaceKey(workspace_id=workspace_id, name=name)
            db.add(row)
        row.value, row.updated_by = encrypt(value), user_id
