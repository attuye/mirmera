"""Exploration agent: an AI explores one charter in a headless browser, the way a
tester runs a session, and leaves everything for a tester to review.

What it leaves behind is an ordinary Mirmera session on the charter, by the tester
"Mirmera agent", paused for a tester to review and end (ending drafts the debrief):
  - notes for what it tried and noticed (tag "test"), and its wrap-up;
  - the actions it took and the pages it opened, as the session's recording;
  - the failed requests, console and page errors the app produced;
  - a bug report draft for each problem it found: unreviewed (no kind yet), with
    the steps that led there, what it expected and saw, and a screenshot.
Nothing is filed to a tracker: a tester reviews each draft first, as with any bug.

The guard rails are the reproduction agent's (app/agent/reproduce.py): it stays
inside the app, never sees secrets, every problem's symptom is checked by Mirmera
before a draft is made, and it has a step budget, a time limit and can be cancelled.
"""
import json
import logging
import os
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from app import models, schemas
from app.agent.reproduce import act, in_app, observe, verify, _back_into_app, describe as describe_browser
from app.ai.base import LLMProvider, ProviderError
from app.ai.llm_json import LLMOutputError, complete_json
from app.database import SessionLocal
from app.evidence_text import _defang as defang
from app.regression.plan import origin
from app.regression.replay import Signals, _first_line
from app.regression.steps import StepError, clean_steps
from app.runner import Signal, describe_action
from app.timeutil import utcnow

logger = logging.getLogger(__name__)

PROMPT = (Path(__file__).resolve().parents[1] / "ai" / "prompts" / "explore_step.md").read_text(encoding="utf-8")
PROMPT_VERSION = "explore-step-v4"  # v4: dismissed_by_the_team
JUDGE_PROMPT = (Path(__file__).resolve().parents[1] / "ai" / "prompts" / "explore_judge.md").read_text(encoding="utf-8")
TESTER = "Mirmera agent"
HISTORY_LIMIT = 20
RUN_TIMEOUT_S = 900  # at least; a run may take STEP_TIME_S per step of its budget
STEP_TIME_S = 80
MAX_BAD_REPLIES = 4  # unusable replies in a row before the run stops: the model is not up to it
STEP_IN_AFTER = 3  # steps in a row that taught nothing before Mirmera takes the next step itself
REPLIES_PER_STEP = 2  # model replies per step of the budget, refused ones too: a model that is only ever refused still stops
SUBMITS = ("click", "press", "try")
TOURS = True  # after the first accepted submit on a form, Mirmera tours the flow itself
INTERRUPTIONS = True  # the flow tour signs out halfway and saves the same record from two tabs
SAVED_TEXT = True  # the flow tour sends Ge'ez and punctuation and checks they come back as typed
KEYBOARD = True
LAYOUT = True  # each new page is looked at 390 px wide once  # the flow tour ends with the form done with the keyboard only
ACCESS_CHECK = True  # after a signed-in run, the user's own pages are opened signed out and as the second user
CONFIRM_RUNS = 3  # each new draft's steps, replayed in this many fresh browsers when the run ends
# A note that says the app accepted or refused something: only true after a submit.
CLAIM = re.compile(r"\b(accepts|accepted|rejects|rejected|refuses|refused|works|succeeds|successful(?:ly)?|confirm(?:s|ed|ing)?)\b", re.I)

ACTIVE: set[str] = set()  # run ids being worked on in this process
# The browser's clock for this run, when one is set (each run is its own task, so its own value).
CLOCK_AT: ContextVar[datetime | None] = ContextVar("clock_at", default=None)
ENGINE: ContextVar[str] = ContextVar("engine", default="chromium")  # chromium | firefox | webkit, for this run
VIEWPORT = {"width": 1280, "height": 800}  # fixed per run, so a target's box can be drawn on any frame
PHONE = {"width": 390, "height": 844}
SLOW_NETWORK = {"offline": False, "latency": 400, "downloadThroughput": 50 * 1024, "uploadThroughput": 20 * 1024}


def steps_used(steps: list[dict]) -> int:
    """The steps that count against the budget: a refused step was never run, and a flow tour is
    Mirmera's own, so they cost none."""
    return sum(1 for s in steps if s.get("status") != "refused" and s.get("kind") not in ("tour", "confirm", "access"))


def context_options(conditions: dict | None) -> dict:
    """How the agent's browser is set up: a desktop by default, or a phone, in a language
    and time zone (the network is slowed separately, on the page)."""
    c = conditions or {}
    out: dict = {"viewport": PHONE if c.get("device") == "phone" else VIEWPORT}
    if c.get("device") == "phone":
        out |= {"is_mobile": True, "has_touch": True, "device_scale_factor": 2,
                "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                              "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"}
    if c.get("locale"):
        out["locale"] = c["locale"]
    if c.get("timezone") or c.get("clock_at"):
        from app.agent.clock import DEFAULT_TZ
        out["timezone_id"] = c.get("timezone") or DEFAULT_TZ
    return out


def _engine_label() -> str:
    from app.browsers import label
    return label(ENGINE.get())


def urlsplit_path(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url or "").path or "/"


def run_today():
    """The user's today: by the browser's clock when the run set one, else UTC's."""
    at = CLOCK_AT.get()
    return at.date() if at else utcnow().date()


def clock_line(at: datetime | None = None) -> str | None:
    """The first step to reproduce a bug found with the clock set, for a person."""
    at = at or CLOCK_AT.get()
    if not at:
        return None
    from app.agent import clock
    return f"Set the computer's clock to {clock.describe(at)}"


async def set_clock(context, at: datetime | None = None) -> None:
    """Fixes the clock of every page in this browser context at the run's moment (timers still run)."""
    at = at or CLOCK_AT.get()
    if at:
        await context.clock.set_fixed_time(at)


@dataclass
class Live:
    """What the agent's browser shows right now, for watching it work."""
    seq: int = 0
    frame: bytes | None = None
    url: str = ""


LIVE: dict[str, Live] = {}  # run id -> its latest frame, while it runs


async def _start_screencast(page, live: Live, viewport: dict = VIEWPORT, slow: bool = False) -> None:
    """Chromium sends a frame whenever the page repaints: kept as the latest frame. Firefox and
    WebKit have no screencast: a screenshot every 0.7 s instead, while the page is open."""
    import asyncio
    import base64
    if ENGINE.get() != "chromium":
        async def shoot():
            while not page.is_closed():
                try:
                    live.frame, live.url = await page.screenshot(type="jpeg", quality=50), page.url
                    live.seq += 1
                except Exception:
                    pass
                await asyncio.sleep(0.7)
        asyncio.ensure_future(shoot())
        return
    try:
        cdp = await page.context.new_cdp_session(page)

        async def on_frame(params):
            live.frame, live.url = base64.b64decode(params["data"]), page.url
            live.seq += 1
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:
                pass

        cdp.on("Page.screencastFrame", lambda params: asyncio.ensure_future(on_frame(params)))
        if slow:  # a slow mobile network, for the whole run
            await cdp.send("Network.enable")
            await cdp.send("Network.emulateNetworkConditions", SLOW_NETWORK)
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60,
                                              "maxWidth": viewport["width"], "maxHeight": viewport["height"]})
    except Exception as err:  # watching is a nicety: the run goes on without it
        logger.info("No live view for this run: %s", _first_line(err))


async def _recording_context(browser, folder: Path, run_id: str, options: dict | None = None):
    """A browser context that films itself (a WebM video, written when the context closes)."""
    options = options or {"viewport": VIEWPORT}
    har = {"record_har_path": str(_raw_har(folder, run_id)), "record_har_content": "embed"}  # cleaned when kept
    try:
        return await browser.new_context(**options, **har, record_video_dir=str(folder / f"video-{run_id[:8]}"),
                                         record_video_size=options["viewport"])
    except Exception as err:  # e.g. Playwright's ffmpeg isn't installed: explore without a recording
        logger.info("No recording for this run: %s", _first_line(err))
        try:
            return await browser.new_context(**options, **har)
        except Exception:
            return await browser.new_context(**options)


def _raw_har(folder: Path, run_id: str) -> Path:
    return folder / f"network-{run_id[:8]}.raw.har"


def _keep_network(folder: Path, run_id: str, secrets: list[str]) -> str | None:
    """Playwright's network log (written when the context closed), kept with cookies, authorization,
    passwords, tokens and pictures taken out; the raw one is deleted. None when there is none."""
    from app.har import clean_har_file
    raw = _raw_har(folder, run_id)
    if not raw.is_file():
        return None
    name = f"network-{run_id[:8]}.har"
    try:
        clean_har_file(raw, folder / name, secrets)
        return name
    except Exception as err:
        logger.info("The run's network log could not be kept: %s", _first_line(err))
        return None
    finally:
        raw.unlink(missing_ok=True)  # never leave the uncleaned log behind


async def _keep_recording(context, folder: Path, run_id: str) -> str | None:
    """Closes the context so its video is complete, and keeps it in the session's folder."""
    if context is None:
        return None
    try:
        videos = [p.video for p in context.pages if p.video]
        await context.close()
        if not videos:
            return None
        source = Path(await videos[0].path())
        name = f"explore-{run_id[:8]}.webm"
        source.replace(folder / name)
        try:
            source.parent.rmdir()
        except OSError:
            pass
        return name
    except Exception as err:
        logger.info("The run's recording could not be kept: %s", _first_line(err))
        return None


async def _step_frame(page, folder: Path, run_id: str, n: int, a: schemas.ExploreAction) -> dict:
    """The page as the agent saw it when it chose step n, and where it is about to act."""
    out: dict = {}
    try:
        name = f"explore-{run_id[:8]}-step-{n}.jpg"
        folder.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(folder / name), type="jpeg", quality=60)
        out["frame"] = name
        if a.locator and a.kind in ("click", "fill", "fill_secret", "select", "check", "uncheck", "press"):
            from app.regression.replay import _locator
            box = await _locator(page, a.locator.model_dump(exclude_none=True)).first.bounding_box(timeout=1_000)
            if box:
                out["box"] = [round(box[k]) for k in ("x", "y", "width", "height")]
    except Exception:
        pass
    return out


@dataclass
class CharterContext:
    mission: str
    focus: list[str] = field(default_factory=list)  # the form fields the charter is about (see focus_fields)
    test_ideas: list[str] = field(default_factory=list)
    oracles: list[str] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)  # ref, title, failure_mode
    story: str | None = None  # the story's claims, when the charter belongs to one
    criteria: list[str] = field(default_factory=list)  # its acceptance criteria, one rule each
    earlier: list[str] = field(default_factory=list)  # what earlier runs on this charter or story found
    app_map: str | None = None  # the app's pages and forms, from the latest survey
    signed_in_as: str | None = None  # Mirmera signed in to the app as this user before the run
    dismissed: list[str] = field(default_factory=list)  # what the team said isn't a bug here, and why
    project_id: str | None = None
    ethiopia: bool = False  # the Ethiopia pack (a project setting)
    test_data: list[dict] = field(default_factory=list)  # the team's values (secrets in memory only): never in the prompt

    def has_rules(self) -> bool:
        return bool(self.criteria or self.risks)


CRITERIA_HEADING = re.compile(r"^\W*acceptance criteria\W*$", re.I)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


OUT_OF_SCOPE = re.compile(r"out of scope|not in scope|non-goals?|later\b", re.I)


def _items(lines: list[str], loose: bool = False) -> list[str]:
    """Bulleted or numbered items, each with its wrapped lines joined ("…in\n   Latin or Ge'ez
    script." is one item). A blank line or a heading ends an item. loose: a plain line that
    doesn't continue an item is an item too (under an "Acceptance criteria" heading)."""
    items: list[list[str]] = []
    cur: list[str] | None = None
    for raw in lines:
        line = raw.strip()
        if BULLET.match(line):
            cur = [BULLET.sub("", line)]
            items.append(cur)
        elif not line or line.startswith("#"):
            cur = None
        elif cur is not None:
            cur.append(line)
        elif loose:
            cur = [line]
            items.append(cur)
    return [" ".join(i) for i in items]


def _clean(criterion: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\*\*|__|`", "", criterion)).strip()


def criteria_from(story: str | None) -> list[str]:
    """The story's acceptance criteria, whole: the lines under an "Acceptance criteria" heading
    (bulleted or not), or else its bulleted or numbered items, leaving out what is out of scope.
    An item wrapped over several lines is one criterion: cut at the first line, "names in Latin
    or Ge'ez script" lost its "Ge'ez", and "expired" lost what expired means."""
    if not story:
        return []
    story = re.sub(r"<!--.*?-->", "", story, flags=re.S)  # commented-out text isn't the story
    lines = story.splitlines()
    out: list[str] = []
    for i, raw in enumerate(lines):
        line = raw.strip()
        if CRITERIA_HEADING.match(line) or re.match(r"^\W*acceptance criteria\s*:", line, re.I):
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            section: list[str] = []
            for nxt in lines[i + 1:]:
                n = nxt.strip()
                if n.startswith("#") or (n.endswith(":") and not BULLET.match(n)):
                    break  # the next section
                section.append(nxt)
            out = ([rest] if rest else []) + _items(section, loose=True)
            break
    if not out:
        kept, skipping = [], False
        for raw in lines:
            line = raw.strip()
            if line.startswith("#") or (line.endswith(":") and not BULLET.match(line) and len(line) < 60):
                skipping = bool(OUT_OF_SCOPE.search(line))
            if not skipping:
                kept.append(raw)
        out = _items(kept)
    return [c[:300] for c in map(_clean, out) if len(c) > 3][:20]


def _save(run_id: str, **fields) -> None:
    with SessionLocal() as db:
        run = db.get(models.ExplorationRun, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        db.commit()


def _cancelled(run_id: str) -> bool:
    with SessionLocal() as db:
        run = db.get(models.ExplorationRun, run_id)
        return bool(run and run.cancel_requested)


def describe(a: schemas.ExploreAction) -> str:
    if a.kind == "note":
        return f"note: {a.note}"
    if a.kind == "problem":
        return f"problem: {a.problem.title}"
    if a.kind == "done":
        return "done"
    if a.kind == "try":
        return try_text(a.values)
    return describe_browser(a.as_browser_action())


def recorded_action(a: schemas.ExploreAction) -> dict | None:
    """The action in the recorder's shape, for the session's recording and the bug's steps."""
    if a.kind not in ("click", "fill", "fill_secret", "select", "check", "uncheck", "press"):
        return None
    out = {"action": "fill" if a.kind == "fill_secret" else a.kind,
           "locator": a.locator.model_dump(exclude_none=True) if a.locator else {}}
    if a.kind == "fill_secret":
        out |= {"masked": True, "secret": a.secret}
    elif a.value is not None:
        out["value"] = a.value
    if a.key:
        out["key"] = a.key
    return out


# ---------- what the form holds now ----------

FORM_JS = """() => {
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
  const labelOf = (el) => {
    const byFor = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    const text = (byFor && byFor.innerText) || (el.closest('label') && el.closest('label').innerText)
      || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.name || el.id || el.type
    return text.replace(/\\s+/g, ' ').trim()
  }
  const fields = []
  const all = [...document.querySelectorAll('input, select, textarea')]
  for (const [idx, el] of all.entries()) {
    const type = (el.type || el.tagName).toLowerCase()
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type) || !visible(el)) continue
    let value
    if (type === 'checkbox' || type === 'radio') value = el.checked ? 'checked' : 'not checked'
    else if (type === 'file') value = el.files && el.files.length ? [...el.files].map((x) => x.name).join(', ') : ''
    else if (type === 'password') value = el.value ? '(filled)' : ''
    else if (el.tagName === 'SELECT') value = el.selectedOptions[0] ? el.selectedOptions[0].text : ''
    else value = el.value
    fields.push({ label: labelOf(el), type, value, required: el.required, idx, accept: el.getAttribute('accept') || '',
                  keys: [el.id, el.name, el.getAttribute('placeholder'), el.getAttribute('aria-label')].filter(Boolean),
                  ...(el.tagName === 'SELECT' ? { options: [...el.options].map((o) => o.text) } : {}) })
  }
  // The button that sends the form: a submit button inside a form with fields, else the only button.
  const buttons = [...document.querySelectorAll('button, input[type=submit]')].filter(visible)
  const submitEl = buttons.find((b) => b.form && (b.type === 'submit')) || (buttons.length === 1 ? buttons[0] : null)
  const submit = submitEl ? (submitEl.innerText || submitEl.value || '').replace(/\\s+/g, ' ').trim() : ''
  const links = [...document.querySelectorAll('a[href]')].filter(visible)
    .map((a) => ({ text: a.innerText.replace(/\\s+/g, ' ').trim(), href: a.href })).filter((l) => l.text).slice(0, 40)
  const messages = [...document.querySelectorAll('[role=status], [role=alert], [aria-live]')]
    .filter(visible).map((el) => el.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean)
  // Every visible line of text: what appears after a submit (a toast, an error under a field,
  // a new page) is the app's answer even when it isn't in a status area.
  const lines = [...new Set((document.body ? document.body.innerText : '').split('\\n')
    .map((l) => l.replace(/\\s+/g, ' ').trim()).filter((l) => l.length > 1))].slice(0, 400)
  const h1 = document.querySelector('h1')
  return { fields, messages, lines, title: document.title, heading: h1 ? h1.innerText.trim() : '', submit, links }
}"""


@dataclass
class FormState:
    fields: list[dict] = field(default_factory=list)  # label, type, value, required
    messages: list[str] = field(default_factory=list)  # what the page says in its status and alert areas
    lines: list[str] = field(default_factory=list)  # every visible line of text
    url: str = ""
    title: str = ""
    heading: str = ""
    submit: str = ""  # the text of the button that sends the form
    links: list[dict] = field(default_factory=list)  # text, href

    def text(self) -> str:
        """For the AI: every field with what it holds now (empty ones marked), and the page's messages.
        Small models lose track of which fields are filled; this is how a submit's result can be read."""
        if not self.fields and not self.messages:
            return ""
        lines = ["Form fields now:"] if self.fields else []
        for f in self.fields[:30]:
            v = f["value"]
            shown = v if f["type"] in ("checkbox", "radio") or v == "(filled)" else (f'"{v[:120]}"' if v else "(empty)")
            lines.append(f'- "{f["label"][:80]}" ({f["type"]}{", required" if f["required"] else ""}): {shown}')
        lines.append("Messages on the page: " + ("; ".join(f'"{m[:200]}"' for m in self.messages[:5]) or "(none)"))
        return "\n".join(lines)

    def values(self) -> str:
        """The fields and their values only: the same form submitted twice has the same values."""
        return "|".join(f'{f["label"]}={f["value"]}' for f in self.fields)

    def value_of(self, name: str | None) -> str | None:
        name = (name or "").strip().lower()
        hits = [f["value"] for f in self.fields if name and f["label"].lower() == name]
        return hits[0] if len(hits) == 1 else None


async def form_state(page) -> FormState:
    try:
        data = await page.evaluate(FORM_JS)
        from app import test_data
        if test_data.secrets():  # the team's secret test data never leaves the browser
            for f in data.get("fields") or []:
                f["value"] = test_data.mask(f.get("value") or "")
            data["messages"] = [test_data.mask(m) for m in data.get("messages") or []]
            data["lines"] = [test_data.mask(m) for m in data.get("lines") or []]
        test_data.PAGE.set(page.url)
        return FormState(fields=data.get("fields") or [], messages=data.get("messages") or [],
                         lines=[l[:200] for l in data.get("lines") or []], url=page.url,
                         title=(data.get("title") or "")[:200], heading=(data.get("heading") or "")[:200],
                         submit=(data.get("submit") or "")[:80], links=data.get("links") or [])
    except Exception:  # navigating, or a page without a DOM to read
        return FormState()


def action_key(a: schemas.ExploreAction) -> str:
    """The whole action: its description shortens long values, and two different long names
    must not count as the same try."""
    return a.model_dump_json(include={"kind", "locator", "value", "values", "key", "url"})


def baseline_key(url: str) -> tuple[str, str, str]:
    """The form sent with valid data on this page: done once, it shows what success looks like.
    The form's current values don't tell two of them apart (they hold the last try's values)."""
    return ("try the form with valid data", (url or "").split("#")[0], "")


def pointless(a: schemas.ExploreAction, url: str, before: FormState, tried: dict[tuple[str, str, str], int]) -> str | None:
    """Why a step would learn nothing, or None. A small model loops: it submits the same form
    again and again, or fills a field with the value it already holds. Such a step isn't run.
    Signing out isn't run either: the run is signed in on purpose."""
    from app.sign_in import SIGN_OUT
    target = " ".join([x for x in ([a.locator.name, a.locator.value] if a.locator else []) if x] + [a.url or ""])
    if a.kind in ("click", "press", "goto") and SIGN_OUT.search(target.replace("-", " ").replace("_", " ")):
        return "the run is signed in on purpose: stay signed in and test as this user"
    if a.kind == "fill" and a.locator:
        f = find_field(before, field_name(a))
        current = f["value"] if f else before.value_of(a.locator.value or a.locator.name)
        if current is not None and current == a.value:
            return (f'that field already holds "{a.value}". Change a different field, or change this one to a '
                    "different value, then submit")
    if a.kind == "try" and not a.values and baseline_key(url) in tried:
        return (f"you sent the form with valid data at step {tried[baseline_key(url)]} on this page, and nothing has "
                "failed since, so it can't show anything new. Give the field and value you mean in values")
    if a.kind in SUBMITS:
        key = (action_key(a), url, before.values())
        if key in tried:
            return (f"you did exactly this at step {tried[key]} on this page with the same field values, and it can't show "
                    "anything new. Change one field first (keep the others valid), or try another idea")
    return None


# ---------- acting on fields the way a person would ----------

FIELDS = "input, select, textarea"
TRUTHY = {"true", "yes", "on", "1", "checked", "check", "tick", "ticked", "x", "accept", "accepted", "agree"}


def _key(text: str) -> str:
    """"documentType", "document-type" and "Document type" are the same name."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    return re.sub(r"[^\w]+", " ", text, flags=re.U).strip().lower()


def find_field(state: FormState, name: str | None) -> dict | None:
    """The form field a small model means, by its label, id, name, placeholder or aria-label:
    exactly first, then the one field whose label contains the name (or the other way round)."""
    k = _key(name or "")
    if not k:
        return None
    exact = [f for f in state.fields if k in {_key(x) for x in [f["label"], *f.get("keys", [])]}]
    if len(exact) >= 1:
        return exact[0]
    near = [f for f in state.fields if k in _key(f["label"]) or (_key(f["label"]) and _key(f["label"]) in k)]
    return near[0] if len(near) == 1 else None


def field_name(a: schemas.ExploreAction) -> str | None:
    if a.locator and a.locator.method != "role":
        return a.locator.value
    if a.locator and a.locator.role in ("textbox", "combobox", "checkbox", "radio", "spinbutton", "searchbox"):
        return a.locator.name
    return None


def option_of(f: dict, value: str) -> str | None:
    """The option of a select that a value means ("passport" is "Passport")."""
    opts = f.get("options") or []
    for o in opts:
        if o == value:
            return o
    hits = [o for o in opts if _key(o) == _key(value)] or [o for o in opts if _key(value) and _key(value) in _key(o)]
    return hits[0] if hits else None


def valid_value(f: dict, today, rules: dict[str, str] | None = None) -> str | None:
    """Ordinary valid data for a field, so a try changes only the fields it names. None: leave it.
    Within what the rules allow: a quantity of 1 to 10 gets 1, not 12345. The team's test data first."""
    from app import test_data
    if (fresh := test_data.fresh_for(f)) is not None:  # the app said "already registered": a new one each time
        return fresh
    if (mine := test_data.valid_for(f)) is not None:
        return mine
    v = _plain_valid(f, today)
    if v is not None:
        test_data.note_made_up(f)
    for c in field_rules(f, rules) if rules else []:
        if c["kind"] == "range":
            lo, hi = c["lo"], c["hi"]
            pick = lo if lo is not None else (min(1.0, hi) if hi is not None else None)
            if pick is not None:
                v = f"{pick:g}"
        elif c["kind"] == "length" and v and c["hi"] is not None and len(v) > c["hi"]:
            v = v[:int(c["hi"])]
    return v


def _plain_valid(f: dict, today) -> str | None:
    from datetime import timedelta
    label, kind = f["label"].lower(), f["type"]
    if kind.startswith("select"):
        opts = [o for o in f.get("options") or [] if not re.search(r"^(choose|select|pick|--|-)", o.strip().lower())]
        return opts[0] if opts else None  # the same baseline every time, whatever was chosen before
    if kind == "checkbox":
        return "checked" if f.get("required") or re.search(r"accept|agree|terms|consent|confirm", label) else None
    if kind == "file":  # an ordinary photo, or a PDF when that's all the field takes
        accept = (f.get("accept") or "").lower()
        return "@file:id-scan.pdf" if "pdf" in accept and "image" not in accept and "jp" not in accept else "@file:id-photo.jpg"
    if kind in ("radio", "range", "color"):
        return None
    if kind == "date" or "date" in label:
        if "birth" in label or "dob" in label:
            return "1990-05-15"
        if re.search(r"expir|valid|until|end", label):
            return (today + timedelta(days=365)).isoformat()
        return today.isoformat()
    if kind == "password":
        return "Mirmera-Test-Pass-24!"
    if kind == "email" or "mail" in label:
        return "abebe.kebede@example.com"
    if kind == "tel" or "phone" in label or "mobile" in label:
        return "+251911223344"
    if kind == "number":
        return "12345"
    if "number" in label or " no" in f" {label}" or "id" == label:
        return "A1234567"
    if "name" in label:
        return "Abebe Kebede"
    if kind == "url" or "website" in label:
        return "https://example.com"
    return "Test value"


async def set_field(page, f: dict, kind: str, value: str | None) -> tuple[str, str, str]:
    """Sets one field as a person would, whatever the action said: a dropdown gets its option
    chosen, a checkbox gets ticked. Returns (status, detail, the action that was done)."""
    el = page.locator(FIELDS).nth(f["idx"])
    t = f["type"]
    if t.startswith("select"):
        opt = option_of(f, value or "")
        if opt is None:
            return "failed", f'"{f["label"]}" has no option "{value}". Its options: ' + ", ".join(f'"{o}"' for o in f.get("options") or []), kind
        await el.select_option(label=opt, timeout=5_000)
        return "ok", (f'"{f["label"]}" is a dropdown, so Mirmera chose "{opt}"' if kind != "select" else ""), "select"
    if t == "file":
        from app.agent import files
        name = files.name_of(value)
        if not name:
            return "failed", (f'"{f["label"]}" takes a file: give one of Mirmera\'s, e.g. "@file:{files.VALID}" or '
                              '"@file:big-photo.png" (see the test ideas)'), kind
        await el.set_input_files(str(files.path_of(name)), timeout=10_000)
        return "ok", f'Mirmera put {files.FILES[name]["what"]} ({name}) in "{f["label"]}"', "upload"
    if t in ("checkbox", "radio"):
        on = kind == "check" or (kind != "uncheck" and (value or "").strip().lower() in TRUTHY)
        await (el.check if on else el.uncheck)(timeout=5_000)
        how = "check" if on else "uncheck"
        return "ok", (f'"{f["label"]}" is a checkbox, so Mirmera {"ticked" if on else "cleared"} it' if kind != how else ""), how
    if kind in ("check", "uncheck"):
        return "failed", f'"{f["label"]}" is not a checkbox; fill it with a value', kind
    await el.fill(expand(value, f), timeout=5_000)
    return "ok", "", "fill"


def field_changes(before: FormState, after: FormState) -> list[tuple[dict, str, str]]:
    """The fields whose value changed: (field, was, now)."""
    was = {(f["label"], f.get("idx")): f["value"] for f in before.fields}
    return [(f, was[(f["label"], f.get("idx"))], f["value"]) for f in after.fields
            if (f["label"], f.get("idx")) in was and was[(f["label"], f.get("idx"))] != f["value"]]


async def do_try(page, a: schemas.ExploreAction, before: FormState, today,
                 rules: dict[str, str] | None = None) -> tuple[str, str, list[dict], FormState | None]:
    """One experiment: the named fields get the given values, every other field ordinary valid
    data, then the form is sent. Returns (status, detail, recorded actions, the form as sent)."""
    done, notes, recorded = set(), [], []
    for name, value in a.values.items():
        f = find_field(before, name)
        if not f:
            return "failed", f'no field "{name}" on this page. Fields: ' + ", ".join(f'"{x["label"]}"' for x in before.fields[:12]), [], None
        status, detail, how = await set_field(page, f, "fill", value)
        if status != "ok":
            return status, detail, [], None
        done.add(f["idx"])
        recorded.append(_rec(how, f, value))
    for f in before.fields:
        if f["idx"] in done:
            continue
        v = valid_value(f, today, rules)
        if v is None or (f["type"] not in ("checkbox",) and f["value"] == v):
            continue
        if f["type"] == "checkbox" and f["value"] == "checked":
            continue
        status, detail, how = await set_field(page, f, "fill", v)
        if status == "ok":
            recorded.append(_rec(how, f, v))
        else:
            notes.append(detail)
    sent = await form_state(page)
    if not before.submit:
        return "failed", "the fields are set, but Mirmera found no button that sends this form: click it yourself", recorded, None
    await page.get_by_role("button", name=before.submit, exact=True).first.click(timeout=5_000)
    recorded.append({"action": "click", "locator": {"method": "role", "role": "button", "name": before.submit}})
    return "ok", "; ".join(notes), recorded, sent


def _rec(how: str, f: dict, value: str) -> dict:
    out = {"action": how, "locator": {"method": "label", "value": f["label"]}}
    if how in ("fill", "select"):
        out["value"] = value
    if how == "upload":
        out["files"] = [value.removeprefix("@file:")]
    return out


def try_text(values: dict[str, str]) -> str:
    return "try " + (", ".join(f'{k} = "{v[:60]}"' for k, v in values.items()) or "the form with valid data") + ", then submit"


def _stems(text: str) -> set[str]:
    """Content words, plural or not: "documents" and "document" are one word here."""
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"\w+", (text or "").lower()) if len(w) > 3}


def suggest(ctx: "CharterContext", state: FormState, tried_values: dict[str, set[str]], dirty: dict[str, str],
            submits: int, visited: set[str], today) -> tuple[dict, str] | None:
    """The next step an experienced tester would take here: (action, why). Shown to the AI when it
    loops, and run by Mirmera itself when the AI is stuck. The charter decides which field first."""
    if dirty and state.submit:
        return {"kind": "try", "values": dict(dirty)}, "you changed fields but haven't submitted, so nothing was tested yet"
    if state.fields and state.submit and not submits:
        return {"kind": "try", "values": {}}, "first send the form once with ordinary valid data, to see what success looks like"
    words = _stems(" ".join([ctx.mission, *ctx.test_ideas]))
    fields = sorted(state.fields[:12], key=lambda f: len(_stems(f["label"]) & words), reverse=True)
    best = len(_stems(fields[0]["label"]) & words) if fields else 0
    links = [l for l in state.links if l["href"].split("#")[0] not in visited and _stems(l["text"]) & words]
    link = max(links, key=lambda l: len(_stems(l["text"]) & words), default=None)
    if link and submits and len(_stems(link["text"]) & words) >= best:
        return {"kind": "goto", "url": link["href"]}, f'the charter is about "{link["text"]}", not looked at yet'
    if state.submit:
        for f in fields:
            for what, v in ideas_for(f, today, rules_of(ctx)):
                if not is_tried(f, v, tried_values):
                    return {"kind": "try", "values": {f["label"]: v}}, f'the next test idea for {f["label"]}: {what}'
    if link:
        return {"kind": "goto", "url": link["href"]}, f'the charter is about "{link["text"]}", not looked at yet'
    return None


def suggestion_text(s: tuple[dict, str] | None) -> str:
    return f" Next, for example: {json.dumps(s[0], ensure_ascii=False)} ({s[1]})." if s else ""


# ---------- test ideas: an experienced tester's checklist, per field ----------

GEEZ_NAME = "አበበ ከበደ"


def ideas_for(f: dict, today, rules: dict[str, str] | None = None) -> list[tuple[str, str]]:
    """(what, value) ideas for one field: the boundaries its rules give first, then the
    variations an experienced tester tries for its type and label."""
    from app import test_data
    first = (boundary_ideas(f, rules) if rules else [])
    first += [(w, v) for w, v in test_data.ideas(f) if v not in {x for _, x in first}]  # the team's own bad values
    from app.agent import ethiopia
    first += [(w, v) for w, v in ethiopia.ideas(f) if v not in {x for _, x in first}]  # how people here write it
    have = {v for _, v in first}
    rest = [(w, v) for w, v in _type_ideas(f, today) if v not in have]
    have |= {v for _, v in rest}
    from app.agent import magnet  # Bug Magnet's lists: once the obvious ideas are done
    return first + rest + [(w, v) for w, v in magnet.ideas(f) if v not in have]


def _type_ideas(f: dict, today) -> list[tuple[str, str]]:
    from datetime import timedelta
    label, kind = f["label"].lower(), f["type"]
    if kind == "date" or "date" in label:
        edges = []
        if CLOCK_AT.get():  # the clock is at a calendar edge: the dates around it first
            from app.agent import clock
            edges = clock.date_ideas(today)
        plain = [("a past date", "2020-01-01"), ("today", today.isoformat()),
                 ("tomorrow", (today + timedelta(days=1)).isoformat()), ("29 February", "2028-02-29"),
                 ("far in the future", "2099-12-31")]
        return edges + [(w, v) for w, v in plain if v not in {x for _, x in edges}]
    if kind.startswith("select"):
        return [(f"option {o}", o) for o in f.get("options") or []]
    if kind == "file":
        from app.agent import files
        return files.ideas()
    if kind in ("checkbox", "radio") or kind == "password":
        return []
    if kind == "email" or "mail" in label:
        return [("a valid address", "name@example.com"), ("no @", "name.example.com"), ("no domain", "name@"),
                ("very long", "@long64@example.com")]
    if kind == "tel" or "phone" in label or "mobile" in label:
        return [("international format", "+251911223344"), ("local format", "0911223344"), ("letters", "abcdefg"),
                ("too short", "12")]
    if kind == "number" or any(w in label for w in ("number", "amount", "quantity", "age")):
        return [("zero", "0"), ("negative", "-1"), ("very large", "999999999999"), ("letters mixed in", "12a4"),
                ("spaces around", " 1234 ")]
    if "name" in label:
        return [("Ge'ez script", GEEZ_NAME), ("apostrophe and hyphen", "O'Brien-Smith"), ("one letter", "A"),
                ("very long", "@long300"), ("spaces around", "  John Smith  "), ("an emoji", "John 😀 Smith")]
    return [("empty", ""), ("only spaces", "   "), ("very long, a counterstring", "@count300"), ("markup", "<b>bold</b>"),
            ("quotes", "It's \"quoted\"")]


LONG = re.compile(r"@long(\d{1,5})")
COUNT = re.compile(r"@count(\d{1,5})")


def counterstring(n: int, mark: str = "*") -> str:
    """James Bach's counterstring (PerlClip): "*3*5*7*9*12*15*…", where each number says the position
    of the * after it. Cut anywhere, the text says how long it still is: "…*47*49*" was kept to 50."""
    parts, pos = [], n
    while pos > 0:
        piece = f"{pos}{mark}"
        piece = piece[-pos:] if len(piece) > pos else piece
        parts.append(piece)
        pos -= len(piece)
    return "".join(reversed(parts))


def counter_cut(sent: str, kept: str | None, page_text: str) -> list[str]:
    """What a counterstring shows after a step, without AI: the field kept fewer characters than were
    sent, or the page shows it back cut short. Plain sentences for the notes; none when it's whole."""
    out = []
    if kept is not None and kept != sent and sent.startswith(kept):
        out.append(f"took {len(kept)} of {len(sent)} characters")
    whole = kept if kept and sent.startswith(kept) else sent
    lo, hi = 0, len(whole)  # the longest start of it the page shows (a shorter start is shown whenever a longer one is)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if whole[:mid] in page_text:
            lo = mid
        else:
            hi = mid - 1
    if 12 <= lo < len(whole):
        out.append(f"is shown cut at {lo} characters")
    return out


def expand(value: str | None, f: dict | None = None) -> str:
    """"@long300" is 300 characters of filler. A small model asked to copy a long value repeats
    it until its reply runs out (an unusable reply), so ideas name long values by this code."""
    if value and value.startswith("@file:"):
        return value[6:]  # a file field holds the file's name
    if value and "{" in value:
        from app import test_data
        value = test_data.resolve(value)  # {data:…} typed into the browser only; {email}, {unique}… made fresh
    if value and "@count" in value:
        value = COUNT.sub(lambda m: counterstring(min(int(m.group(1)), 20_000)), value)
    if not value or "@long" not in value:
        return value or ""
    name = f is not None and "name" in f["label"].lower()
    def fill(m):
        n = min(int(m.group(1)), 20_000)
        return (("Abebe Kebede " * (n // 13 + 1)) if name else ("x" * n))[:n]
    return LONG.sub(fill, value)


def is_tried(f: dict, value: str, tried: dict[str, set[str]]) -> bool:
    done = tried.get(f["label"], set())
    return value in done or expand(value, f) in done


MAGNET = "Bug Magnet: "


def magnet_tally(fields: list[dict], tried: dict[str, set[str]], today) -> tuple[int, int]:
    """Bug Magnet values tried, of those offered for these fields."""
    pairs = [(f, v) for f in fields for w, v in ideas_for(f, today) if w.startswith(MAGNET)]
    return sum(1 for f, v in pairs if is_tried(f, v, tried)), len(pairs)


def ideas_text(fields: list[dict], tried: dict[str, set[str]], today, limit_fields: int = 6,
               rules: dict[str, str] | None = None) -> tuple[str, int, int]:
    """The ideas not tried yet for the fields on this page (a few per field), and the tally."""
    lines, total, done = [], 0, 0
    for f in fields[:limit_fields]:
        ideas = ideas_for(f, today, rules)
        main = [(w, v) for w, v in ideas if not w.startswith(MAGNET)]  # Bug Magnet's are extra: counted apart
        total += len(main)
        done += sum(1 for _, v in main if is_tried(f, v, tried))
        left = [(w, v) for w, v in ideas if not is_tried(f, v, tried)]
        if left:
            lines.append(f'- {f["label"]}: ' + "; ".join(f'{w} ("{v[:40]}")' for w, v in left[:3]))
    head = (f"Test ideas not tried yet ({done} of {total} tried), each with the rest of the form valid "
            '("@long300" stands for 300 characters and "@count300" for a 300-character counterstring: send them as '
            'written, Mirmera types them):')
    return (head + "\n" + "\n".join(lines)) if lines else "", done, total


def earlier_runs(db, charter) -> list[str]:
    """What earlier explorations of this charter, or of its story's other charters, found:
    an experienced tester doesn't repeat yesterday's session."""
    ids = {charter.id} | {c.id for s in charter.stories for c in s.charters}
    runs = (db.query(models.ExplorationRun).filter(models.ExplorationRun.charter_id.in_(ids),
                                                   models.ExplorationRun.status != "running")
            .order_by(models.ExplorationRun.created_at.desc()).limit(4).all())
    out = []
    for r in runs:
        bugs = [b.title for b in (db.get(models.Bug, x) for x in r.bug_ids or []) if b]
        when = r.created_at.strftime("%d %b") if r.created_at else "earlier"
        out.append(f"{when}, \"{r.charter.mission_statement[:80]}\": "
                   + (f"found {'; '.join(bugs[:3])}" if bugs else "found no bug")
                   + (f". Summary: {r.summary[:200]}" if r.summary else ""))
    return out


# ---------- pairing: hints from a tester, or from Mirmera ----------

def add_hint(db, run, text: str, *, by: str) -> dict:
    """Adds a hint for the agent's next step (and keeps it with the run)."""
    import uuid
    h = {"id": uuid.uuid4().hex[:8], "text": text[:400], "by": by, "at": utcnow().isoformat(), "step": None}
    run.hints = list(run.hints or []) + [h]
    db.commit()
    return h


def _pending_hints(run_id: str) -> list[dict]:
    with SessionLocal() as db:
        run = db.get(models.ExplorationRun, run_id)
        return [h for h in (run.hints or []) if h.get("step") is None] if run else []


def _hints_taken(run_id: str, ids: set[str], step: int) -> None:
    """The step that read these hints. Re-read first, so a hint sent meanwhile isn't lost."""
    with SessionLocal() as db:
        run = db.get(models.ExplorationRun, run_id)
        run.hints = [({**h, "step": step} if h["id"] in ids else h) for h in run.hints or []]
        db.commit()


def _mirmera_hint(run_id: str, text: str) -> None:
    with SessionLocal() as db:
        run = db.get(models.ExplorationRun, run_id)
        if run and not any(h["text"] == text for h in run.hints or []):
            add_hint(db, run, text, by="Mirmera")


def generalize_hint(before: FormState, title: str) -> str | None:
    """After a bug, the experienced tester's next question: does it happen with the other
    choices too? (The form's select fields, with the options not sent.)"""
    for f in before.fields:
        others = [o for o in f.get("options") or [] if o != f["value"]]
        if f["type"].startswith("select") and others:
            return (f'You just found "{title[:120]}" with {f["label"]} = {f["value"]}. Generalize it: submit the same form '
                    f'with {f["label"]} = ' + " and then ".join(others[:4]) + ", keeping everything else the same, "
                    "and note whether it happens there too.")
    return None


def _user_message(ctx: CharterContext, history: list[dict], observation: str, steps_left: int,
                  hints: list[dict] | None = None, ideas: str = "", tip: tuple[dict, str] | None = None,
                  dirty: dict[str, str] | None = None) -> str:
    lines = [f"{h['n']}. {h['action']} -> {h['status']}{': ' + h['detail'] if h['detail'] else ''}"
             for h in history[-HISTORY_LIMIT:]]
    charter = {"mission": ctx.mission, "test_ideas": ctx.test_ideas, "oracles": ctx.oracles, "risks": ctx.risks}
    if ctx.criteria:
        charter["acceptance_criteria"] = [f"AC{i}: {c}" for i, c in enumerate(ctx.criteria, 1)]
    if ctx.story:
        charter["story"] = ctx.story
    if ctx.earlier:
        charter["earlier_runs"] = ctx.earlier
    if ctx.dismissed:
        charter["dismissed_by_the_team"] = ctx.dismissed
    if ctx.app_map:
        charter["app_map"] = ctx.app_map
    if ctx.signed_in_as:
        charter["signed_in"] = f"Mirmera signed you in as {ctx.signed_in_as}. Test as this user and stay signed in: never sign out."
    if ctx.focus:
        charter["focus"] = (f"The charter is about: {', '.join(ctx.focus)}. Try its ideas first. Other fields are beyond "
                            "the charter: explore them once its ideas are tried, and they are marked as beyond it.")
    return (
        f"<charter>\n{defang(json.dumps(charter, indent=2, ensure_ascii=False))}\n</charter>\n\n"
        f"<history>\n{chr(10).join(lines) or '(no steps yet)'}\n</history>\n\n"
        f"<page>\n{observation}\n</page>\n\n"
        + (f"<ideas>\n{defang(ideas)}\n</ideas>\n\n" if ideas else "")
        + ((f"You changed {', '.join(defang(k) for k in dirty)} since the last submit: nothing is tested until the form is sent.\n"
            if dirty else "")
           + (f"<next>\nA good next step, if you have nothing better: {defang(json.dumps(tip[0], ensure_ascii=False))} "
              f"({defang(tip[1])})\n</next>\n\n" if tip else ""))
        + (("<pair>\n" + "\n".join(f"{h['by']}: {defang(h['text'])}" for h in hints) + "\n</pair>\n"
            "Your pair asks you to try this next: do it now, unless it would leave the app or do something "
            "irreversible (then say so in a note).\n\n") if hints else "")
        + f"Steps left: {steps_left}. Reply with the next single step."
        + (" This is your last step: finish with done." if steps_left == 1 else "")
    )


# ---------- what it leaves in the session ----------

def _note(session_id: str, tag: str, content: str) -> None:
    with SessionLocal() as db:
        db.add(models.Note(session_id=session_id, tag=tag, content=content[:4000]))
        db.commit()


def _record(session_id: str, signals: list[Signal]) -> None:
    from app.routers.runner import store_signals
    with SessionLocal() as db:
        store_signals(db, session_id, signals)


# ---------- checking what the app answered ----------

def sent_text(before: FormState) -> str:
    """The form as it was submitted, in one line (passwords only as "(filled)")."""
    return "; ".join(f'{f["label"][:60]} = {f["value"][:80] or "(empty)"}' for f in before.fields)[:900]


def rules_of(ctx: CharterContext) -> dict[str, str]:
    """The rules an answer is checked against, by id: AC1… (the story) and R1… (the charter's risks)."""
    out = {f"AC{i}": c for i, c in enumerate(ctx.criteria, 1)}
    out |= {r["ref"]: f"{r['title']}" + (f" ({r['failure_mode']})" if r.get("failure_mode") else "") for r in ctx.risks}
    return out


def answer_of(a: schemas.ExploreAction, before: FormState, after: FormState) -> tuple[list[str], str]:
    """What the app answered this step, and how: "now" (something new appeared: a status
    message, a toast, an error under a field, a new page) or "still" (a submit got the same
    message as before). New status messages come first."""
    new_messages = [m for m in after.messages if m not in before.messages]
    if a.kind == "goto":  # a page it opened itself: its text isn't an answer to anything
        return new_messages[:6], "now"
    old = set(before.lines) | set(new_messages)
    new_lines = [l for l in after.lines if l not in old and not any(l in m for m in new_messages)]
    said = (new_messages + new_lines)[:6]
    if said:
        return said, "now"
    if a.kind in SUBMITS and after.messages:
        return after.messages[:6], "still"
    return [], ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}


def quoted(quote: str, said: list[str]) -> bool:
    """Is the AI's quote the app's words? Word for word, or nearly: a small model trims
    punctuation or a word or two, but a made-up sentence shares few words with the answer."""
    if not quote.strip():
        return False
    answer = " ".join(said)
    if _norm(quote) in _norm(answer):
        return True
    q = _words(quote)
    return bool(q) and len(q & _words(answer)) / len(q) >= 0.75


# ---------- oracles beyond the written rules ----------

GLITCH = re.compile(r"\bundefined\b|\bNaN\b|\[object Object\]|\{\{[^}]*\}\}|Traceback \(most recent call last\)"
                    r"|\b[a-z]+(?:_[a-z0-9]+){2,}\b")
SUCCESS_WORDS = ("verified", "welcome", "success", "saved", "thank you", "thanks", "created", "confirmed", "done",
                 "placed", "added", "updated", "submitted", "registered", "booked", "received", "signed up",
                 "subscribed", "joined", "completed", "ordered", "paid", "uploaded", "accepted")
REFUSAL_WORDS = ("please", "required", "must", "invalid", "not allowed", "only", "can't", "cannot", "not accepted", "rejected",
                 "not available", "unavailable", "sorry", "isn't possible", "not possible", "declined", "not valid",
                 "already used", "already exists", "already registered", "already taken", "enter a valid",
                 "is not a valid")
LISTING_WORDS = ("list", "lists", "listed", "show", "shows", "shown", "display", "displays", "appear", "appears")


def outcome_of(said: list[str], broke: list) -> str:
    """accepted | refused | error | other, from what the app said (no AI): for comparing tries."""
    if broke:
        return "error"
    text = _norm(" ".join(said))
    if any(w in text for w in ("went wrong", "error", "failed", "try again later")):
        return "error"
    if any(w in text for w in REFUSAL_WORDS):
        return "refused"
    if any(w in text for w in SUCCESS_WORDS):
        return "accepted"
    return "other"


def glitches(lines: list[str]) -> list[str]:
    """Text no user should see: undefined, NaN, [object Object], {{template}}, a stack trace,
    an internal code like mrz_parser_unavailable."""
    out = []
    for line in lines:
        m = GLITCH.search(line)
        if m and line not in out:
            out.append(line)
    return out[:3]


def one_choice_changed(a: dict, b: dict, fields: list[dict]) -> tuple[str, str, str] | None:
    """(field, value before, value now) when two submits differ only in one choice (a select)."""
    diff = [k for k in set(a) | set(b) if a.get(k) != b.get(k)]
    if len(diff) != 1:
        return None
    kind = next((f["type"] for f in fields if f["label"] == diff[0]), "")
    return (diff[0], a.get(diff[0], ""), b.get(diff[0], "")) if kind.startswith("select") else None


def place_rule(rules: dict[str, str], after: FormState) -> tuple[str, str] | None:
    """The rule that names this page as where something is listed or shown, e.g. AC6:
    "After verification, My documents lists every verified document" on a page titled
    "My documents"."""
    names = {_norm(x.split("·")[0].split("|")[0]) for x in (after.heading, after.title) if x}
    for rid, text in rules.items():
        t = _norm(text)
        if any(w in t.split() for w in LISTING_WORDS) and any(len(n) >= 6 and n in t for n in names):
            return rid, text
    return None


def listing_place(links: list[dict], rules: dict[str, str]) -> tuple[dict | None, tuple[str, str] | None]:
    """Where submitted things are listed: a link a rule names next to "list" or "show" (the longest,
    "My orders" not "Order"), else a "My …" link. (link, the rule) or (None, None)."""
    named = []  # (link, rule): a link a rule names as whole words, in a rule about listing or showing
    for rid, text in rules.items():
        t = f" {_norm(text)} "
        if any(w in t.split() for w in LISTING_WORDS):
            named += [(l, (rid, text)) for l in links if len(l["text"]) >= 4
                      and re.search(rf"(?<!\w){re.escape(_norm(l['text']))}(?!\w)", t)]
    if named:  # a rule that says "list" first, then the longest link
        return max(named, key=lambda x: (bool(re.search(r"\blist", x[1][1].lower())), len(x[0]["text"])))
    return next((l for l in links if l["text"].lower().startswith("my ")), None), None


def kept_values(before: FormState) -> list[str]:
    """What a submit sent that should be findable later: text values, not secrets or choices."""
    return [f["value"] for f in before.fields
            if f["type"] in ("text", "email", "tel", "number", "search", "textarea") and len(f["value"]) >= 4][:4]


def crashes(failed: list[str], page_errors: list[str], requests: list[dict] | None = None,
            secrets: list[str] | None = None) -> list[schemas.ExploreProblem]:
    """Bug drafts for what broke during a step: a server error (5xx) or an uncaught error in
    the page. Wrong whatever the rules say, so no AI is asked. With the requests' details, a
    server error comes with the curl command that sends it again."""
    from urllib.parse import urlsplit
    from app.curl import curl_of
    out = []
    for f in failed:
        m = re.match(r"(\w+) (\S+) -> (5\d\d)$", f)
        if m:
            method, url, status = m.groups()
            path = urlsplit(url).path or "/"
            req = next((r for r in reversed(requests or []) if r["method"] == method and r["url"] == url
                        and str(r["status"]) == status), None)
            out.append(schemas.ExploreProblem(
                title=f"{method} {path} fails with {status}", severity="high",
                expected="The app handles the request and answers it (accepted or refused with a reason); a server error is never the answer.",
                actual=f"{method} {path} answered {status}.",
                symptom=schemas.Symptom(kind="failed_request", value=path,
                                        explanation="A server error, seen by Mirmera in the browser's network log."),
                reproduce=curl_of(method, url, req["headers"], req["body"], secrets) if req else None))
    for e in page_errors:
        first = e.strip().splitlines()[0][:150] if e.strip() else "error"
        out.append(schemas.ExploreProblem(
            title=f"Page error: {first}"[:200], severity="medium",
            expected="The page runs without uncaught errors.", actual=f"The page threw: {first}.",
            symptom=schemas.Symptom(kind="page_error", value=first[:120],
                                    explanation="An uncaught error in the page, seen by Mirmera.")))
    return out


def exif_leaks(leaks: list[dict], origins: set[str]) -> list[schemas.ExploreProblem]:
    """A bug draft when the app served an image that still says where it was taken (EXIF GPS).
    No rule needed: a customer's photo must never give away their location. One draft per run."""
    from urllib.parse import urlsplit
    from app.agent.exif import place
    mine = [g for g in leaks if in_app(g["url"], origins)]
    if not mine:
        return []
    g = next((x for x in mine if x["probe"]), mine[0])
    path = urlsplit(g["url"]).path or "/"
    whose = ("the location and camera Mirmera put in the photo it uploaded, so the upload is stored and served untouched"
             if g["probe"] else "a location Mirmera didn't put there: someone's photo, served with where it was taken")
    return [schemas.ExploreProblem(
        title="Uploaded photo is served back with its GPS location (EXIF)" if g["probe"]
        else "A photo is served with a GPS location in it (EXIF)", severity="high",
        expected="Photos are stripped of their EXIF data (GPS location, camera) when uploaded, or at least before "
                 "they are shown again: a customer's photo must not tell anyone where they live.",
        actual=f"{path} was served with GPS {place(g)}" + (f" and camera \"{g['camera']}\"" if g["camera"] else "")
               + f": {whose}.",
        symptom=schemas.Symptom(kind="privacy", value="gps",
                                explanation="Mirmera read the EXIF of every image the app sent to the browser."),
        same_as="exif-gps")]


async def _sign_out_check(page, signals, history: list, session_id: str, run_id: str, noted: set) -> None:
    """At the end of a run, Mirmera clicks the app's sign-out (when the page has one) and reads what the
    browser still holds: a customer's details or session left behind for the next person on that phone."""
    from app.agent import storage
    from app.sign_in import SIGN_OUT
    control = page.get_by_role("button", name=SIGN_OUT).or_(page.get_by_role("link", name=SIGN_OUT)).first
    if not await control.count() or not await control.is_visible():
        return
    name = re.sub(r"\s+", " ", (await control.inner_text(timeout=2_000)).strip())[:40] or "Sign out"
    before = await storage.snapshot(page)
    await control.click(timeout=5_000)
    await signals.settle(timeout_ms=5_000)
    after = await storage.snapshot(page)
    left = storage.after_sign_out(before, after)
    history.append({"n": len(history) + 1, "thought": "Mirmera signs out at the end of the run and reads what the app "
                    "left in the browser (local storage, session storage, cookies).",
                    "action": f'click "{name}"', "kind": "signout", "status": "ok", "by": "Mirmera", "url": page.url,
                    "detail": f"left behind: {len(left)}" if left else "nothing of the session or the customer's details left behind",
                    "storage": storage.changes(before, after)})
    _save(run_id, steps=list(history))
    for c in left:
        if c["key"] not in noted:
            noted.add(c["key"])
            _note(session_id, "issue", f"Curio: {c['what']} (seen on {urlsplit_path(page.url)}). "
                                       "On a shared phone, the next person finds it: worth a look.")


def display_mismatches(found: list[dict]) -> list[schemas.ExploreProblem]:
    """Bug drafts for values the page shows that its own JSON contradicts (app/agent/datacheck.py)."""
    from urllib.parse import urlsplit
    out = []
    for m in found:
        path = urlsplit(m["url"]).path or "/"
        status = "the data says" in m["why"]
        out.append(schemas.ExploreProblem(
            title=f"{m['label']} shown differs from the app's own data", severity="high" if status else "medium",
            expected=f"The page shows {m['label']} as the server sent it: {m['key']} = {json.dumps(m['value'], ensure_ascii=False)}.",
            actual=f"The page shows \"{m['label']}: {m['shown']}\"; {m['method']} {path} answered "
                   f"{m['key']}: {json.dumps(m['value'], ensure_ascii=False)} ({m['why']}).",
            symptom=schemas.Symptom(kind="mismatch", value=m["label"],
                                    explanation="Mirmera compared what the page shows with the JSON it was drawn from."),
            same_as=f"mismatch:{m['label'].lower()}"))
    return out


GAP_WORDS = ("required", "missing", "empty", "please enter", "please fill", "please provide", "can't be blank",
             "cannot be blank", "is mandatory")
LABEL_NOISE = {"the", "your", "and", "for", "with", "enter", "create", "accept", "date", "number"}


def refusal_about_a_gap(before: FormState, said: list[str], tested: str | None = None) -> bool:
    """Is a refusal about a field left empty or a box left unticked, rather than about what
    the rule is about? Yes if the answer names one of those fields, or says something is
    required or missing. "Name must use letters only." with the phone left empty is not.
    `tested`: the field left empty on purpose (an optional one), which is the test, not a gap."""
    gaps = [f for f in before.fields if f["type"] != "radio" and f["value"] in ("", "not checked") and f["label"] != tested]
    if not gaps:
        return False
    answer = _norm(" ".join(said))
    if any(w in answer for w in GAP_WORDS):
        return True
    words = set(re.findall(r"\w+", answer))
    return any(w in words for f in gaps for w in re.findall(r"\w+", f["label"].lower()) if len(w) > 2 and w not in LABEL_NOISE)


ETHIOPIC = re.compile(r"[\u1200-\u137F]")
NEGATIVE_RULE = re.compile(r"\b(reject(?:s|ed)?|refuse[sd]?|not allowed|not accepted|must not|cannot|can't|never|blocked|denied|declined)\b", re.I)


NUM = r"(-?\d{1,7}(?:\.\d+)?)"
RANGE_RE = re.compile(rf"\b(?:between\s+|from\s+)?{NUM}\s*(?:to|and|through|–)\s*{NUM}\b(.{{0,20}})")
MAX_RE = re.compile(rf"\b(?:at most|maximum(?: of)?|max\.?|up to|no more than|not more than|less than or equal to)\s*{NUM}\b(.{{0,20}})")
MIN_RE = re.compile(rf"\b(?:at least|minimum(?: of)?|min\.?|no less than|not less than)\s*{NUM}\b(.{{0,20}})")
OPTIONAL_RE = re.compile(r"\b(optional|not required|not mandatory|(?:may|can) be (?:left )?(?:empty|blank))\b")
REQUIRED_RE = re.compile(r"\b(required|mandatory|must be (?:given|entered|filled(?: in)?|provided))\b")
EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
LENGTH_UNIT = re.compile(r"^\s*(?:characters?|chars?|letters?|digits?|symbols?)\b")


def field_rules(f: dict, rules: dict[str, str]) -> list[dict]:
    """What the rules say about one field, read without AI: optional or required, a number range,
    a length limit, a valid email or phone. Only rules that name the field count."""
    def stems(text: str) -> set[str]:  # content words of 3+ letters, plural or not ("Age" counts)
        return {w[:-1] if w.endswith("s") and len(w) > 4 else w for w in re.findall(r"\w+", text.lower()) if len(w) > 2}
    words = {w for w in stems(f["label"]) if w not in LABEL_NOISE}
    out = []
    for rid, text in rules.items():
        t = text.lower()
        if not words or not (words & stems(t)):
            continue
        base = {"rid": rid, "text": text}
        if OPTIONAL_RE.search(t):
            out.append({**base, "kind": "optional"})
        elif REQUIRED_RE.search(t):
            out.append({**base, "kind": "required"})
        if f["type"] == "file":  # "up to 5 MB" is a file size, checked by file_cases, not a number range
            continue
        for m in RANGE_RE.finditer(t):
            lo, hi = sorted((float(m.group(1)), float(m.group(2))))
            out.append({**base, "kind": "length" if LENGTH_UNIT.match(m.group(3)) else "range", "lo": lo, "hi": hi})
        for m in MAX_RE.finditer(t):
            out.append({**base, "kind": "length" if LENGTH_UNIT.match(m.group(2)) else "range", "lo": None, "hi": float(m.group(1))})
        for m in MIN_RE.finditer(t):
            out.append({**base, "kind": "length" if LENGTH_UNIT.match(m.group(2)) else "range", "lo": float(m.group(1)), "hi": None})
        label = f["label"].lower()
        if ("email" in t and (f["type"] == "email" or "mail" in label)) and re.search(r"\bvalid|\bformat|\baddress", t):
            out.append({**base, "kind": "email"})
        if ("phone" in t and (f["type"] == "tel" or "phone" in label or "mobile" in label)) and re.search(r"\bvalid|\bformat|\bdigits", t):
            out.append({**base, "kind": "phone"})
    return out


def _bounds(c: dict) -> str:
    n = lambda x: f"{x:g}"  # noqa: E731
    if c["lo"] is not None and c["hi"] is not None:
        return f"{n(c['lo'])} to {n(c['hi'])}"
    return f"at most {n(c['hi'])}" if c["hi"] is not None else f"at least {n(c['lo'])}"


def broken_by(c: dict, f: dict, v: str) -> tuple[str, str] | None:
    """(accept | refuse, what kind of value) when this rule says what the app must do with v."""
    empty = not (v or "").strip()
    k = c["kind"]
    if k == "optional" and empty:
        return "accept", f'{f["label"]} left empty'
    if k == "required" and empty:
        return "refuse", f'{f["label"]} left empty'
    if k == "range" and not empty:
        try:
            x = float(v.strip())
        except ValueError:
            return None
        if (c["lo"] is not None and x < c["lo"]) or (c["hi"] is not None and x > c["hi"]):
            return "refuse", f'{f["label"]} {v.strip()}, outside {_bounds(c)}'
    if k == "length" and not empty:
        n = len(v)
        if (c["lo"] is not None and n < c["lo"]) or (c["hi"] is not None and n > c["hi"]):
            return "refuse", f'{f["label"]} of {n} characters, outside {_bounds(c)}'
    if k == "email" and not empty and not EMAIL_OK.match(v.strip()):
        return "refuse", f'an email that isn\'t valid, "{v[:40]}",'
    if k == "phone" and not empty and (re.search(r"[A-Za-z]", v) or len(re.sub(r"\D", "", v)) < 7):
        return "refuse", f'a phone number that isn\'t valid, "{v[:40]}",'
    return None


def boundary_ideas(f: dict, rules: dict[str, str]) -> list[tuple[str, str]]:
    """The values at and just past each limit the rules give for this field: boundary values,
    the first thing an experienced tester tries."""
    out = []
    for c in field_rules(f, rules):
        if c["kind"] == "range":
            n = lambda x: f"{x:g}"  # noqa: E731
            if c["lo"] is not None:
                out += [(f"just under the minimum {n(c['lo'])}", n(c["lo"] - 1)), (f"the minimum {n(c['lo'])}", n(c["lo"]))]
            if c["hi"] is not None:
                out += [(f"the maximum {n(c['hi'])}", n(c["hi"])), (f"just over the maximum {n(c['hi'])}", n(c["hi"] + 1))]
        elif c["kind"] == "length" and c["hi"] is not None:
            out += [(f"{c['hi']:g} characters, the limit", f"@long{int(c['hi'])}"),
                    (f"{c['hi'] + 1:g} characters, one over", f"@long{int(c['hi']) + 1}")]
        elif c["kind"] == "optional":
            out.append(("left empty (the rules say optional)", ""))
        elif c["kind"] == "required":
            out.append(("left empty (the rules say required)", ""))
    seen, uniq = set(), []
    for w, v in out:
        if v not in seen:
            seen.add(v)
            uniq.append((w, v))
    return uniq


FILE_TYPES = re.compile(r"\b(jpe?g|png|pdf|heic|gif|webp|svg)\b")
MB = re.compile(r"(\d+(?:\.\d+)?)\s*(mb|megabytes?)\b")


def file_cases(rules: dict[str, str], f: dict) -> list[tuple]:
    """A rule about a file field ("JPG or PNG, up to 5 MB") and one of Mirmera's files that breaks it:
    empty, not what its name says, a type the rule doesn't name, or too big. (Refusals only.)"""
    from app.agent import files
    name = f["value"]
    if name not in files.FILES:
        return []
    words = {w for w in re.findall(r"\w+", f["label"].lower()) if len(w) > 2 and w not in LABEL_NOISE}
    out = []
    for rid, text in rules.items():
        t = text.lower()
        types = {x.replace("jpeg", "jpg") for x in FILE_TYPES.findall(t)}
        mb = MB.search(t)
        if not (words & set(re.findall(r"\w+", t))) or not (types or mb or re.search(r"\b(upload|photo|image|file|scan)\b", t)):
            continue
        real, ext = files.FILES[name]["real"], name.rsplit(".", 1)[-1].lower().replace("jpeg", "jpg")
        if real == "empty":
            why = "an empty file (0 bytes)"
        elif real in ("exe", "txt"):
            why = files.FILES[name]["what"]
        elif types and ext not in types:
            why = f"a .{ext} file, not {' or '.join(sorted(x.upper() for x in types))}"
        elif mb and files.size_mb(name) > float(mb.group(1)):
            why = f"a {files.size_mb(name):.0f} MB file, over {mb.group(1)} MB"
        else:
            continue
        out.append((rid, text, "refuse", f'{f["label"]} = "{name}"', why, f["label"]))
        break
    return out


def rule_cases(rules: dict[str, str], sent: FormState, today) -> list[tuple[str, str, str, str, str, str]]:
    """The rules that speak about a kind of value in the form as sent, and what they expect:
    (rule id, rule, accept | refuse, field = value, what kind of value, field label). A rule naming
    Ge'ez script and a name in Ge'ez letters, an expired document and a past expiry date, an
    optional field left empty, a quantity outside its range, an email that isn't valid."""
    out = []
    for f in sent.fields:
        if f["type"] == "file":
            out += file_cases(rules, f)
            continue
        if f["type"] in ("checkbox", "radio", "password"):
            continue
        for c in field_rules(f, rules):
            if hit := broken_by(c, f, f["value"]):
                out.append((c["rid"], c["text"], hit[0], f'{f["label"]} = "{f["value"][:60]}"', hit[1], f["label"]))
        v, label = f["value"], f["label"].lower()
        kinds = []
        if ETHIOPIC.search(v):
            kinds.append(("a name in Ge'ez script", ("ge'ez", "ge’ez", "geez", "ethiopic", "amharic", "fidel")))
        if "name" in label and re.search(r"[A-Za-z]['’][A-Za-z]|[A-Za-z]-[A-Za-z]", v):
            kinds.append(("a name with an apostrophe or hyphen", ("apostroph", "hyphen")))
        if re.search(r"expir", label) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or "") and v < today.isoformat():
            kinds.append(("an expiry date in the past", ("expired", "expiry", "expire")))
        elif (f["type"] == "date" or "date" in label) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v or "") \
                and v < today.isoformat():
            past = past_rule(rules, f["label"])
            if past:
                out.append((past[0], past[1], "refuse", f'{f["label"]} = "{v}"',
                            f'a {f["label"].lower()} in the past ({v}, the user\'s today is {today.isoformat()})', f["label"]))
        for what, words in kinds:
            for rid, text in rules.items():
                if any(w in text.lower() for w in words):
                    out.append((rid, text, "refuse" if NEGATIVE_RULE.search(text) else "accept",
                                f'{f["label"]} = "{v[:60]}"', what, f["label"]))
                    break
    return out


def checked_by_mirmera(rules: dict[str, str], sent: FormState) -> set[str]:
    """The rules Mirmera reads itself for what this form sends (see rule_cases): an expiry rule
    when an expiry date was sent, a no-past-dates rule for its date, a Ge'ez or apostrophe rule
    when such a name was sent. For these, the AI's verdict is only a note: a small model once
    called a future expiry date "expired" and so hid the real bug a step later."""
    ids: set[str] = set()
    for f in sent.fields:
        v, label = f["value"] or "", f["label"].lower()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and not ETHIOPIC.search(v) and "name" not in label:
            continue
        for rid, text in rules.items():
            t = text.lower()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) and (
                    (re.search(r"expir", label) and re.search(r"expir", t)) or past_rule({rid: text}, f["label"])):
                ids.add(rid)
            if ETHIOPIC.search(v) and any(w in t for w in ("ge'ez", "ge’ez", "geez", "ethiopic", "amharic", "fidel")):
                ids.add(rid)
            if "name" in label and re.search(r"[A-Za-z]['’][A-Za-z]|[A-Za-z]-[A-Za-z]", v) and re.search(r"apostroph|hyphen", t):
                ids.add(rid)
    return ids


def own_verdict(rule_id: str, rules: dict[str, str], sent: FormState, said: list[str], did: str, today) -> tuple[str, str] | None:
    """Mirmera's verdict on a rule it checks itself, or None when it doesn't check that rule for
    this form (then the AI's facts decide, as before)."""
    rid = next((k for k in rules if k.upper() == rule_id.upper()), None)
    if not rid or rid not in checked_by_mirmera(rules, sent):
        return None
    cases = [c for c in rule_cases(rules, sent, today) if c[0] == rid]
    if not cases:
        return "consistent", f"Mirmera's own check: nothing sent falls under {rid}"
    _, _, expects, first, kind, tested = cases[0]
    if (did == "accepted" and expects == "refuse") or (did == "refused" and expects == "accept"
                                                       and not refusal_about_a_gap(sent, said, tested)):
        return "breaks", f"Mirmera's own check: {kind} is {did}, but {rid} says it must be {'refused' if expects == 'refuse' else 'accepted'}"
    if did in ("accepted", "refused"):
        return "consistent", f"Mirmera's own check: {kind}, {did}, as {rid} expects"
    return None


PAST_RULE = re.compile(r"\b(today or later|today or after|not (?:be )?in the past|can(?:not|'t|’t) be in the past|"
                       r"no past dates?|past dates? (?:are|is) (?:refused|not allowed|not accepted|rejected)|"
                       r"must be (?:in the future|after today|a future date)|from today on)\b", re.I)


def past_rule(rules: dict[str, str], label: str) -> tuple[str, str] | None:
    """The rule that says this date can't be in the past ("The delivery date is today or later")."""
    words = {w for w in re.findall(r"\w+", label.lower()) if len(w) > 2 and w not in LABEL_NOISE and w != "date"} or {"date"}
    for rid, text in rules.items():
        t = text.lower()
        if PAST_RULE.search(t) and words & set(re.findall(r"\w+", t)):
            return rid, text
    return None


def decide(j: schemas.LLMExploreJudgement, rules: dict[str, str], before: FormState, said: list[str]) -> tuple[str, str]:
    """breaks | consistent | unclear, and why. The AI only reports facts (what the app did,
    what the rule expects); the verdict is made here, so a small model can't talk itself into a bug."""
    if not quoted(j.answer_quote, said):
        return "unclear", "the words it quoted aren't in the app's answer"
    if not j.rule or j.rule.strip().upper() not in {k.upper() for k in rules} or j.rule_expects == "none":
        return "unclear", "no rule says what should happen here"
    expects, did = j.rule_expects, j.answer_means
    if did == "accepted" and expects == "refuse":
        return "breaks", "accepted, but the rule says it must be refused"
    if did == "error" and expects == "accept":
        return "breaks", "it broke, but the rule says it must be accepted"
    if did == "refused" and expects == "accept":
        if refusal_about_a_gap(before, said):
            return "unclear", "refused because a field was empty or unticked, not because of the rule"
        return "breaks", "refused, but the rule says it must be accepted"
    if (did == "accepted" and expects == "accept") or (did == "refused" and expects == "refuse"):
        return "consistent", "as the rule expects"
    return "unclear", "the answer doesn't say clearly what happened"


async def judge(provider: LLMProvider, ctx: CharterContext, before: FormState, said: list[str], url: str
                ) -> schemas.LLMExploreJudgement | None:
    """The facts about one answer: what the app did, which rule is about what was sent, and
    what that rule expects. A separate, focused question: a small model that steers the
    browser often walks past a wrong answer."""
    rules = rules_of(ctx)
    moved = f"Went to: {defang(url)}\n" if before.url and url and url.split("#")[0] != before.url.split("#")[0] else ""
    user = (f"Today: {run_today().isoformat()}\n\n<rules>\n"
            + defang("\n".join(f"{k}: {v}" for k, v in rules.items())) + "\n</rules>\n\n"
            f"<sent>\n{defang(sent_text(before)) or '(no form)'}\n</sent>\n\n"
            f"<answer>\n{moved}" + "\n".join(f"Says: {defang(m[:300])}" for m in said) + "\n</answer>")
    try:
        return await complete_json(provider, JUDGE_PROMPT, user, schemas.LLMExploreJudgement, task="explore_judge", max_tokens=500)
    except (LLMOutputError, ProviderError) as err:
        logger.info("Couldn't check an answer: %s", _first_line(err))
        return None


def signature(p: schemas.ExploreProblem) -> str:
    """The problem as a key, so the same one met again isn't drafted twice: a failing request
    by its path, a page error by its message, a wrong answer by the rule it breaks and the
    words on the page (the same "Order placed" can break two different rules)."""
    if p.same_as:
        return p.same_as
    sym = p.symptom
    value = _norm(sym.value)[:200]
    if sym.kind in ("text_visible", "text_absent"):
        return f"{sym.kind}:{_norm(sym.explanation or p.title)[:200]}:{value}"
    return f"{sym.kind}:{value}"


SEEN_AGAIN = "seen again"


def first_step(fault: str) -> str:
    """What has to happen before the steps for a bug Mirmera made happen on purpose."""
    if fault.startswith(("open ", "sign in")):
        return fault[:1].upper() + fault[1:]
    if fault.startswith(("Sign out ", "Open ")):  # already a step a person does (the interruptions tour)
        return fault
    return f"Make {fault} (e.g. stop the API, or block the request in the browser's dev tools)"


FIELD_ACTIONS = ("fill", "select", "check", "uncheck")


def charter_target(mission: str) -> str:
    """The charter's target: "the expiry date" in "Explore the expiry date with … to discover …"."""
    m = re.match(r"\s*explore\s+(.*?)(?:\s+with\s+|\s+to discover\s+|\.?\s*$)", mission, re.I | re.S)
    return (m.group(1) if m else mission).strip()


def _focus_stems(text: str) -> set[str]:
    """Content words of a label or target, without words every field shares ("date", "number")."""
    return {w for w in _stems(text) if w not in LABEL_NOISE}


def focus_fields(mission: str, fields: list[dict]) -> list[str]:
    """The form fields the charter is about: those named in its target ("Expiry date" for "the expiry
    date"). Only the target counts: "…to discover whether expired documents are rejected" doesn't
    make "Document type" part of it."""
    target = _focus_stems(charter_target(mission))
    return [f["label"] for f in fields if f["type"] not in ("hidden",) and _focus_stems(f["label"]) & target]


def beyond_charter(changed: list[str], focus: list[str]) -> bool:
    """A step that changed only fields the charter isn't about. Unknown focus: never beyond."""
    if not changed or not focus:
        return False
    on = set().union(*(_focus_stems(f) for f in focus))
    return not any(_focus_stems(c) & on for c in changed)


def shortest(steps: list[dict]) -> list[dict]:
    """A draft's steps, short enough to replay. A long run's journey is every fill and click since it
    began (a Ge'ez refusal at step 24 had more than the 60 steps a replay takes, so it was drafted
    with none, and couldn't be confirmed). Too long, it becomes: the last page opened, each field
    set to its last value, in order, and the action that showed the problem. Short journeys stay
    as they are: the order of earlier submits can matter."""
    from app.regression.steps import MAX_EDITED_STEPS
    if len(steps) <= MAX_EDITED_STEPS:
        return steps
    last_goto = max((i for i, s in enumerate(steps) if s.get("type") == "goto"), default=-1)
    head, rest = (steps[:1], steps) if last_goto < 0 else ([steps[last_goto]], steps[last_goto + 1:])
    if not rest:
        return head
    *before, final = rest
    fields: dict[str, dict] = {}
    for s in before:
        act = s.get("action") or {}
        if s.get("type") == "action" and act.get("action") in FIELD_ACTIONS:
            key = json.dumps(act.get("locator", {}), sort_keys=True)
            fields.pop(key, None)  # keep the field where it was last set
            fields[key] = s
    out = head + list(fields.values()) + [final]
    return out[-MAX_EDITED_STEPS:] if len(out) > MAX_EDITED_STEPS else out


def _draft_bug(session_id: str, charter_id: str, p: schemas.ExploreProblem, evidence: str, steps: list[dict],
               page_url: str, screenshot: Path | None, provider: LLMProvider) -> tuple[str, bool]:
    """(bug id, new?): a bug draft, or, when the project already has a draft of the same problem, a sighting
    added to it: the tester reviews each problem once, and sees everywhere it showed up."""
    key = signature(p)
    before = ""
    with SessionLocal() as db:
        session = db.get(models.Session, session_id)
        project_id = session.charter.project_id if session and session.charter else None
        if project_id:  # a tester dismissed this problem before: not drafted again (see app/dismissals.py)
            from app import dismissals
            from app.trackers.fingerprint import fingerprint_of
            d = dismissals.for_problem(db, project_id, fingerprint_of(key, p.symptom.model_dump(), p.title))
            if d and d.reason not in dismissals.STILL_DRAFTED:
                db.add(models.Note(session_id=session_id, tag="test",
                                   content=f"Not drafted again: {p.title} ({dismissals.describe(d)})."[:4000]))
                db.commit()
                return None, False
            if d:
                before = f"\n\nSeen again after it was {dismissals.describe(d)}: it may be intermittent."
        same = (db.query(models.Bug).join(models.Charter, models.Bug.charter_id == models.Charter.id)
                .filter(models.Charter.project_id == project_id, models.Bug.signature == key)
                .order_by(models.Bug.created_at).first()) if project_id else None
        if same:
            charter = db.get(models.Charter, charter_id)
            same.sightings = list(same.sightings or []) + [{
                "session_id": session_id, "charter_id": charter_id, "at": utcnow().isoformat(), "url": page_url,
                "mission": (charter.mission_statement if charter else "")[:200]}]
            if screenshot and screenshot.is_file():
                db.add(models.Evidence(session_id=session_id, bug_id=same.id, kind="screenshot",
                                       summary=f"Seen again: {p.title}"[:300], url=page_url, file_path=str(screenshot)))
            if p.reproduce and not same.reproduce:  # drafted before Mirmera kept the request: keep it now
                same.reproduce = p.reproduce
            if (same.dismissal or {}).get("reason") == "cant_reproduce":  # it showed again: reproducible after all
                for d in db.query(models.Dismissal).filter(models.Dismissal.bug_id == same.id).all():
                    db.delete(d)
                same.dismissal = None
                same.last_change = "Seen again after it was dismissed as can't reproduce: a draft again"
            db.add(models.Note(session_id=session_id, tag="bug",
                               content=f"Seen again: {same.title} (already drafted, so added to that bug)"[:4000]))
            db.commit()
            return same.id, False
    try:
        edited = clean_steps(shortest(steps))
    except StepError as err:  # drafted without steps to replay: say why, so it isn't a silent gap
        logger.info("A draft's steps couldn't be kept: %s", err)
        edited = None
    with SessionLocal() as db:
        trail = [{"tag": n.tag, "content": n.content, "timestamp": n.timestamp.isoformat()}
                 for n in db.query(models.Note).filter_by(session_id=session_id).order_by(models.Note.timestamp).all()][-6:]
        bug = models.Bug(
            session_id=session_id, charter_id=charter_id, title=p.title[:200], severity=p.severity,
            description=(f"{p.actual}\n\nExpected: {p.expected}\n\nWhat Mirmera checked: {evidence}\n\n"
                         + f"Found by the Mirmera exploration agent ({provider.name}/{provider.model}). "
                         "A draft: review it before filing." + before),
            expected=p.expected, actual=p.actual,
            steps_to_reproduce=([clock_line()] if CLOCK_AT.get() else []) + ([first_step(p.fault)] if p.fault else []) + [s["action"]["summary"] if s["type"] == "action"
                                                       else f"open {s['url']}" for s in (edited or [])],
            reproduction_trail=trail, edited_steps=edited,
            environment={"browser": f"{_engine_label()} (headless)", "url": page_url, "found_by": TESTER, "judged_by": p.found_by,
                         **({"engine": ENGINE.get()} if ENGINE.get() != "chromium" else {}),
                         **({"injected_failure": p.fault} if p.fault else {}),
                         **({"clock": clock_line().removeprefix("Set the computer's clock to "),
                             "clock_at": CLOCK_AT.get().isoformat(), "timezone": str(CLOCK_AT.get().tzinfo)}
                            if CLOCK_AT.get() else {})},
            signature=key, sightings=[], symptom=p.symptom.model_dump(), reproduce=p.reproduce,
        )
        db.add(bug)
        db.flush()
        if screenshot and screenshot.is_file():
            db.add(models.Evidence(session_id=session_id, bug_id=bug.id, kind="screenshot",
                                   summary=f"What the agent saw: {p.title}"[:300], url=page_url, file_path=str(screenshot)))
        db.add(models.Note(session_id=session_id, tag="bug", content=f"{p.title}: {p.actual}"[:4000]))
        db.commit()
        return bug.id, True


def _seen(entry: dict, new: bool) -> None:
    if not new:
        entry["seen_again"] = True
        entry["detail"] = (entry.get("detail") or "") + " (seen before: added to that draft)"


# ---------- flow tours: the ways users really move through a flow ----------

def _marker() -> str:
    import random
    return "Tour" + "".join(random.choice("bcdfghjklmnpqrstvwxz") for _ in range(5)).capitalize()


async def flow_tour(page, context, signals, form_url: str, answer_url: str, sent: FormState, to_here: list[dict],
                    rules: dict[str, str], keep, *, sign_in_cfg: dict | None = None, base: str | None = None) -> str:
    """After an accepted submit, what a tester does next: open the answer page by its address
    (a bookmark, a shared link), refresh it, go back, and send the same form twice quickly. A
    server error or a page error on the way is a draft; so is one double click listed twice.
    keep(problem, key, (page, signals, steps)) drafts a checked bug. Returns what it did."""
    from urllib.parse import urlsplit
    from app.regression.replay import Signals
    done: list[str] = []
    found = 0

    async def check(pg, sigs, f0: int, e0: int, how: str, rec: list[dict]) -> None:
        nonlocal found
        for c in crashes(sigs.failed[f0:], sigs.page_errors[e0:], sigs.failed_requests):
            c.actual = f"{how}: {c.actual}"[:600]
            if await keep(c, (c.symptom.kind, c.symptom.value), (pg, sigs, rec)):
                found += 1

    answer_path = urlsplit(answer_url).path or "/"
    # 1. The answer page opened by its address, in a new tab.
    if answer_url.split("#")[0] != form_url.split("#")[0]:
        tab = await context.new_page()
        tab_signals = Signals()
        tab_signals.attach(tab)
        try:
            await tab.goto(answer_url, wait_until="domcontentloaded", timeout=30_000)
            await tab_signals.settle()
            await check(tab, tab_signals, 0, 0, f"Opening {answer_path} directly (a bookmark or a shared link)",
                        [{"type": "goto", "url": answer_url}])
        finally:
            await tab.close()
        done.append(f"opened {answer_path} directly")
    # 2. Refresh, 3. Back.
    f0, e0 = len(signals.failed), len(signals.page_errors)
    await page.reload(wait_until="domcontentloaded", timeout=30_000)
    await signals.settle()
    await check(page, signals, f0, e0, f"Refreshing {answer_path} after the submit", to_here + [{"type": "goto", "url": answer_url}])
    done.append("refreshed")
    f0, e0 = len(signals.failed), len(signals.page_errors)
    try:
        await page.go_back(wait_until="domcontentloaded", timeout=10_000)
        await signals.settle()
        await check(page, signals, f0, e0, f"Going back from {answer_path}", to_here)
        done.append("went back")
    except Exception:
        pass
    # 4. The same form, sent with one double click. A text field carries a marker, so an entry
    #    listed twice can be counted. A double click is a race: listed once, it is tried once more.
    place, rule, listed = None, None, 0
    for attempt in range(2):
        await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
        await signals.settle()
        form = await form_state(page)
        texts = [f for f in form.fields if f["type"] in ("text", "search", "textarea")]
        marked = next((f for f in texts if "name" in f["label"].lower()), texts[0] if texts else None)
        marker = _marker()
        rec = to_here[:1] + [{"type": "goto", "url": form_url}]
        for f in form.fields:
            was = next((x["value"] for x in sent.fields if x["label"] == f["label"]), None)
            if was is None or f["type"] in ("password", "file"):
                continue
            value = f"{was} {marker}".strip() if marked and f["idx"] == marked["idx"] else was
            status, _, how = await set_field(page, f, "fill", value)
            if status == "ok":
                rec.append({"type": "action", "action": _rec(how, f, value), "url": form_url})
        button = page.get_by_role("button", name=sent.submit, exact=True).first
        f0, e0 = len(signals.failed), len(signals.page_errors)
        await button.dblclick(timeout=5_000)
        rec.append({"type": "action", "url": form_url,
                    "action": {"action": "dblclick", "locator": {"method": "role", "role": "button", "name": sent.submit}}})
        await signals.settle(timeout_ms=10_000)
        await check(page, signals, f0, e0, f"Clicking {sent.submit} twice quickly", rec)
        if attempt == 0:
            done.append(f"clicked {sent.submit} twice quickly")
        if not marked:
            break
        # Where submitted things are listed: a link the rules name next to "listed", or a "My …" link.
        links = form.links or (await form_state(page)).links
        place, rule = listing_place(links, rules)
        if not place:
            break
        f0, e0 = len(signals.failed), len(signals.page_errors)
        await page.goto(place["href"], wait_until="domcontentloaded", timeout=30_000)
        await signals.settle()
        listed = (await page.evaluate("() => document.body ? document.body.innerText : ''")).count(marker)
        rec_list = rec + [{"type": "goto", "url": place["href"]}]
        await check(page, signals, f0, e0, f"Opening {place['text']} after clicking {sent.submit} twice", rec_list)
        if listed != 1:
            break
    if place and marked:
        if listed >= 2:
            why = f" {rule[0]} says: \"{rule[1][:200]}\"" if rule else ""
            if await keep(schemas.ExploreProblem(
                    title=f"Clicking {sent.submit} twice quickly creates it twice"[:200], severity="high",
                    expected=f"One entry per submit, even when the button is clicked twice (users double-click).{why}"[:600],
                    actual=f"After one double click on {sent.submit}, {place['text']} lists it {listed} times "
                           f"(marked \"{marker}\")."[:600],
                    symptom=schemas.Symptom(kind="text_visible", value=marker,
                                            explanation=f"Listed {listed} times after one double click on {sent.submit}"),
                    same_as=f"double:{urlsplit(form_url).path}:{_norm(sent.submit)}"),
                    ("double", form_url.split("?")[0]), (page, signals, rec_list)):
                found += 1
        done.append(f"looked in {place['text']}: listed {listed} time{'s' if listed != 1 else ''}"
                    + (" (after a second try)" if attempt else ""))
    # 5. The server failing: the same form sent while its request fails, as a user meets it one day.
    if FAULTS:
        what, n = await failure_tour(page, signals, form_url, sent, to_here, keep)
        done.append(what)
        found += n
    # 6. Saved text: Ge'ez, an apostrophe and an ampersand sent, then looked for as typed.
    if SAVED_TEXT:
        from app.agent.saved import saved_text_tour

        async def fill_with(pg, overrides: dict) -> list[dict]:
            out = []
            for f in (await form_state(pg)).fields:
                was = overrides.get(f["label"], next((x["value"] for x in sent.fields if x["label"] == f["label"]), None))
                if was is None or f["type"] in ("password", "file"):
                    continue
                status, _, kind = await set_field(pg, f, "fill", was)
                if status == "ok":
                    out.append({"type": "action", "action": _rec(kind, f, was), "url": form_url})
            return out
        what, n = await saved_text_tour(page, signals, form_url, sent, to_here, rules, keep, fill=fill_with)
        if what:
            done.append(what)
        found += n
    # 7. Interruptions: signed out halfway, and the same record saved from two tabs.
    if INTERRUPTIONS:
        from app.agent.interrupt import interruptions_tour

        async def fill_sent(pg) -> list[dict]:
            out = []
            for f in (await form_state(pg)).fields:
                was = next((x["value"] for x in sent.fields if x["label"] == f["label"]), None)
                if was is None or f["type"] in ("password", "file"):
                    continue
                status, _, kind = await set_field(pg, f, "fill", was)
                if status == "ok":
                    out.append({"type": "action", "action": _rec(kind, f, was), "url": form_url})
            return out
        what, n = await interruptions_tour(
            page, context, signals, form_url, sent, to_here, keep, fill=fill_sent, signed_in=bool(sign_in_cfg),
            on_sign_in=lambda url: bool(sign_in_cfg) and signed_out(url, sign_in_cfg, base or form_url))
        if what:
            done.append(what)
        found += n
    # 8. The keyboard only: Tab through the form, see focus, send it with Enter.
    if KEYBOARD:
        from app.agent.keyboard import keyboard_tour

        async def fill_as_sent(pg) -> list[dict]:
            out = []
            for f in (await form_state(pg)).fields:
                was = next((x["value"] for x in sent.fields if x["label"] == f["label"]), None)
                if was is None or f["type"] in ("password", "file"):
                    continue
                status, _, kind = await set_field(pg, f, "fill", was)
                if status == "ok":
                    out.append({"type": "action", "action": _rec(kind, f, was), "url": form_url})
            return out
        what, n = await keyboard_tour(page, signals, form_url, sent, to_here,
                                      keep, fill=fill_as_sent)
        done.append(what)
        found += n
    await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
    await signals.settle()
    return ", ".join(done) + (f"; {found} problem{'s' if found != 1 else ''} drafted" if found else "; nothing broke")


FAULTS = (("500", "the server answers 500"), ("offline", "the network drops"))
BUSY = re.compile(r"…|\.\.\.$|\b(loading|please wait|checking|placing|processing|saving|sending|submitting|verifying)\b", re.I)
FAILURE_WORDS = ("went wrong", "error", "failed", "try again", "couldn't", "could not", "unable", "unavailable",
                 "no connection", "offline", "network", "problem", "sorry")


def _failing(mode: str, home: str, hit: list[str]):
    """A route handler that fails the app's own fetch/XHR calls that change something (not GETs).
    (One argument only: Playwright passes the request too to a handler that takes two.)"""
    from urllib.parse import urlsplit

    async def fail(route):
        req = route.request
        if req.method != "GET" and req.resource_type in ("fetch", "xhr") and origin(req.url) == home:
            hit.append(f"{req.method} {urlsplit(req.url).path or '/'}")
            if mode == "500":
                await route.fulfill(status=500, content_type="application/json",
                                    body='{"error": "Internal Server Error (made by Mirmera\'s failure tour)"}')
            else:
                await route.abort("internetdisconnected")
        else:
            await route.continue_()
    return fail


async def failure_tour(page, signals, form_url: str, sent: FormState, to_here: list[dict], keep) -> tuple[str, int]:
    """The form sent again while the request it makes fails: first the server answers 500, then
    the network drops. A user meets both one day; a tester can rarely make them happen. What the
    page should do: say so, keep what was typed, and never claim success. Drafted when it throws,
    stays on "Checking…", says nothing, says it worked, or loses what was typed. (No AI.)
    Returns (what it did, drafts made)."""
    from urllib.parse import urlsplit
    home = origin(form_url)
    found, broke = 0, []
    for mode, how in FAULTS:
        await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
        await signals.settle()
        form = await form_state(page)
        rec = to_here[:1] + [{"type": "goto", "url": form_url}]
        for f in form.fields:
            was = next((x["value"] for x in sent.fields if x["label"] == f["label"]), None)
            if was is None or f["type"] in ("password", "file"):
                continue
            status, _, kind = await set_field(page, f, "fill", was)
            if status == "ok":
                rec.append({"type": "action", "action": _rec(kind, f, was), "url": form_url})
        hit: list[str] = []
        fail = _failing(mode, home, hit)

        before = await form_state(page)
        e0 = len(signals.page_errors)
        await page.route("**/*", fail)
        try:
            await page.get_by_role("button", name=sent.submit, exact=True).first.click(timeout=5_000)
            await signals.settle(timeout_ms=5_000)
            await page.wait_for_timeout(3_000)  # what still says "Checking…" after this is stuck
            after = await form_state(page)
        finally:
            await page.unroute("**/*", fail)
        if not hit:
            return "the form sent no request Mirmera could fail", found
        request = hit[0]
        rec.append({"type": "action", "url": form_url,
                    "action": {"action": "click", "locator": {"method": "role", "role": "button", "name": sent.submit}}})
        fault = f"{request} {'answering 500' if mode == '500' else 'with the network down'}"
        said, _ = answer_of(schemas.ExploreAction(kind="click", locator=schemas.PlanLocator(method="role", role="button",
                                                                                         name=sent.submit)), before, after)
        text = " ".join(said).lower()
        left = [f for f in before.fields if f["type"] in ("text", "email", "tel", "number", "textarea") and f["value"]
                and next((a["value"] for a in after.fields if a["label"] == f["label"]), f["value"]) == ""]
        problems = []
        for c in crashes([], signals.page_errors[e0:]):
            c.title = f"When {how}, {sent.submit} throws: {c.symptom.value}"[:200]
            c.actual = f"With {fault} (Mirmera's failure tour), the page threw: {c.symptom.value}."[:600]
            problems.append(c)
        busy = next((l for l in after.lines if BUSY.search(l) and l not in before.lines), None) or \
            next((m for m in after.messages if BUSY.search(m)), None)
        moved = after.url.split("#")[0] != before.url.split("#")[0]
        if said and outcome_of(said, []) == "accepted" or (moved and not any(w in text for w in FAILURE_WORDS)):
            claim = said[0] if said else after.heading or after.title
            problems.append(schemas.ExploreProblem(
                title=f"Says \"{claim[:60]}\" although {how}"[:200], severity="high",
                expected="When the request fails, the page says it didn't work, so the user doesn't think it did.",
                actual=f"With {fault} (Mirmera's failure tour), the page answered: \"{claim[:200]}\"."[:600],
                symptom=schemas.Symptom(kind="text_visible", value=claim[:300], explanation=f"Success shown while {how}")))
        elif busy:
            problems.append(schemas.ExploreProblem(
                title=f"When {how}, the page stays on \"{busy[:60]}\""[:200], severity="medium",
                expected="When the request fails, the page stops waiting and says what happened.",
                actual=f"With {fault} (Mirmera's failure tour), the page still says \"{busy[:200]}\" seconds later."[:600],
                symptom=schemas.Symptom(kind="text_visible", value=busy[:300], explanation=f"Stuck waiting while {how}")))
        elif not any(w in text for w in FAILURE_WORDS) and not problems:
            problems.append(schemas.ExploreProblem(
                title=f"No message when {how}"[:200], severity="medium",
                expected="When the request fails, the page says it didn't work and what to do.",
                actual=f"With {fault} (Mirmera's failure tour), nothing on the page changed or said so."[:600],
                symptom=schemas.Symptom(kind="text_absent", value="went wrong", explanation=f"Silent while {how}")))
        if left and not moved:
            problems.append(schemas.ExploreProblem(
                title=f"What was typed is lost when {how}"[:200], severity="low",
                expected="After a failed submit the form keeps what the user typed, so they can try again.",
                actual=f"With {fault}, {', '.join(f['label'] for f in left[:3])} came back empty."[:600],
                symptom=schemas.Symptom(kind="text_absent", value=left[0]["value"][:300], explanation="Typed value gone")))
        for prob in problems:
            prob.same_as = f"fault:{mode}:{urlsplit(form_url).path}:{_norm(prob.title)[:80]}"
            prob.fault = fault
            if await keep(prob, ("fault", mode, prob.title), (page, signals, rec)):
                found += 1
        broke.append(f"{how}: " + ("; ".join(p.title for p in problems) if problems else "it said so"))
    return "made the request fail (" + " · ".join(broke) + ")", found


class SignInFailed(Exception):
    """The app under test couldn't be signed in to: the run doesn't start signed out by mistake."""


def signed_out(url: str, cfg: dict, base: str) -> bool:
    from app.sign_in import signed_out as on_sign_in_page
    return on_sign_in_page(url, cfg, base)


def secrets_of(cfg: dict | None) -> list[str]:
    from app import test_data
    from app.sign_in import secrets
    return secrets(cfg) + test_data.secrets()


# ---------- confirming a draft before a tester reads it ----------

async def confirm_drafts(pw, bug_ids: list[str], session_id: str, headless: bool, executable_path: str | None,
                         prepare=None, context_options: dict | None = None) -> str:
    """Replays each draft this session made (not sightings of older ones) from its steps, in fresh
    browsers, and checks its symptom each time: "always" (every time), "intermittent" (some
    times) or "once" (never again). Kept on the bug as its reproducibility and confirmation."""
    from app.agent.reproduce import _reruns
    with SessionLocal() as db:
        todo = [(b.id, b.title, b.edited_steps, b.symptom) for b in (db.get(models.Bug, i) for i in bug_ids)
                if b and b.session_id == session_id and b.symptom and not b.confirmation
                and not (b.environment or {}).get("injected_failure")]  # its steps alone can't make the failure happen
    out = []
    for bug_id, title, steps, symptom in todo:
        if steps:
            result = await _reruns({"steps": steps}, symptom, CONFIRM_RUNS, headless, executable_path, pw, prepare=prepare,
                                   context_options=context_options, browser=ENGINE.get())
        else:
            result = {"runs": 0, "reproduced": 0, "results": [], "note": "no steps to replay"}
        n, runs = result["reproduced"], result["runs"]
        with SessionLocal() as db:
            bug = db.get(models.Bug, bug_id)
            bug.confirmation = {**result, "at": utcnow().isoformat()}
            if runs:
                bug.reproducibility = "always" if n == runs else "intermittent" if n else "once"
            db.commit()
        out.append(f"\"{title[:80]}\" {n} of {runs}" if runs else f"\"{title[:80]}\": no steps to replay")
    return ("Replayed each new draft in a fresh browser: " + "; ".join(out)) if out else ""


# ---------- the run ----------

async def run_agent(run_id: str, ctx: CharterContext, provider: LLMProvider, *, session_id: str, charter_id: str,
                    start_url: str, origins: set[str], max_steps: int, headless: bool, executable_path: str | None,
                    folder: Path, checker: LLMProvider | None = None, conditions: dict | None = None,
                    sign_in_cfg: dict | None = None) -> None:
    from playwright.async_api import async_playwright

    ACTIVE.add(run_id)
    from app import test_data
    from app.agent import ethiopia
    test_data.start(ctx.test_data)  # this run's rows (and nothing picked yet)
    ethiopia.ON.set(ctx.ethiopia)
    history: list[dict] = []
    bug_ids: list[str] = []
    reported: set[tuple[str, str]] = set()
    ideas_seen: dict = {}  # test ideas tried and not, for the summary Mirmera writes when the AI wrote none
    charter_steps = {"on": 0, "beyond": []}  # tests on the charter's focus, and those beyond it: (n, fields, bug ids)
    steps: list[dict] = [{"type": "goto", "url": start_url}]
    started = time.monotonic()
    run_timeout = max(RUN_TIMEOUT_S, max_steps * STEP_TIME_S)
    final: dict = {}
    video: str | None = None  # the run's recording, kept in the session's folder
    har: str | None = None  # its network log (HAR), cleaned of secrets, in the same folder

    def now() -> datetime:
        return utcnow()

    try:
        async with async_playwright() as pw:
            from app import browsers
            ENGINE.set((conditions or {}).get("browser") or "chromium")
            browser = await browsers.launch(pw, ENGINE.get(), headless=headless, executable_path=executable_path)
            context = None
            try:
                options = browsers.context_options(ENGINE.get(), context_options(conditions))
                context = await _recording_context(browser, folder, run_id, options)
                filmed_from = time.monotonic()  # step times are kept against the recording's start
                if (conditions or {}).get("clock_at"):
                    from zoneinfo import ZoneInfo
                    CLOCK_AT.set(datetime.fromisoformat(conditions["clock_at"]).astimezone(
                        ZoneInfo(conditions.get("timezone") or "Africa/Addis_Ababa")))
                    await set_clock(context)
                    _note(session_id, "setup", f"The browser's clock is set to {clock_line().removeprefix('Set the computer' + chr(39) + 's clock to ')}: "
                                               "the app's today is that day.")
                if ENGINE.get() != "chromium":
                    _note(session_id, "setup", f"Exploring in {browsers.label(ENGINE.get())}"
                          + (" (the slow network is Chromium's only: this run's network is normal)."
                             if (conditions or {}).get("network") == "slow" else "."))
                from app import test_data as _td
                _td.TODAY.set(run_today())  # {today} is the user's today, by the run's clock when it has one
                page = await context.new_page()
                live = LIVE[run_id] = Live()
                await _start_screencast(page, live, options["viewport"], slow=(conditions or {}).get("network") == "slow")
                signals = Signals()
                signals.attach(page)
                seen_signals: dict[str, int] = {}
                seen: dict[str, int] = {}
                tried: dict[tuple[str, str, str], int] = {}  # (click, page, field values before it) -> step
                noted: set[str] = set()  # notes already written, so one isn't written again
                submits: list[tuple[dict, str, int]] = []  # (field values, outcome, step) of each submit that got an answer
                accepted_values: list[str] = []  # values the app accepted: they should show up where the rules say
                seen_pages: set[str] = set()  # pages already looked at for glitches and accessibility
                asked: set[str] = set()  # questions already noted
                tried_values: dict[str, set[str]] = {}  # field label -> values submitted (for the test ideas)
                curios_noted: set[str] = set()  # display versus data: each curio once a run
                stored: dict = {"local": {}, "session": {}, "cookies": {}}  # browser storage after the last step
                last_fields: list[dict] = []  # the last form seen, for the ideas tally at the end
                toured: set[str] = set()  # forms whose flow was toured after an accepted submit
                tour_due = None
                dirty: dict[str, str] = {}  # fields changed since the last submit: nothing is tested until it is sent
                n_submits = 0
                visited: set[str] = {start_url.split("#")[0]}
                stuck = 0  # steps in a row that taught nothing (refused, failed, unusable)
                if sign_in_cfg:
                    # The app under test is signed in before anything else, as a user would be.
                    from app.sign_in import sign_in
                    ok, how = await sign_in(page, sign_in_cfg, start_url, signals)
                    _note(session_id, "setup", f"Signed in to the app: {how}." if ok else f"Couldn't sign in to the app: {how}.")
                    if not ok:
                        raise SignInFailed(how)
                await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
                await signals.settle()
                _record(session_id, [Signal("navigation", f"Opened {start_url}", start_url, now())])
                signed_again = 0
                bad_replies = 0

                def drain_signals() -> None:
                    """The app's errors, kept in the session as the runner keeps them."""
                    out = []
                    for kind, items in (("failed_request", signals.failed), ("console_error", signals.console_errors),
                                        ("page_error", signals.page_errors)):
                        out += [Signal(kind, x[:500], page.url, now()) for x in items[seen_signals.get(kind, 0):]]
                        seen_signals[kind] = len(items)
                    if out:
                        _record(session_id, out)

                for _ in range(max_steps * REPLIES_PER_STEP):
                    if _cancelled(run_id):
                        final = {"status": "cancelled", "detail": "Stopped by a tester."}
                        break
                    used = steps_used(history)
                    if used >= max_steps:
                        final = {"status": "finished", "detail": f"Used all {max_steps} steps."}
                        break
                    if time.monotonic() - started > run_timeout:
                        final = {"status": "finished", "detail": f"Stopped: the run took longer than {run_timeout // 60} minutes."}
                        break
                    if sign_in_cfg and signed_again < 3 and signed_out(page.url, sign_in_cfg, start_url):
                        # Back on the sign-in page: the session ended (or the app signed out). Sign in again.
                        from app.sign_in import sign_in
                        signed_again += 1
                        back_to = history[-1]["url"] if history else start_url
                        ok, how = await sign_in(page, sign_in_cfg, start_url, signals)
                        _note(session_id, "issue", f"The app signed the run out (on {page.url}); signed in again: {how}." if ok
                              else f"The app signed the run out and signing in again failed: {how}.")
                        if ok and not signed_out(back_to, sign_in_cfg, start_url):
                            await page.goto(back_to, wait_until="domcontentloaded", timeout=30_000)
                            await signals.settle()
                    observation = await observe(page, signals, seen)
                    for secret in secrets_of(sign_in_cfg):
                        observation = observation.replace(secret, "•••")
                    before = await form_state(page)
                    if before.fields:
                        last_fields = before.fields
                        if not ctx.focus:  # the fields the charter is about, once a form is seen
                            ctx.focus = focus_fields(ctx.mission, before.fields)
                    if before.text():
                        observation += "\n" + defang(before.text())
                    drain_signals()
                    hints = _pending_hints(run_id)
                    today = run_today()
                    ideas, _, _ = ideas_text(before.fields, tried_values, today, rules=rules_of(ctx))
                    tip = suggest(ctx, before, tried_values, dirty, n_submits, visited, today)
                    stepped_in = False
                    if stuck >= STEP_IN_AFTER and tip and not hints:
                        # A small model can loop for the rest of the run; a tester at its side would take
                        # the keyboard for a moment. Mirmera does: it runs the next idea itself.
                        step = schemas.LLMExploreStep(thought=f"Mirmera stepped in: {stuck} steps in a row taught nothing, "
                                                              f"so it took the next step itself ({tip[1]}).",
                                                      action=schemas.ExploreAction(**tip[0]))
                        stepped_in = True
                    else:
                        try:
                            step = await complete_json(provider, PROMPT, _user_message(ctx, history, observation, max_steps - used,
                                                                                       hints, ideas, tip, dirty),
                                                       schemas.LLMExploreStep, task="explore_step", max_tokens=1500)
                        except LLMOutputError as err:
                            bad_replies += 1
                            stuck += 1
                            reason = str(err).split("Details: ", 1)[-1].splitlines()[:3]
                            history.append({"n": len(history) + 1, "thought": "", "action": "(unusable reply)", "kind": "invalid",
                                            "status": "failed", "detail": "; ".join(reason), "url": page.url})
                            _save(run_id, steps=list(history))
                            if bad_replies >= MAX_BAD_REPLIES:
                                raise
                            continue
                        bad_replies = 0
                    if hints:
                        _hints_taken(run_id, {h["id"] for h in hints}, len(history) + 1)
                        for h in hints:
                            if h["by"] != "Mirmera":
                                _note(session_id, "setup", f"Hint from {h['by']}: {h['text']}")
                    a = step.action
                    picked = ""
                    if a.pick:  # a fill without a value: the next test idea for that field
                        f = find_field(before, field_name(a))
                        choice = next(((w, v) for w, v in ideas_for(f, today, rules_of(ctx)) if not is_tried(f, v, tried_values)),
                                      None) if f else None
                        if f and (choice or valid_value(f, today, rules_of(ctx)) is not None):
                            what, value = choice or ("ordinary valid data", valid_value(f, today, rules_of(ctx)))
                            a = a.model_copy(update={"value": value, "pick": False})
                            picked = f"no value given, so Mirmera used the next test idea ({what})"
                    entry = {"n": len(history) + 1, "thought": step.thought,
                             "action": describe(a) if not a.pick else f"{a.kind} {field_name(a) or '?'} (no value)",
                             "kind": a.kind, "status": "ok", "detail": "", "url": page.url}
                    if stepped_in:
                        entry["by"] = "Mirmera"
                        for name, v in (a.values or {}).items():  # it counts as tried whatever happens
                            if fld := find_field(before, name):
                                tried_values.setdefault(fld["label"], set()).add(v)
                    entry["t"] = round(time.monotonic() - filmed_from, 2)  # where this step is in the recording
                    if a.kind != "done":
                        entry |= await _step_frame(page, folder, run_id, entry["n"], a)

                    if a.kind == "done":
                        history.append(entry)
                        final = {"status": "finished", "summary": a.summary, "detail": "The agent finished the charter."}
                        break
                    if a.pick:
                        entry["status"], entry["detail"] = "refused", f"{a.kind} needs a value and a field of this page." + suggestion_text(tip)
                        history.append(entry)
                        stuck += 1
                        _save(run_id, steps=list(history))
                        continue
                    if a.kind == "note":
                        why = None
                        if _norm(a.note) in noted:
                            why = "you already noted this."
                        elif history and history[-1]["kind"] == "note" and history[-1]["status"] == "ok":
                            why = "two notes in a row: don't describe the plan, act on it."
                        elif CLAIM.search(a.note) and (dirty or not n_submits):
                            why = (("nothing was submitted since the form changed" if n_submits else "nothing was submitted yet")
                                   + ", so the app hasn't accepted or refused anything: submit to find out.")
                        if why:
                            entry["status"], entry["detail"] = "refused", why + suggestion_text(tip)
                            history.append(entry)
                            stuck += 1
                            _save(run_id, steps=list(history))
                            continue
                        noted.add(_norm(a.note))
                        _note(session_id, "test", a.note)
                        history.append(entry)
                        _save(run_id, steps=list(history))
                        continue
                    if a.kind == "problem":
                        a.problem.same_as = a.problem.fault = None  # Mirmera's own, never the AI's
                        a.problem.found_by = "ai"  # the AI decided this is a problem (Mirmera checked the claim)
                        key = (a.problem.symptom.kind, a.problem.symptom.value)
                        if key in reported:
                            entry["status"], entry["detail"] = "refused", "already reported in this session"
                        else:
                            ok, detail = await verify(page, a.problem.symptom, signals)
                            if not ok:
                                entry["status"], entry["detail"] = "not verified", detail
                            else:
                                shot = folder / f"explore-{run_id[:8]}-{len(bug_ids) + 1}.png"
                                try:
                                    shot.parent.mkdir(parents=True, exist_ok=True)
                                    await page.screenshot(path=str(shot))
                                except Exception:
                                    shot = None
                                bid, new = _draft_bug(session_id, charter_id, a.problem, detail, steps, page.url, shot, provider)
                                reported.add(key)
                                if bid is None:
                                    entry["status"], entry["detail"] = "dismissed before", "the team dismissed this problem before"
                                else:
                                    if bid not in bug_ids:
                                        bug_ids.append(bid)
                                    entry["status"], entry["detail"] = "drafted", detail
                                    _seen(entry, new)
                        if entry["status"] != "drafted":
                            entry["detail"] += "." + suggestion_text(tip)
                        stuck = 0 if entry["status"] == "drafted" else stuck + 1
                        history.append(entry)
                        _save(run_id, steps=list(history), bug_ids=list(bug_ids))
                        continue

                    if why := pointless(a, page.url, before, tried):
                        entry["status"], entry["detail"] = "refused", f"not run: {why}." + suggestion_text(tip)
                        history.append(entry)
                        stuck += 1
                        _save(run_id, steps=list(history))
                        continue
                    if a.kind in SUBMITS:
                        tried[(action_key(a), page.url, before.values())] = entry["n"]
                    if a.kind == "try" and not a.values:
                        tried[baseline_key(page.url)] = entry["n"]
                    failed_before, page_errors_before = len(signals.failed), len(signals.page_errors)
                    leaks_before = len(signals.exif_leaks)
                    answers_before = signals.json_count
                    acted_at = time.monotonic()
                    bugs_before = len(bug_ids)
                    changed = (list((a.values or {}).keys()) if a.kind == "try"
                               else [n] if a.kind in FIELD_ACTIONS and (n := field_name(a)) else [])
                    done_actions: list[dict] | None = None  # what was done, when Mirmera did it its own way
                    try:
                        if a.kind == "try":
                            entry["status"], entry["detail"], done_actions, sent_form = await do_try(page, a, before, today, rules_of(ctx))
                            if sent_form:
                                before = sent_form  # the form as it was sent: what the answer is read against
                        elif a.kind in ("fill", "select", "check", "uncheck") and (f := find_field(before, field_name(a))):
                            entry["status"], entry["detail"], how = await set_field(page, f, a.kind, a.value)
                            if entry["status"] == "ok":
                                a = a.model_copy(update={"kind": how, "locator": schemas.PlanLocator(method="label", value=f["label"]),
                                                         "value": a.value if how in ("fill", "select") else None})
                                if how == "select":
                                    a = a.model_copy(update={"value": option_of(f, a.value or "")})
                                entry["action"], entry["kind"] = describe(a), a.kind
                        else:
                            entry["status"], entry["detail"] = await act(page, a.as_browser_action(), origins)
                    except Exception as err:
                        entry["status"], entry["detail"] = "failed", _first_line(err)
                        if "Malformed value" in str(err):  # a date picker refuses an impossible date: nothing to send
                            for label, v in (a.values or {}).items() if a.kind == "try" else [(field_name(a), a.value)]:
                                if label and v is not None:
                                    tried_values.setdefault(label, set()).add(v)  # not offered again
                            entry["status"] = "refused"
                            entry["detail"] = ("not sent: the field is the browser's date picker, which only holds real dates "
                                               "(YYYY-MM-DD), so the app can't receive this value. Try a real date at an edge instead.")
                    if picked:
                        entry["detail"] = picked + ("; " + entry["detail"] if entry["detail"] else "")
                    if a.kind in SUBMITS and entry["status"] == "ok":
                        for f in before.fields:  # a value counts as tried once it has been submitted
                            tried_values.setdefault(f["label"], set()).add(f["value"])
                    # A submit's answer can take a while (a document check, a slow API): wait for what
                    # it sent to come back, up to 10 s, so the answer is read, not "Checking…".
                    await signals.settle(timeout_ms=10_000 if a.kind in SUBMITS else 3_000)
                    # How long the app took: until what the step sent came back. Measured here, before
                    # Mirmera's own checks (asking the AI about the answer took ~9 s with qwen3:8b and
                    # was reported as "the app took 9.7 s").
                    app_took = time.monotonic() - acted_at
                    if not page.is_closed() and not in_app(page.url, origins):
                        left = page.url
                        await _back_into_app(page, entry["url"], signals)
                        entry["detail"] = (entry["detail"] + "; " if entry["detail"] else "") + \
                            f"that left the app ({origin(left)}), so the browser went back"
                    broke = crashes(signals.failed[failed_before:], signals.page_errors[page_errors_before:],
                                    signals.failed_requests, secrets_of(sign_in_cfg))
                    # What Mirmera noticed besides a crash: drafted like one, but the step didn't break, so the
                    # rule checks and the outcome ("accepted") still count (a page can be accepted and misshown).
                    noticed = exif_leaks(signals.exif_leaks[leaks_before:], origins)
                    answers = [a for a in signals.json_answers if a["n"] > answers_before and in_app(a["url"], origins)]
                    if answers:  # display versus data: the page against the JSON it was drawn from
                        from app.agent import datacheck
                        try:
                            shown = await page.locator("body").inner_text(timeout=3_000)
                        except Exception:
                            shown = ""
                        noticed += display_mismatches(datacheck.mismatches(answers, shown))
                        for c in datacheck.curios(answers, utcnow().date()):
                            if c["key"] not in curios_noted:
                                curios_noted.add(c["key"])
                                _note(session_id, "issue", f"Curio: {c['what']} ({c['method']} {urlsplit_path(c['url'])}). "
                                                           "Mirmera can't tell if this is wrong: worth a look.")
                    if in_app(page.url, origins):  # what the app keeps in the browser (app/agent/storage.py)
                        from app.agent import storage
                        now_stored = await storage.snapshot(page)
                        if kept := storage.changes(stored, now_stored):  # not "changed": that's the fields this step changed
                            entry["storage"] = kept
                        found = storage.curios(now_stored, page.url.startswith("https:"))
                        if storage.is_sign_out(describe(a), page.url):
                            found = storage.after_sign_out(stored, now_stored) + found
                        for c in found:
                            if c["key"] not in curios_noted:
                                curios_noted.add(c["key"])
                                _note(session_id, "issue", f"Curio: {c['what']} (seen on {urlsplit_path(page.url)}). "
                                                           "Mirmera can't tell if this is wrong: worth a look.")
                        stored = now_stored
                    said: list[str] = []  # what the app answered this step
                    after = FormState()
                    toggled = False
                    if entry["status"] == "ok" and not page.is_closed():
                        after = await form_state(page)
                        changes = field_changes(before, after)
                        if a.kind == "click" and changes and all(f["type"] in ("checkbox", "radio") for f, _, _ in changes) \
                                and not [m for m in after.messages if m not in before.messages]:
                            # A click on a checkbox or its label ticks it; it doesn't send anything.
                            toggled = True
                            entry["detail"] = "; ".join(f'"{f["label"]}" is now {now_}' for f, _, now_ in changes) + \
                                " (not sent yet: the page's old message stays until you submit)"
                        else:
                            said, how = answer_of(a, before, after)
                            if said:  # kept in the history, so the AI can compare tries
                                entry["detail"] = (entry["detail"] + "; " if entry["detail"] and a.kind != "try" else "") + \
                                    f"the page {how} says " + "; ".join(f'"{m[:160]}"' for m in said[:3])
                        if a.kind not in SUBMITS or toggled:
                            for f, _, now_ in changes:
                                dirty[f["label"]] = now_
                        visited.add(after.url.split("#")[0])
                    if entry["status"] == "ok":
                        if a.kind == "goto":
                            steps.append({"type": "goto", "url": a.url})
                            _record(session_id, [Signal("navigation", f"Opened {a.url}", a.url, now())])
                        else:
                            for rec in (done_actions if done_actions is not None else [recorded_action(a)]):
                                if rec:
                                    steps.append({"type": "action", "action": rec, "url": entry["url"]})
                                    _record(session_id, [Signal("action", describe_action(rec), entry["url"], now(), rec)])
                    is_submit = a.kind in SUBMITS and not toggled
                    if is_submit and entry["status"] == "ok":
                        dirty.clear()
                        n_submits += 1
                    if said and ctx.has_rules() and is_submit:  # a submit, and the app answered
                        j = await judge(checker or provider, ctx, before, said, page.url)
                        if j:
                            rules = rules_of(ctx)
                            verdict, why = decide(j, rules, before, said)
                            rule_id = (j.rule or "").strip().upper()
                            own = own_verdict(rule_id, rules, before, said, outcome_of(said, broke), today)
                            if own:  # a rule Mirmera reads itself: its verdict, and its draft below
                                verdict, why = own
                            rule_text = next((v for k, v in rules.items() if k.upper() == rule_id), "")
                            entry["judged"] = verdict
                            entry["judged_why"] = why
                            entry["judged_means"] = j.answer_means
                            quote = j.answer_quote.strip() or said[0]
                            note = f"Sent: {sent_text(before)[:400]}. The app said: \"{said[0][:160]}\"."
                            if verdict == "breaks":
                                note += f" Breaks {rule_id} (\"{rule_text[:160]}\"): {why}. {j.because}".rstrip()
                                key = ("rule", rule_id)
                                if key not in reported and not broke and not own:  # a crash is drafted below, once
                                    must = "refused" if j.rule_expects == "refuse" else "accepted"
                                    problem = schemas.ExploreProblem(
                                        found_by="ai",  # the AI read the answer and judged the rule broken
                                        title=(j.title or f"{j.answer_means.capitalize()} although {rule_id} says it must be {must}")[:200],
                                        expected=f"Per {rule_id} (\"{rule_text[:300]}\"), this must be {must}. {j.because}"[:600],
                                        actual=f"With {sent_text(before)[:300]}, the app answered: \"{quote[:200]}\"."[:600],
                                        severity="high" if j.rule_expects == "refuse" else "medium",
                                        symptom=schemas.Symptom(kind="text_visible", value=quote[:300],
                                                                explanation=f"Breaks {rule_id}: {rule_text}"[:600]))
                                    ok, evidence = await verify(page, problem.symptom, signals)
                                    if ok:
                                        shot = folder / f"explore-{run_id[:8]}-{len(bug_ids) + 1}.png"
                                        try:
                                            await page.screenshot(path=str(shot))
                                        except Exception:
                                            shot = None
                                        bid, new = _draft_bug(session_id, charter_id, problem,
                                                              f"{evidence}. Breaks {rule_id}: {rule_text}", steps, page.url, shot, provider)
                                        reported.add(key)
                                        if bid is not None:
                                            if bid not in bug_ids:
                                                bug_ids.append(bid)
                                            entry["status"] = "drafted"
                                            _seen(entry, new)
                                        if bid is not None and (g := generalize_hint(before, problem.title)):
                                            _mirmera_hint(run_id, g)
                                entry["detail"] += f"; breaks {rule_id}"
                            elif verdict == "consistent":
                                note += f" As {rule_id} expects."
                            else:
                                note += f" Not judged: {why}."
                                q = _norm(said[0])[:160]
                                if why.startswith("no rule") and q not in asked:  # a good tester asks the team
                                    asked.add(q)
                                    _note(session_id, "question", f"No rule says what should happen here. Sent: {sent_text(before)[:300]}. "
                                                                  f"The app said: \"{said[0][:160]}\". Is that what the team wants?")
                            _note(session_id, "test", note)
                    async def keep(problem: schemas.ExploreProblem, key: tuple, extra: str = "", *, on=None) -> bool:
                        """A checked bug draft for something Mirmera saw itself (no AI involved).
                        on: (page, its signals, the steps to it, the history entry) for a flow tour."""
                        pg, sigs, rec, ent = on or (page, signals, steps, entry)
                        if key in reported:
                            return False
                        ok, evidence = await verify(pg, problem.symptom, sigs)
                        if not ok:
                            return False
                        shot = folder / f"explore-{run_id[:8]}-{len(bug_ids) + 1}.png"
                        try:
                            await pg.screenshot(path=str(shot))
                        except Exception:
                            shot = None
                        bid, new = _draft_bug(session_id, charter_id, problem, evidence + extra, rec, pg.url, shot, provider)
                        if bid is None:  # dismissed before by the team
                            reported.add(key)
                            return False
                        if bid not in bug_ids:
                            bug_ids.append(bid)
                        reported.add(key)
                        ent["status"] = "drafted"
                        ent["detail"] = (ent["detail"] + "; " if ent["detail"] else "") + problem.title
                        _seen(ent, new)
                        return True

                    submitted = is_submit and entry["status"] in ("ok", "drafted")
                    sent = f" (sent: {sent_text(before)[:300]})" if submitted and before.fields else ""
                    # Crashes need no rule: a server error or an uncaught page error after this step
                    # is a bug draft whatever the story says (Mirmera saw it itself; no AI involved).
                    for crash in broke + noticed:
                        crash.actual = (f"After {entry['action']}{sent}"
                                        + f": {crash.actual}" + (f' The page said: "{said[0][:160]}".' if said else ""))[:600]
                        if await keep(crash, (crash.symptom.kind, crash.symptom.value)) and submitted \
                                and (g := generalize_hint(before, crash.title)):
                            _mirmera_hint(run_id, g)

                    if submitted and said and not broke and ctx.has_rules():
                        # The rules that name what was sent (Ge'ez script, an expired date…): checked by
                        # Mirmera itself, so a bug doesn't depend on a small model reading the rule right.
                        did = outcome_of(said, broke)
                        cases = rule_cases(rules_of(ctx), before, today)
                        refusing = [c for c in cases if c[2] == "refuse"]
                        for rid, rtext, expects, first, kind, tested in cases:
                            breaks = (did == "accepted" and expects == "refuse") or \
                                     (did == "refused" and expects == "accept" and not refusing
                                      and not refusal_about_a_gap(before, said, tested))
                            ruled = ("rule", rid, kind) if tested in {x["label"] for x in before.fields if x["type"] == "file"} \
                                else ("rule", rid)  # each way a file breaks the rule is its own bug
                            if not breaks:
                                continue
                            entry["judged"] = "breaks"  # a break, even when it was drafted at an earlier step
                            if ruled in reported:
                                break
                            should, got = ("refused", "accepted") if expects == "refuse" else ("accepted", "refused")
                            await keep(schemas.ExploreProblem(
                                title=f"{kind[:1].upper() + kind[1:]} is {got} although {rid} says it is {should}"[:200],
                                severity="high" if expects == "refuse" else "medium",
                                expected=f"Per {rid} (\"{rtext[:300]}\"), {first} is {should}."[:600],
                                actual=f"With {sent_text(before)[:300]}, the app answered: \"{said[0][:200]}\"."[:600],
                                symptom=schemas.Symptom(kind="text_visible", value=said[0][:300],
                                                        explanation=f"Breaks {rid}: {rtext}"[:600])),
                                ruled, f". Breaks {rid}: {rtext}")
                            entry["judged"] = "breaks"
                            break

                    sent_dates = {f["label"]: f["value"] for f in before.fields
                                  if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f["value"] or "")} if submitted and not broke else {}
                    if sent_dates:
                        # A date the page shows in the Ethiopian calendar: converted here and compared, no AI.
                        from app.agent import clock
                        try:
                            shown = await page.evaluate("() => document.body ? document.body.innerText : ''")
                        except Exception:
                            shown = ""
                        for w in clock.wrong_dates(shown, sent_dates):
                            await keep(schemas.ExploreProblem(
                                title=(f"Shows {w['shown']} for {w['field']} {sent_dates[w['field']]}" if w.get("field")
                                       else f"Shows {w['shown']}, a day that doesn't exist")[:200],
                                severity="high",
                                expected=("Each date shown in the Ethiopian calendar is the date sent: " + w["why"]
                                          if w.get("field") else "Only days that exist in the Ethiopian calendar are shown.")[:600],
                                actual=f"After sending {sent_text(before)[:300]}, the page shows \"{w['shown']}\": {w['why']}."[:600],
                                symptom=schemas.Symptom(kind="text_visible", value=w["shown"],
                                                        explanation=w["why"][:600])),
                                ("ethiopian-date", w["shown"]), f". Checked against the Ethiopian calendar: {w['why']}")

                    if submitted and (said or broke):
                        # What the app did: from its own words first (no AI to misread it), else the check's reading.
                        outcome = outcome_of(said, broke)
                        if outcome == "other" and entry.get("judged_means") in ("accepted", "refused", "error"):
                            outcome = entry["judged_means"]
                        from app import test_data
                        for line in test_data.after_submit(outcome, said, session_id, run_id, noted, sent=before.fields):
                            _note(session_id, "test", line)
                        values = {f["label"]: f["value"] for f in before.fields}
                        # Consistent with itself: the form offers these choices, so each should work like
                        # the others. Same form, one choice changed, accepted before and not now: a bug.
                        for prev_values, prev_outcome, prev_n in submits:
                            change = one_choice_changed(prev_values, values, before.fields)
                            if change and prev_outcome == "accepted" and outcome in ("refused", "error") and said \
                                    and entry["status"] != "drafted":
                                fld, was, now_ = change
                                what = "refused" if outcome == "refused" else "fails"
                                await keep(schemas.ExploreProblem(
                                    title=f"{fld} = {now_} is {what} where {was} is accepted"[:200], severity="high",
                                    expected=f"Every {fld} the form offers works like the others: with {fld} = {was} and "
                                             f"everything else the same, the app accepted it (step {prev_n}).",
                                    actual=f"With {fld} = {now_}{sent}, the app answered: \"{said[0][:200]}\"."[:600],
                                    symptom=schemas.Symptom(kind="text_visible", value=said[0][:300],
                                                            explanation="The same form with one choice changed got a different answer.")),
                                    ("choice", fld, now_), " (consistency with the product itself)")
                                break
                        from app.agent import ethiopia
                        if (prob := ethiopia.check(before.fields, values, outcome, submits, rules_of(ctx), said)):
                            await keep(prob, ("ethiopia", prob.title), " (valid Ethiopian formats, the rest of the form the same)")
                        submits.append((values, outcome, entry["n"]))
                        if outcome == "accepted":
                            accepted_values += [v for v in kept_values(before) if v not in accepted_values]
                            # What was typed, as typed: a mangled copy on the answer page is a draft (no AI).
                            from app.agent.saved import free_text, garbled, problem as saved_problem
                            for g in garbled({f["label"]: f["value"] for f in free_text(before.fields)}, after.lines):
                                await keep(saved_problem(g, f"The answer to {entry['url'].split('?')[0]}"),
                                           ("saved", g["field"], g["why"]))
                            form_path = entry["url"].split("?")[0].split("#")[0]
                            if TOURS and form_path not in toured and before.submit and not page.is_closed():
                                toured.add(form_path)
                                tour_due = (entry["url"], page.url, before, list(steps))

                    if entry["status"] in ("ok", "drafted") and not page.is_closed() and after.url:
                        new_page = after.url.split("#")[0] != (before.url or "").split("#")[0]
                        # Follow the data: a rule names this page as where accepted data is listed.
                        if new_page and accepted_values and (pr := place_rule(rules_of(ctx), after)):
                            rid, rtext = pr
                            text = " ".join(after.lines)
                            if not any(v in text for v in accepted_values):  # none of it: the item isn't there at all
                                key_value = sorted(accepted_values, key=lambda v: (not re.search(r"\d", v), -len(v)))[0]
                                place = after.heading or after.title
                                await keep(schemas.ExploreProblem(
                                    title=f"What was accepted is not listed on {place} ({key_value})"[:200], severity="high",
                                    expected=f"Per {rid} (\"{rtext[:300]}\"), what the app accepted earlier is listed on {place}.",
                                    actual=f"Accepted earlier: {', '.join(accepted_values[:4])}. None of it is on {place}."[:600],
                                    symptom=schemas.Symptom(kind="text_absent", value=key_value[:300],
                                                            explanation=f"Follow the data: {rid} says it is listed here.")),
                                    ("listed", rid))
                        # Text no user should see, on a page seen for the first time or in an answer.
                        path = after.url.split("?")[0].split("#")[0]
                        look = after.lines if path not in seen_pages else (said if submitted else [])
                        for line in glitches(look):
                            await keep(schemas.ExploreProblem(
                                title=f"Raw text shown to users: \"{line[:80]}\""[:200], severity="low",
                                expected="Users see words meant for them, not internal codes or placeholders.",
                                actual=f"On {path} the page shows: \"{line[:200]}\".",
                                symptom=schemas.Symptom(kind="text_visible", value=GLITCH.search(line).group(0)[:300],
                                                        explanation="Raw or internal text on the page.")),
                                ("glitch", GLITCH.search(line).group(0)))
                        # A password in the address: it ends up in history, logs and referrers.
                        query = after.url.split("?", 1)[1] if "?" in after.url else ""
                        if re.search(r"(?:^|&)(?:password|passwd|pwd|pass)=[^&]+", query, re.I):
                            await keep(schemas.ExploreProblem(
                                title="A password is sent in the page address", severity="high",
                                expected="Passwords never appear in a URL (browser history, server logs, referrers).",
                                actual=f"After {entry['action']} the address has a password parameter.",
                                symptom=schemas.Symptom(kind="url", value="pass", explanation="A password parameter in the URL.")),
                                ("url-password",))
                        if path not in seen_pages:
                            seen_pages.add(path)
                            try:
                                from app.runner import A11Y_JS
                                leads = await page.evaluate(A11Y_JS)
                            except Exception:
                                leads = []
                            if leads:
                                _note(session_id, "issue", f"Accessibility leads on {path} ({len(leads)}): "
                                      + "; ".join(f"{x.get('rule')}: {x.get('text')}" for x in leads[:5]))
                            if LAYOUT and not page.is_closed():
                                # The page at phone width, once: sideways scroll, cut-off text, overlap, small targets.
                                from app.agent import layout
                                try:
                                    found = await layout.look(page)
                                except Exception:
                                    found = {}
                                url_path = urlsplit_path(after.url)
                                for prob in layout.problems(found, url_path):
                                    prob.same_as = f"layout:{url_path}:{prob.symptom.value[:80]}"
                                    await keep(prob, ("layout", url_path, prob.symptom.value),
                                               f". Seen at 390 px wide ({'the run itself is on a phone' if (conditions or {}).get('device') == 'phone' else 'Mirmera narrowed the browser for a moment'}).")
                    # Slow answers: a tester notices waiting.
                    if a.kind == "try" and entry["status"] in ("ok", "drafted") and not page.is_closed():
                        for name, code in (a.values or {}).items():  # a counterstring says where text was cut
                            if "@count" not in (code or ""):
                                continue
                            f = find_field(before, name)
                            try:
                                text = await page.locator("body").inner_text(timeout=3_000)
                            except Exception:
                                text = ""
                            label = f["label"] if f else name
                            for found in counter_cut(expand(code, f), f["value"] if f else None, text):
                                entry["detail"] = (entry["detail"] + "; " if entry["detail"] else "") + f"{label} {found}"
                                _note(session_id, "test", f"Counterstring: {label} {found} (each number in it is the "
                                                          "position of the * after it).")
                    if a.kind == "try" and entry["status"] in ("ok", "drafted"):  # a test: on the charter or beyond it
                        if beyond_charter(changed, ctx.focus):
                            if not charter_steps["beyond"]:
                                _note(session_id, "test", f"From step {entry['n']} the agent went beyond the charter "
                                      f"({charter_target(ctx.mission)}): {', '.join(changed)}. Kept, and marked as beyond it.")
                            entry["beyond_charter"] = True
                            entry["detail"] = (entry["detail"] + " " if entry["detail"] else "") + "(beyond the charter)"
                            charter_steps["beyond"].append((entry["n"], changed, bug_ids[bugs_before:], dict(a.values or {})))
                        elif changed and ctx.focus:
                            charter_steps["on"] += 1
                    if is_submit and app_took > 5:
                        _note(session_id, "issue", f"Slow: after {entry['action']} the app took {app_took:.1f} s to answer.")
                    if broke or entry["status"] in ("drafted", "failed"):  # something failed: valid data may be sent again, to re-check
                        tried.pop(baseline_key(entry["url"]), None)
                    history.append(entry)
                    stuck = stuck + 1 if entry["status"] in ("refused", "failed", "not verified") else 0
                    _save(run_id, steps=list(history), bug_ids=list(bug_ids))
                    if tour_due:
                        # An accepted submit: walk the flow the ways users do, not only forward (Mirmera's
                        # own steps, no AI, outside the budget).
                        form_url, answer_url, sent_form, to_here = tour_due
                        tour_due = None
                        tour = {"n": len(history) + 1, "thought": "Mirmera's flow tour after an accepted submit: open the "
                                "answer page directly, refresh it, go back, send the form twice quickly, make its request fail, "
                                "and do it with the keyboard only.",
                                "action": "flow tour", "kind": "tour", "status": "ok", "detail": "", "url": answer_url,
                                "by": "Mirmera", "t": round(time.monotonic() - filmed_from, 2)}
                        try:
                            tour["detail"] = await flow_tour(page, context, signals, form_url, answer_url, sent_form, to_here,
                                                             rules_of(ctx), lambda prob, key, on: keep(prob, key, on=(*on, tour)),
                                                             sign_in_cfg=sign_in_cfg, base=start_url)
                        except Exception as err:  # a tour is extra: it never stops the run
                            tour["detail"] = f"the tour stopped: {_first_line(err)}"
                        history.append(tour)
                        _save(run_id, steps=list(history), bug_ids=list(bug_ids))
                        _note(session_id, "test", f"Flow tour after the submit on {form_url}: {tour['detail']}")
                else:
                    used = steps_used(history)
                    final = {"status": "finished", "detail": f"Used all {max_steps} steps." if used >= max_steps else
                             f"Stopped after {max_steps * REPLIES_PER_STEP} replies from the AI, "
                             f"{len(history) - used} of them refused: it kept repeating steps that teach nothing."}
                drain_signals()
                if last_fields:
                    _, done, total = ideas_text(last_fields, tried_values, utcnow().date(), limit_fields=20, rules=rules_of(ctx))
                    if total:
                        left = [f'{f["label"]}: {w}' for f in last_fields for w, v in ideas_for(f, utcnow().date(), rules_of(ctx))
                                if not is_tried(f, v, tried_values) and not w.startswith(MAGNET)]
                        m_done, m_total = magnet_tally(last_fields[:20], tried_values, utcnow().date())
                        _note(session_id, "setup", f"Test ideas tried: {done} of {total}."
                              + (f" Not tried: {'; '.join(left[:8])}" + ("…" if len(left) > 8 else "") if left else "")
                              + (f" Bug Magnet values tried: {m_done} of {m_total}." if m_total else ""))
                        ideas_seen.update(done=done, total=total, left=left)
            finally:
                try:  # what signing out leaves in the browser: Mirmera signs out itself, last (the agent never does)
                    if not page.is_closed() and in_app(page.url, origins):
                        await _sign_out_check(page, signals, history, session_id, run_id, curios_noted)
                except Exception as err:  # extra: it never fails the run
                    logger.info("No sign-out check: %s", _first_line(err))
                video = await _keep_recording(context, folder, run_id)
                try:
                    known = secrets_of(sign_in_cfg) + [v for k, v in os.environ.items() if k.startswith("MIRMERA_SECRET_") and v]
                except Exception:
                    known = []
                har = _keep_network(folder, run_id, known)
                await browser.close()
            if ACCESS_CHECK and sign_in_cfg and final.get("status") not in ("cancelled", "error"):
                # Who sees what: the user's own pages opened signed out and as the second user. (No AI.)
                from app.agent.access import access_check
                from app.sign_in import second
                access = {"n": len(history) + 1, "thought": "Mirmera opens the pages this user opened, that look like "
                          "their own, as someone else: signed out, and as the project's second user.",
                          "action": "check who sees what", "kind": "access", "status": "ok", "detail": "",
                          "url": start_url, "by": "Mirmera"}

                async def draft_access(problem, pg, rec):
                    from app.regression.replay import Signals
                    ok, evidence = await verify(pg, problem.symptom, Signals())
                    if not ok:
                        return False
                    shot = folder / f"explore-{run_id[:8]}-{len(bug_ids) + 1}.png"
                    try:
                        await pg.screenshot(path=str(shot))
                    except Exception:
                        shot = None
                    bid, new = _draft_bug(session_id, charter_id, problem, evidence, rec, pg.url, shot, provider)
                    if bid is None:
                        return False
                    if bid not in bug_ids:
                        bug_ids.append(bid)
                    access["status"] = "drafted"
                    return True
                try:
                    urls = [h["url"] for h in history if h.get("url")] + sorted(visited)
                    access["detail"] = await access_check(pw, urls, sign_in_cfg, second(sign_in_cfg), start_url,
                                                          executable_path, headless, draft_access)
                except Exception as err:  # extra: it never fails the run
                    access["detail"] = f"could not check: {_first_line(err)}"
                if access["detail"]:
                    history.append(access)
                    _save(run_id, steps=list(history), bug_ids=list(bug_ids))
                    _note(session_id, "test", f"Who sees what: {access['detail']}")
            if CONFIRM_RUNS and bug_ids and final.get("status") != "cancelled":
                # Before a tester reads a draft: does it happen again from its steps alone, in a
                # browser that saw nothing before? (No AI.)
                confirm = {"n": len(history) + 1, "thought": f"Mirmera replays each new draft's steps {CONFIRM_RUNS} times in "
                           "a fresh browser, to tell a steady bug from a one-off.", "action": "confirm the drafts",
                           "kind": "confirm", "status": "ok", "detail": "", "url": start_url, "by": "Mirmera"}
                try:
                    async def as_the_run(fresh_page):  # each fresh browser: the run's clock, signed in as the run was
                        await set_clock(fresh_page.context)
                        if sign_in_cfg:
                            from app.sign_in import sign_in
                            ok, how = await sign_in(fresh_page, sign_in_cfg, start_url)
                            if not ok:
                                raise RuntimeError(f"couldn't sign in: {how}")
                    zone = {k: v for k, v in context_options(conditions).items() if k in ("timezone_id", "locale")}
                    confirm["detail"] = await confirm_drafts(pw, bug_ids, session_id, headless, executable_path,
                                                             prepare=as_the_run if sign_in_cfg or CLOCK_AT.get() else None,
                                                             context_options=zone)
                except Exception as err:  # confirming is extra: it never fails the run
                    confirm["detail"] = f"could not replay: {_first_line(err)}"
                if confirm["detail"]:
                    history.append(confirm)
                    _save(run_id, steps=list(history))
    except SignInFailed as err:
        final = {"status": "error", "detail": f"Couldn't sign in to the app: {err}. Check the project's Settings → "
                                               "Signing in to the app, and try it there."}
    except ProviderError as err:
        final = {"status": "error", "detail": f"The AI could not continue: {err}"}
    except Exception as err:
        logger.exception("Exploration run %s failed", run_id)
        final = {"status": "error", "detail": f"The run failed: {_first_line(err)}"}
    finally:
        try:
            if final.get("status") == "finished" and not final.get("summary"):
                final["summary"] = facts_summary(history, _titles(bug_ids), ideas_seen, final.get("detail", ""))
            if final.get("status") == "finished" and charter_steps["beyond"]:
                line = beyond_line(ctx, charter_steps)
                follow = follow_up_charters(charter_id, ctx, charter_steps)
                final["summary"] = (final.get("summary") or "").rstrip() + " " + line + (
                    " Suggested follow-up charters: " + "; ".join(f'"{m}"' for m in follow) + "." if follow else "")
                if follow:
                    _note(session_id, "test", "Suggested follow-up charters, for what was found beyond the charter: "
                          + "; ".join(f'"{m}"' for m in follow))
            _wrap_up(session_id, final, history, bug_ids)
            if ctx.project_id:
                from app import test_data
                try:
                    test_data.after_run(ctx.project_id, visited_forms=bool(last_fields))
                except Exception:
                    logger.exception("Test data: couldn't record which rows met their fields")
            _save(run_id, steps=list(history), bug_ids=list(bug_ids), finished_at=utcnow(), video=video, har=har,
                  journey=list(steps)[:60], **final)
        finally:
            ACTIVE.discard(run_id)
            LIVE.pop(run_id, None)  # from here on, its steps' frames show what it did


def _titles(bug_ids: list[str]) -> list[str]:
    if not bug_ids:
        return []
    with SessionLocal() as db:
        by_id = {b.id: b.title for b in db.query(models.Bug).filter(models.Bug.id.in_(bug_ids))}
    return [by_id[b] for b in bug_ids if b in by_id]


def facts_summary(history: list[dict], bug_titles: list[str], ideas: dict, why_it_ended: str) -> str:
    """The run's summary when the agent didn't write one (it ran out of steps): what Mirmera knows
    for a fact, written by Mirmera. Pages, tests actually run, bug drafts, test ideas left."""
    ran = [h for h in history if h.get("by") != "Mirmera" and h["status"] not in ("refused", "failed")
           and h["kind"] not in ("note", "problem", "done", "invalid", "goto")]
    sent = sum(1 for h in ran if h["kind"] == "try" or h.get("detail", "").startswith(("the page now says", "the page still says")))
    pages = list(dict.fromkeys(urlsplit_path(h["url"]) for h in history if h.get("url")))
    out = [f"Written by Mirmera: the agent didn't finish with a summary ({why_it_ended.rstrip('.').lower()})."]
    out.append(f"It ran {len(ran)} action{'s' if len(ran) != 1 else ''}, {sent} of them sending a form, "
               f"on {', '.join(pages[:6]) or 'no page'}.")
    if bug_titles:
        out.append(f"Bug drafts ({len(bug_titles)}): " + "; ".join(t[:120] for t in bug_titles[:8]) + ".")
    else:
        out.append("No bug drafts.")
    if ideas.get("total"):
        left = ideas.get("left") or []
        out.append(f"Test ideas tried: {ideas['done']} of {ideas['total']}"
                   + (f"; not tried: {'; '.join(left[:6])}" + ("…" if len(left) > 6 else "") if left else "") + ".")
    return " ".join(out)[:2000]


def beyond_line(ctx: CharterContext, charter_steps: dict) -> str:
    """On the charter, and beyond it, in one sentence for the summary."""
    beyond = charter_steps["beyond"]
    fields = list(dict.fromkeys(f for _, changed, _, _ in beyond for f in changed))
    found = _titles([b for _, _, ids, _ in beyond for b in ids])
    return (f"On the charter ({charter_target(ctx.mission)}): {charter_steps['on']} test{'s' if charter_steps['on'] != 1 else ''}. "
            f"Beyond it: {len(beyond)} ({', '.join(fields[:6])})"
            + (f", which found: {'; '.join(t[:100] for t in found[:4])}." if found else ", nothing found there."))


def rule_for_field(rules: dict[str, str], label: str, values: list[str]) -> str | None:
    """The rule a field's follow-up checks: the one naming the values tried there (AC1 names
    "Passport" for Document type) counts most, then its label's words. The first rule sharing a
    word was picked before: "…as written on the document" made the name rule Document type's."""
    label_words = _focus_stems(label)
    value_words = set().union(*(_focus_stems(v) for v in values)) if values else set()
    best, score = None, 0
    for rid, text in rules.items():
        words = _focus_stems(text)
        s = 2 * len(value_words & words) + len(label_words & words)
        if s > score:
            best, score = rid, s
    return best


def follow_up_charters(charter_id: str, ctx: CharterContext, charter_steps: dict) -> list[str]:
    """A proposed charter for each field where something was found beyond the charter: an unplanned
    find deserves its own session. Linked to the same stories; not made twice."""
    by_field: dict[str, list[str]] = {}
    tried: dict[str, list[str]] = {}  # the values sent to each field beyond the charter
    for _, changed, ids, values in charter_steps["beyond"]:
        for f in changed:
            tried.setdefault(f, []).append(str(values.get(f, "")))
            if ids:
                by_field.setdefault(f, []).extend(ids)
    if not by_field:
        return []
    rules = rules_of(ctx)
    made: list[str] = []
    with SessionLocal() as db:
        charter = db.get(models.Charter, charter_id)
        if not charter:
            return []
        have = [c.mission_statement.lower() for c in db.query(models.Charter).filter(models.Charter.project_id == charter.project_id)]
        for f in list(by_field)[:3]:
            if any(f.lower() in m for m in have):
                continue
            rid = rule_for_field(rules, f, tried.get(f, []))
            info = f"whether it works as {rid} says" if rid else "how it handles unusual values"
            mission = f"Explore {f[:1].lower() + f[1:]} with its test ideas, everything else valid, to discover {info}"
            follow = models.Charter(project_id=charter.project_id, mission_statement=mission, source="follow-up",
                                    priority="high", rationale=(f"Mirmera found something here while exploring "
                                                                f"\"{charter.mission_statement[:120]}\", beyond that charter."),
                                    test_ideas=[], oracles=[])
            follow.stories = list(charter.stories)
            db.add(follow)
            have.append(mission.lower())
            made.append(mission)
        db.commit()
    return made


def _wrap_up(session_id: str, final: dict, history: list[dict], bug_ids: list[str]) -> None:
    """Pauses the session with the agent's summary as a note. A tester reviews it and ends it,
    which drafts the debrief from the agent's notes: the debrief is the tester's."""
    tried = sum(1 for h in history if h["kind"] not in ("note", "problem", "done", "invalid"))
    text = (f"Mirmera agent: {final.get('summary') or final.get('detail') or 'stopped'} "
            f"({tried} actions, {len(bug_ids)} bug draft{'s' if len(bug_ids) != 1 else ''} to review.)")
    with SessionLocal() as db:
        db.add(models.Note(session_id=session_id, tag="setup", content=text[:4000]))
        s = db.get(models.Session, session_id)
        try:  # how far to trust the drafts: what testers made of the agent's drafts so far (app/agent/record.py)
            from app.agent.record import line, record
            charter = db.get(models.Charter, s.charter_id) if s else None
            if charter and (said := line(record(db, charter.project_id))):
                db.add(models.Note(session_id=session_id, tag="setup", content=said))
        except Exception:
            logger.info("No track record for this run", exc_info=True)
        if s and not s.ended_at and not s.paused_at:
            s.paused_at = utcnow()  # the clock stops; testing time is the agent's own
        db.commit()
