"""The project's test data: values the team keeps for the app under test (a valid national ID, a test
phone number, a known-expired document), and which of the app's fields each one fills.

Exploration runs fill a field from here before making a value up, so the app gets data it really
accepts; invalid and edge rows become test ideas for their fields. A secret row is encrypted at rest
and travels as a "{data:Name}" token: it is typed into the browser only, and the AI, steps, notes and
drafts see "•••". A "once" row (a voucher, a phone number for one sign-up) is marked used when the
app accepts it, and the next run takes the next one; when none are left Mirmera makes a value up and
says so. After each run Mirmera notes which rows met their field, so a renamed field shows in Settings.
"""
import re
from contextvars import ContextVar

from sqlalchemy.orm import Session as DBSession

from app import models
from app.keys import decrypt, encrypt
from app.timeutil import utcnow

KINDS = ("valid", "invalid", "edge")
TOKEN = re.compile(r"\{data:([^{}]{1,80})\}")
NOISE = {"the", "your", "enter", "please", "number", "no", "of", "a", "an", "and", "or", "for", "to", "in", "on", "e", "g"}

# The run's rows (secret values decrypted, in memory only), its current page, and what was picked.
ROWS: ContextVar[list[dict] | None] = ContextVar("test_data_rows", default=None)
PAGE: ContextVar[str] = ContextVar("test_data_page", default="")
PICKED: ContextVar[dict | None] = ContextVar("test_data_picked", default=None)  # field label -> row id (this form)
MADE_UP: ContextVar[set | None] = ContextVar("test_data_made_up", default=None)  # labels Mirmera made a value up for
SEEN: ContextVar[set | None] = ContextVar("test_data_seen", default=None)  # row ids that met a field this run


# ---------- stored rows ----------

def to_row(t: models.TestData) -> dict:
    value = (decrypt(t.value) or "") if t.secret else t.value
    return {"id": t.id, "name": t.name, "value": value, "labels": list(t.labels or []), "page": t.page or "",
            "kind": t.kind, "once": t.once, "secret": t.secret, "used": bool(t.used_at)}


def save_value(t: models.TestData, value: str, secret: bool) -> None:
    t.value = encrypt(value) if secret else value
    t.secret = secret


def for_run(db: DBSession, project_id: str) -> list[dict]:
    """What a run may use: everything but "once" values already used."""
    rows = db.query(models.TestData).filter(models.TestData.project_id == project_id).order_by(models.TestData.created_at).all()
    return [to_row(t) for t in rows if not (t.once and t.used_at)]


def start(rows: list[dict]) -> None:
    """At the start of a run (or a replay): its rows, and nothing picked or seen yet."""
    ROWS.set(rows)
    PICKED.set({})
    MADE_UP.set(set())
    SEEN.set(set())


def secrets() -> list[str]:
    return [r["value"] for r in ROWS.get() or [] if r["secret"] and r["value"]]


# ---------- matching rows to fields ----------

def _words(text: str) -> set[str]:
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"\w+", (text or "").lower())} - NOISE


def matches(f: dict, row: dict, page: str | None = None) -> bool:
    """Does this row fill this field? By its label, placeholder, aria-label or name, loosely: case,
    plurals and words like "your" or "number" don't count. A row for one page only matches there."""
    where = page if page is not None else PAGE.get()
    if row["page"] and row["page"] not in (where or ""):
        return False
    names = [f.get("label", ""), *f.get("keys", [])]
    for label in row["labels"]:
        want = _words(label)
        if not want:
            continue
        for n in names:
            have = _words(n)
            if have and (want == have or want <= have):
                return True
    return False


def token(row: dict) -> str:
    return "{data:" + row["name"] + "}"


def valid_for(f: dict) -> str | None:
    """A valid value of the team's for this field (a token for a secret one), or None: Mirmera makes one up."""
    rows = [r for r in ROWS.get() or [] if r["kind"] == "valid" and not r["used"] and matches(f, r)]
    if rows:
        r = rows[0]
        (SEEN.get() if SEEN.get() is not None else set()).add(r["id"])
        if PICKED.get() is not None:
            PICKED.get()[f["label"]] = r["id"]
        return token(r) if r["secret"] else r["value"]
    if any(matches(f, r) and r["kind"] == "valid" and r["once"] for r in ROWS.get() or []):
        return None  # every "once" value is used: made up, and said so (see after_submit)
    return None


def ideas(f: dict) -> list[tuple[str, str]]:
    """The team's invalid and edge values for this field, as test ideas."""
    out = []
    for r in ROWS.get() or []:
        if r["kind"] in ("invalid", "edge") and matches(f, r):
            (SEEN.get() if SEEN.get() is not None else set()).add(r["id"])
            out.append((f"{r['name']} (your test data)", token(r) if r["secret"] else r["value"]))
    return out


def note_made_up(f: dict) -> None:
    if MADE_UP.get() is not None and f.get("type") not in ("checkbox", "radio", "file", "password") \
            and not f.get("type", "").startswith("select"):
        MADE_UP.get().add(f["label"])


def resolve(text: str | None, rows: list[dict] | None = None) -> str:
    """'{data:National ID}' becomes the value (typed into the browser only)."""
    if not text or "{data:" not in text:
        return text or ""
    by_name = {r["name"].lower(): r["value"] for r in (rows if rows is not None else ROWS.get() or [])}
    return TOKEN.sub(lambda m: by_name.get(m.group(1).strip().lower(), m.group(0)), text)


def mask(text: str) -> str:
    """Secret values never leave the browser: '•••' wherever one would show."""
    for s in secrets():
        if len(s) >= 3:
            text = text.replace(s, "•••")
    return text


# ---------- after a submit, after a run ----------

def after_submit(outcome: str, said: list[str], session_id: str, run_id: str, noted: set) -> list[str]:
    """Marks the "once" values the app accepted as used; says when the app refused a value Mirmera made
    up for a field the team has no data for. Returns notes to keep. Clears what this form picked."""
    picked, made_up = dict(PICKED.get() or {}), set(MADE_UP.get() or set())
    PICKED.set({})
    MADE_UP.set(set())
    notes = []
    rows = ROWS.get() or []
    if outcome == "accepted":
        once = [r for r in rows if r["id"] in picked.values() and r["once"] and not r["used"]]
        if once:
            from app.database import SessionLocal
            with SessionLocal() as db:
                for r in once:
                    t = db.get(models.TestData, r["id"])
                    if t:
                        t.used_at, t.used_by_run = utcnow(), run_id
                    r["used"] = True
                db.commit()
            for r in once:
                left = [x for x in rows if x["kind"] == "valid" and x["once"] and not x["used"] and set(x["labels"]) & set(r["labels"])]
                if not left and ("used-up", r["name"]) not in noted:
                    noted.add(("used-up", r["name"]))
                    notes.append(f'Test data: every "once" value for {", ".join(r["labels"][:2])} is used now '
                                 f'(the last was {r["name"]}). Add more in Settings → Test data; until then Mirmera makes values up.')
    elif outcome == "refused":
        text = " ".join(said).lower()
        for label in sorted(made_up):
            words = {w for w in _words(label) if len(w) >= 4}  # "voucher" in "This voucher is not valid"
            if words and words & _words(text) and ("refused", label) not in noted:
                noted.add(("refused", label))
                notes.append(f'The app refused the {label} Mirmera made up ("{said[0][:120]}"). Add a valid {label} '
                             "in Settings → Test data, and runs will use it.")
    return notes


def after_run(project_id: str, visited_forms: bool) -> None:
    """Which rows met their field in this run: a row that didn't, in a run that saw forms, may have lost
    its field (a renamed label), and Settings says so."""
    if not visited_forms or ROWS.get() is None:
        return
    seen = SEEN.get() or set()
    from app.database import SessionLocal
    now = utcnow()
    with SessionLocal() as db:
        for t in db.query(models.TestData).filter(models.TestData.project_id == project_id).all():
            if t.id in seen:
                t.last_seen_at = now
            elif not (t.once and t.used_at):
                t.missed_at = now
        db.commit()


def known_fields(db: DBSession, project_id: str) -> list[dict]:
    """The app's fields as Mirmera has met them: from the latest survey, and what runs filled.
    [{"label", "type", "pages": [...]}], for picking which fields a row fills."""
    from app import ai_jobs
    out: dict[str, dict] = {}

    def add(label: str, kind: str, page: str) -> None:
        if not label:
            return
        e = out.setdefault(label.lower(), {"label": label, "type": kind, "pages": []})
        if page and page not in e["pages"]:
            e["pages"].append(page)
    job = ai_jobs.latest(db, project_id, "app_map")
    if job and job.status == "done" and job.result:
        for p in job.result.get("pages", []):
            for f in p.get("fields") or []:
                if f.get("type") not in ("hidden", "submit", "button"):
                    add(f.get("label", ""), f.get("type", "text"), p.get("path", ""))
    runs = (db.query(models.ExplorationRun).filter(models.ExplorationRun.project_id == project_id)
            .order_by(models.ExplorationRun.created_at.desc()).limit(10).all())
    from urllib.parse import urlsplit
    for r in runs:
        for s in r.journey or []:
            a = s.get("action") or {}
            loc = a.get("locator") or {}
            if s.get("type") == "action" and a.get("action") in ("fill", "select", "upload") and loc.get("method") == "label":
                add(loc.get("value", ""), "select" if a.get("action") == "select" else "text", urlsplit(s.get("url") or "").path)
    return sorted(out.values(), key=lambda e: (e["pages"][:1], e["label"].lower()))
