"""Test-idea nudges: small, specific suggestions while a session runs.

They come from what is happening in this session, not from an AI, so they are
instant, work offline, and each one says why it appeared:

- something went wrong (a failed request, an error) and no bug or note covers it
- the charter's risks and test ideas that no note mentions yet
- fields the tester filled with only ordinary values: boundary and odd inputs,
  chosen by what the field looks like (a date, a number, an email, text)
- a choice made only one way, a file uploaded, a form submitted: the usual
  ways those break (other options, odd files, double submits, Back, refresh)
- pages other sessions opened but never tried
- half the session gone with no bug or question: switch heuristics

A nudge has a stable id, so "Not now" hides it for the rest of the session and
"Tried it" logs a test note and hides it.
"""
import hashlib
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from sqlalchemy.orm import Session as DBSession

from app import coverage, models
from app.runner import describe_locator
from app.timeutil import utcnow
from app.trackers.base import keywords

MAX_NUDGES = 8
SUBMIT_WORDS = re.compile(r"\b(submit|save|verify|pay|send|continue|confirm|upload|create|delete|register|sign ?up|log ?in|next|apply|place order|checkout)\b", re.I)


@dataclass
class Nudge:
    id: str
    kind: str  # problem | risk | idea | field | choice | upload | submit | page | time
    title: str
    why: str
    heuristic: str  # the testing heuristic behind it, named
    suggestions: list[str] = field(default_factory=list)  # concrete things to try
    priority: int = 5  # lower first
    heuristic_key: str | None = None  # its card in the heuristics library


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


# ---------- what a field looks like, and what to try in it ----------

def field_type(label: str, values: list[str]) -> str:
    text = label.lower()
    sample = next((v for v in values if v), "")
    if re.search(r"date|expir|birth|dob|valid until|issued", text) or re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{8}", sample):
        return "date"
    if "email" in text or "@" in sample:
        return "email"
    if re.search(r"phone|mobile|tel\b", text):
        return "phone"
    if re.search(r"amount|price|qty|quantity|age|number|count|total|\bno\b", text) or re.fullmatch(r"-?\d+([.,]\d+)?", sample):
        return "number"
    return "text"


# (variant name, how to recognise it in a value tried, what to suggest)
VARIANTS: dict[str, list[tuple[str, object, str]]] = {
    "date": [
        ("empty", lambda v: v == "", "leave it empty"),
        ("past", lambda v: _year(v) is not None and _year(v) < utcnow().year, "a date in the past (an expired document?)"),
        ("today", lambda v: v.startswith(utcnow().strftime("%Y-%m-%d")), "today's date (the boundary)"),
        ("far", lambda v: _year(v) is not None and _year(v) > utcnow().year + 30, "a date far in the future (2099-12-31)"),
        ("leap", lambda v: "02-29" in v or "29/02" in v, "29 February, in a leap year and in a non-leap year"),
        ("format", lambda v: "/" in v or "." in v, "another format: 31/12/2030, 12/31/2030"),
    ],
    "number": [
        ("empty", lambda v: v == "", "leave it empty"),
        ("zero", lambda v: v.strip() in ("0", "0.0"), "0"),
        ("negative", lambda v: v.strip().startswith("-"), "a negative number (-1)"),
        ("decimal", lambda v: bool(re.search(r"\d[.,]\d", v)), "a decimal (1.5, and 1,5)"),
        ("huge", lambda v: len(v.strip()) > 9, "a huge number (99999999999)"),
        ("letters", lambda v: bool(re.search(r"[A-Za-z]", v)), "letters (abc), and spaces around the number"),
    ],
    "email": [
        ("empty", lambda v: v == "", "leave it empty"),
        ("no_at", lambda v: v != "" and "@" not in v, "no @ (abebe.example.com)"),
        ("plus", lambda v: "+" in v, "plus addressing (abebe+test@example.com)"),
        ("case", lambda v: any(c.isupper() for c in v), "upper case (ABEBE@Example.com): same account?"),
        ("spaces", lambda v: v != v.strip(), "spaces before or after"),
        ("long", lambda v: len(v) > 64, "a very long address (over 64 characters before the @)"),
    ],
    "phone": [
        ("empty", lambda v: v == "", "leave it empty"),
        ("intl", lambda v: v.strip().startswith("+"), "the international form (+251 91 234 5678)"),
        ("spaces", lambda v: " " in v.strip() or "-" in v, "with spaces or dashes (091-234-5678)"),
        ("short", lambda v: 0 < len(re.sub(r"\D", "", v)) < 7, "too few digits"),
        ("letters", lambda v: bool(re.search(r"[A-Za-z]", v)), "letters"),
    ],
    "text": [
        ("empty", lambda v: v == "", "leave it empty"),
        ("spaces", lambda v: v != "" and v.strip() == "", "only spaces"),
        ("long", lambda v: len(v) > 100, "something very long (300+ characters)"),
        ("non_ascii", lambda v: any(ord(c) > 127 for c in v), "non-Latin script and accents: አበበ ከበደ, Zoë"),
        ("quote", lambda v: "'" in v or '"' in v, "an apostrophe (O'Brien) and quotes"),
        ("markup", lambda v: "<" in v or ">" in v, "markup: <b>bold</b>, <script>"),
        ("trim", lambda v: v != v.strip() and v.strip() != "", "spaces before and after the text"),
    ],
}


def _year(v: str) -> int | None:
    m = re.search(r"(19|20|21)\d{2}", v)
    return int(m.group(0)) if m else None


def untried_variants(kind: str, values: list[str]) -> list[str]:
    out = []
    for name, seen, suggestion in VARIANTS[kind]:
        if not any(seen(v) for v in values):
            out.append(suggestion)
    return out


# ---------- the nudges ----------

def _covered(text: str, notes: list[str]) -> bool:
    """Some note mentions it: most of its key words (60%, at least one) appear in one note.
    "Upload while offline" needs both words, so a note about another upload doesn't hide it."""
    words = keywords(text, limit=8)
    if not words:
        return True
    need = max(1, math.ceil(len(words) * 0.6))
    return any(sum(1 for w in words if w in n) >= need for n in notes)


def build(db: DBSession, session: models.Session) -> list[dict]:
    charter = session.charter
    notes = db.query(models.Note).filter(models.Note.session_id == session.id).order_by(models.Note.timestamp).all()
    note_text = [n.content.lower() for n in notes]
    bugs = db.query(models.Bug).filter(models.Bug.session_id == session.id).all()
    bug_text = [b.title.lower() for b in bugs]
    evidence = (db.query(models.Evidence).filter(models.Evidence.session_id == session.id)
                .order_by(models.Evidence.captured_at).all())
    nudges: list[Nudge] = []

    # Something went wrong and nothing covers it.
    for e in evidence:
        if e.kind not in coverage.ERROR_KINDS + ("slow_request",):
            continue
        summary = coverage._problem(e)
        if any(b.created_at >= e.captured_at for b in bugs) or _covered(summary, note_text + bug_text):
            continue  # a bug logged after it, or a note about it
        times = f" {e.count} times" if (e.count or 1) > 1 else ""
        if e.kind == "slow_request":
            nudges.append(Nudge(
                _id("problem", summary), "problem", f"Investigate: {summary}",
                f"The browser recorded this{times}, and no bug or note covers it yet.",
                "Performance: is it slow for users?", [
                    "Repeat it: slow every time, or only the first time (cold cache)?",
                    "Try a slow connection (DevTools → Network → Slow 4G): does the page say it's working?",
                    "Click again while it's still loading: does it send the request twice?",
                ], priority=1, heuristic_key="performance"))
            continue
        nudges.append(Nudge(
            _id("problem", summary), "problem", f"Investigate: {summary}",
            f"The browser recorded this{times}, and no bug or note covers it yet.",
            "Investigate what the app tells you", [
                "Repeat what you did just before it: does it happen every time?",
                "Log a bug if it's a problem, or a note saying why it's fine",
            ], priority=0))

    # Accessibility findings: one nudge for them all, with ways to check by hand.
    findings = [e for e in evidence if e.kind == "accessibility"]
    if findings and not any(w in n for n in note_text + bug_text for w in ("accessib", "a11y", "screen reader", "keyboard", "contrast")):
        nudges.append(Nudge(
            _id("a11y", str(len(findings) > 0)), "problem", f"Accessibility: {len(findings)} finding{'s' if len(findings) != 1 else ''} recorded",
            "e.g. " + "; ".join(f.summary for f in findings[:2]) + ". Checks find leads; people find barriers.",
            "Accessibility: can everyone use it?", [
                "Use the main form with the keyboard only: Tab, Shift+Tab, Enter, Esc",
                "Zoom to 200%: is anything cut off or overlapping?",
                "Turn on a screen reader (NVDA, or VoiceOver) and fill the form",
                "Log a bug for any finding that blocks someone",
            ], priority=2, heuristic_key="usability"))

    # The charter's risks and ideas that no note mentions yet.
    for r in charter.risks:
        if not _covered(r.failure_mode, note_text + bug_text):
            nudges.append(Nudge(
                _id("risk", r.id), "risk", f"{r.ref}: try to make it happen",
                f"This charter targets {r.ref} {r.title} (score {r.score}), and no note mentions it yet.",
                "Risk-based testing", [r.failure_mode], priority=1))
    for idea in charter.test_ideas or []:
        if not _covered(idea, note_text):
            nudges.append(Nudge(_id("idea", idea), "idea", "From the charter", "A starting idea on the charter that no note mentions yet.",
                                "Charter test ideas", [idea], priority=2))

    # Heuristics the tester picked for this charter, until a note mentions them.
    from app import heuristics as library
    for key in charter.heuristics or []:
        h = library.BY_KEY.get(key)
        if h and not any(h.name.lower() in n or key.replace("_", " ") in n for n in note_text):
            nudges.append(Nudge(_id("heuristic", key), "heuristic", f"Try: {h.name}", h.summary,
                                "Chosen for this charter", h.questions, priority=3, heuristic_key=key))

    # What the recorded browser saw the tester do.
    fills: dict[str, list[str]] = defaultdict(list)
    choices: dict[str, list[str]] = defaultdict(list)
    uploads: set[str] = set()
    submits: set[str] = set()
    for e in evidence:
        a = e.data or {}
        if e.kind != "action" or not isinstance(a.get("locator"), dict):
            continue
        target = describe_locator(a["locator"])
        name = a["locator"].get("name") or a["locator"].get("value") or target
        if a.get("action") == "fill" and not a.get("masked"):
            fills[name].append(str(a.get("value", "")))
        elif a.get("action") == "select":
            choices[name].append(str(a.get("value", "")))
        elif a.get("action") == "upload":
            uploads.add(name)
        elif a.get("action") == "click" and SUBMIT_WORDS.search(str(name)):
            submits.add(str(name))

    for name, values in fills.items():
        kind = field_type(name, values)
        todo = untried_variants(kind, values)
        if todo:
            tried = ", ".join(f'"{v}"' if v else "empty" for v in dict.fromkeys(values))
            nudges.append(Nudge(
                _id("field", name), "field", f'Vary "{name}"',
                f"You've tried {tried}. It looks like a {kind} field.",
                "Boundaries and odd inputs", todo[:5], priority=3, heuristic_key="goldilocks"))
    for name, values in choices.items():
        picked = list(dict.fromkeys(values))
        if len(picked) < 3:
            nudges.append(Nudge(
                _id("choice", name, str(len(picked))), "choice", f'Other choices in "{name}"',
                f"You've chosen {', '.join(picked) or 'one option'}.",
                "Cover the options", ["each other option, through to the end", "change the choice after filling the rest of the form"],
                priority=4, heuristic_key="configurations"))
    for name in uploads:
        nudges.append(Nudge(_id("upload", name), "upload", f'Odd files for "{name}"', "You uploaded a file here.",
                            "Files are inputs too", [
                                "a very large file (20 MB+)", "a zero-byte file", "the wrong type renamed (.pdf named .jpg)",
                                "a very long file name with spaces and non-Latin letters", "the same file twice"], priority=4, heuristic_key="goldilocks"))
    for name in submits:
        nudges.append(Nudge(_id("submit", name), "submit", f'Interrupt "{name}"', f'You clicked "{name}".',
                            "Interruptions and timing", [
                                f'double-click "{name}"', "press Back after it, then submit again", "refresh while it's working",
                                "leave the form open 30 minutes, then submit (session timeout)"], priority=4, heuristic_key="interruptions"))

    # Pages others opened but nobody tried.
    here = {coverage.page_key(e.url) for e in evidence if e.kind in ("navigation", "action")} - {None}
    others = coverage.build(db, charter.project)["pages"]
    untried = [p["path"] for p in others if "not_tried" in p["flags"] and p["path"] not in here]
    if untried:
        nudges.append(Nudge(_id("pages", *untried[:5]), "page", "Pages nobody has tried",
                            "Opened in earlier sessions, but nothing was clicked or typed there.",
                            "Explore where others haven't", untried[:5], priority=5))

    # Half the time gone, nothing found yet.
    now = session.paused_at or utcnow()
    minutes = ((now - session.started_at).total_seconds() - (session.total_paused_seconds or 0)) / 60
    target = session.duration_target_minutes or 90
    if minutes >= target / 2 and not bugs and not any(n.tag in ("bug", "question", "issue") for n in notes):
        nudges.append(Nudge(_id("time", "half"), "time", "Half the session, nothing found yet",
                            f"{round(minutes)} of {round(target)} minutes, and no bug, issue or question logged.",
                            "Change your approach", [
                                "Goldilocks: too small, too big, just right", "a different user: new, impatient, on a slow connection",
                                "follow the data: create, change, delete, then look for it everywhere", "ask a question you've been assuming the answer to"],
                            priority=6, heuristic_key="personas"))

    dismissed = set(session.dismissed_nudges or [])
    unique = list({n.id: n for n in nudges}.values())  # the same problem recorded twice is one nudge
    kept = [n for n in unique if n.id not in dismissed]
    kept.sort(key=lambda n: n.priority)
    return [asdict(n) for n in kept[:MAX_NUDGES]]
