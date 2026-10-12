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
import re
import time
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
PROMPT_VERSION = "explore-step-v3"
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
CONFIRM_RUNS = 3  # each new draft's steps, replayed in this many fresh browsers when the run ends
# A note that says the app accepted or refused something: only true after a submit.
CLAIM = re.compile(r"\b(accepts|accepted|rejects|rejected|refuses|refused|works|succeeds|successful(?:ly)?|confirm(?:s|ed|ing)?)\b", re.I)

ACTIVE: set[str] = set()  # run ids being worked on in this process
VIEWPORT = {"width": 1280, "height": 800}  # fixed per run, so a target's box can be drawn on any frame
PHONE = {"width": 390, "height": 844}
SLOW_NETWORK = {"offline": False, "latency": 400, "downloadThroughput": 50 * 1024, "uploadThroughput": 20 * 1024}


def steps_used(steps: list[dict]) -> int:
    """The steps that count against the budget: a refused step was never run, and a flow tour is
    Mirmera's own, so they cost none."""
    return sum(1 for s in steps if s.get("status") != "refused" and s.get("kind") not in ("tour", "confirm"))


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
    if c.get("timezone"):
        out["timezone_id"] = c["timezone"]
    return out


@dataclass
class Live:
    """What the agent's browser shows right now, for watching it work."""
    seq: int = 0
    frame: bytes | None = None
    url: str = ""


LIVE: dict[str, Live] = {}  # run id -> its latest frame, while it runs


async def _start_screencast(page, live: Live, viewport: dict = VIEWPORT, slow: bool = False) -> None:
    """Chromium sends a frame whenever the page repaints: kept as the latest frame."""
    import asyncio
    import base64
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
    try:
        return await browser.new_context(**options, record_video_dir=str(folder / f"video-{run_id[:8]}"),
                                         record_video_size=options["viewport"])
    except Exception as err:  # e.g. Playwright's ffmpeg isn't installed: explore without a recording
        logger.info("No recording for this run: %s", _first_line(err))
        return await browser.new_context(**options)


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
    test_ideas: list[str] = field(default_factory=list)
    oracles: list[str] = field(default_factory=list)
    risks: list[dict] = field(default_factory=list)  # ref, title, failure_mode
    story: str | None = None  # the story's claims, when the charter belongs to one
    criteria: list[str] = field(default_factory=list)  # its acceptance criteria, one rule each
    earlier: list[str] = field(default_factory=list)  # what earlier runs on this charter or story found
    app_map: str | None = None  # the app's pages and forms, from the latest survey
    signed_in_as: str | None = None  # Mirmera signed in to the app as this user before the run

    def has_rules(self) -> bool:
        return bool(self.criteria or self.risks)


CRITERIA_HEADING = re.compile(r"^\W*acceptance criteria\W*$", re.I)
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def criteria_from(story: str | None) -> list[str]:
    """The story's acceptance criteria: the lines under an "Acceptance criteria" heading
    (bulleted or not), or else its bulleted lines."""
    if not story:
        return []
    lines = [l.strip() for l in story.splitlines()]
    out, inside = [], False
    for line in lines:
        if CRITERIA_HEADING.match(line) or re.match(r"^\W*acceptance criteria\s*:", line, re.I):
            inside = True
            rest = line.split(":", 1)[1].strip() if ":" in line else ""
            if rest:
                out.append(rest)
            continue
        if inside:
            if line.startswith("#") or (line.endswith(":") and not BULLET.match(line)):
                break  # the next section
            if line:
                out.append(BULLET.sub("", line))
    if not out:
        out = [BULLET.sub("", l) for l in lines if BULLET.match(l)]
    return [c[:300] for c in out if len(c) > 3][:20]


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
    else if (type === 'password') value = el.value ? '(filled)' : ''
    else if (el.tagName === 'SELECT') value = el.selectedOptions[0] ? el.selectedOptions[0].text : ''
    else value = el.value
    fields.push({ label: labelOf(el), type, value, required: el.required, idx,
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
    Within what the rules allow: a quantity of 1 to 10 gets 1, not 12345."""
    v = _plain_valid(f, today)
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
    if kind in ("radio", "file", "range", "color"):
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
    first = boundary_ideas(f, rules) if rules else []
    have = {v for _, v in first}
    return first + [(w, v) for w, v in _type_ideas(f, today) if v not in have]


def _type_ideas(f: dict, today) -> list[tuple[str, str]]:
    from datetime import timedelta
    label, kind = f["label"].lower(), f["type"]
    if kind == "date" or "date" in label:
        return [("a past date", "2020-01-01"), ("today", today.isoformat()),
                ("tomorrow", (today + timedelta(days=1)).isoformat()), ("29 February", "2028-02-29"),
                ("far in the future", "2099-12-31")]
    if kind.startswith("select"):
        return [(f"option {o}", o) for o in f.get("options") or []]
    if kind in ("checkbox", "radio") or kind == "password" or kind == "file":
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
    return [("empty", ""), ("only spaces", "   "), ("very long", "@long300"), ("markup", "<b>bold</b>"),
            ("quotes", "It's \"quoted\"")]


LONG = re.compile(r"@long(\d{1,5})")


def expand(value: str | None, f: dict | None = None) -> str:
    """"@long300" is 300 characters of filler. A small model asked to copy a long value repeats
    it until its reply runs out (an unusable reply), so ideas name long values by this code."""
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


def ideas_text(fields: list[dict], tried: dict[str, set[str]], today, limit_fields: int = 6,
               rules: dict[str, str] | None = None) -> tuple[str, int, int]:
    """The ideas not tried yet for the fields on this page (a few per field), and the tally."""
    lines, total, done = [], 0, 0
    for f in fields[:limit_fields]:
        ideas = ideas_for(f, today, rules)
        total += len(ideas)
        left = [(w, v) for w, v in ideas if not is_tried(f, v, tried)]
        done += len(ideas) - len(left)
        if left:
            lines.append(f'- {f["label"]}: ' + "; ".join(f'{w} ("{v[:40]}")' for w, v in left[:3]))
    head = (f"Test ideas not tried yet ({done} of {total} tried), each with the rest of the form valid "
            '("@long300" stands for 300 characters: send it as written, Mirmera types them):')
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
    if ctx.app_map:
        charter["app_map"] = ctx.app_map
    if ctx.signed_in_as:
        charter["signed_in"] = f"Mirmera signed you in as {ctx.signed_in_as}. Test as this user and stay signed in: never sign out."
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
                 "subscribed", "joined", "completed", "ordered", "paid")
REFUSAL_WORDS = ("please", "required", "must", "invalid", "not allowed", "only", "can't", "cannot", "not accepted", "rejected",
                 "not available", "unavailable", "sorry", "isn't possible", "not possible", "declined")
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


def kept_values(before: FormState) -> list[str]:
    """What a submit sent that should be findable later: text values, not secrets or choices."""
    return [f["value"] for f in before.fields
            if f["type"] in ("text", "email", "tel", "number", "search", "textarea") and len(f["value"]) >= 4][:4]


def crashes(failed: list[str], page_errors: list[str]) -> list[schemas.ExploreProblem]:
    """Bug drafts for what broke during a step: a server error (5xx) or an uncaught error in
    the page. Wrong whatever the rules say, so no AI is asked."""
    from urllib.parse import urlsplit
    out = []
    for f in failed:
        m = re.match(r"(\w+) (\S+) -> (5\d\d)$", f)
        if m:
            method, url, status = m.groups()
            path = urlsplit(url).path or "/"
            out.append(schemas.ExploreProblem(
                title=f"{method} {path} fails with {status}", severity="high",
                expected="The app handles the request and answers it (accepted or refused with a reason); a server error is never the answer.",
                actual=f"{method} {path} answered {status}.",
                symptom=schemas.Symptom(kind="failed_request", value=path,
                                        explanation="A server error, seen by Mirmera in the browser's network log.")))
    for e in page_errors:
        first = e.strip().splitlines()[0][:150] if e.strip() else "error"
        out.append(schemas.ExploreProblem(
            title=f"Page error: {first}"[:200], severity="medium",
            expected="The page runs without uncaught errors.", actual=f"The page threw: {first}.",
            symptom=schemas.Symptom(kind="page_error", value=first[:120],
                                    explanation="An uncaught error in the page, seen by Mirmera.")))
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


def rule_cases(rules: dict[str, str], sent: FormState, today) -> list[tuple[str, str, str, str, str, str]]:
    """The rules that speak about a kind of value in the form as sent, and what they expect:
    (rule id, rule, accept | refuse, field = value, what kind of value, field label). A rule naming
    Ge'ez script and a name in Ge'ez letters, an expired document and a past expiry date, an
    optional field left empty, a quantity outside its range, an email that isn't valid."""
    out = []
    for f in sent.fields:
        if f["type"] in ("checkbox", "radio", "password", "file"):
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
        for what, words in kinds:
            for rid, text in rules.items():
                if any(w in text.lower() for w in words):
                    out.append((rid, text, "refuse" if NEGATIVE_RULE.search(text) else "accept",
                                f'{f["label"]} = "{v[:60]}"', what, f["label"]))
                    break
    return out


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
    user = (f"Today: {utcnow().date().isoformat()}\n\n<rules>\n"
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


def _draft_bug(session_id: str, charter_id: str, p: schemas.ExploreProblem, evidence: str, steps: list[dict],
               page_url: str, screenshot: Path | None, provider: LLMProvider) -> tuple[str, bool]:
    """(bug id, new?): a bug draft, or, when the project already has a draft of the same problem, a sighting
    added to it: the tester reviews each problem once, and sees everywhere it showed up."""
    key = signature(p)
    with SessionLocal() as db:
        session = db.get(models.Session, session_id)
        project_id = session.charter.project_id if session and session.charter else None
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
            db.add(models.Note(session_id=session_id, tag="bug",
                               content=f"Seen again: {same.title} (already drafted, so added to that bug)"[:4000]))
            db.commit()
            return same.id, False
    try:
        edited = clean_steps(steps)
    except StepError:
        edited = None
    with SessionLocal() as db:
        trail = [{"tag": n.tag, "content": n.content}
                 for n in db.query(models.Note).filter_by(session_id=session_id).order_by(models.Note.timestamp).all()][-6:]
        bug = models.Bug(
            session_id=session_id, charter_id=charter_id, title=p.title[:200], severity=p.severity,
            description=(f"{p.actual}\n\nExpected: {p.expected}\n\nWhat Mirmera checked: {evidence}\n\n"
                         f"Found by the Mirmera exploration agent ({provider.name}/{provider.model}). "
                         "A draft: review it before filing."),
            expected=p.expected, actual=p.actual,
            steps_to_reproduce=([f"Make {p.fault} (e.g. stop the API, or block the request in the browser's dev tools)"]
                                if p.fault else []) + [s["action"]["summary"] if s["type"] == "action"
                                                       else f"open {s['url']}" for s in (edited or [])],
            reproduction_trail=trail, edited_steps=edited,
            environment={"browser": "Chromium (headless)", "url": page_url, "found_by": TESTER,
                         **({"injected_failure": p.fault} if p.fault else {})},
            signature=key, sightings=[], symptom=p.symptom.model_dump(),
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
                    rules: dict[str, str], keep) -> str:
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
        for c in crashes(sigs.failed[f0:], sigs.page_errors[e0:]):
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
        named = []  # (link, rule): a link a rule names as whole words, in a rule about listing or showing
        for rid, text in rules.items():
            t = f" {_norm(text)} "
            if any(w in t.split() for w in LISTING_WORDS):
                named += [(l, (rid, text)) for l in links if len(l["text"]) >= 4
                          and re.search(rf"(?<!\w){re.escape(_norm(l['text']))}(?!\w)", t)]
        if named:  # a rule that says "list" first, then the longest link ("My orders", not "Order")
            place, rule = max(named, key=lambda x: (bool(re.search(r"\blist", x[1][1].lower())), len(x[0]["text"])))
        place = place or next((l for l in links if l["text"].lower().startswith("my ")), None)
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
    from app.sign_in import secrets
    return secrets(cfg)


# ---------- confirming a draft before a tester reads it ----------

async def confirm_drafts(pw, bug_ids: list[str], session_id: str, headless: bool, executable_path: str | None,
                         prepare=None) -> str:
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
            result = await _reruns({"steps": steps}, symptom, CONFIRM_RUNS, headless, executable_path, pw, prepare=prepare)
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
    history: list[dict] = []
    bug_ids: list[str] = []
    reported: set[tuple[str, str]] = set()
    steps: list[dict] = [{"type": "goto", "url": start_url}]
    started = time.monotonic()
    run_timeout = max(RUN_TIMEOUT_S, max_steps * STEP_TIME_S)
    final: dict = {}
    video: str | None = None  # the run's recording, kept in the session's folder

    def now() -> datetime:
        return utcnow()

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
            context = None
            try:
                options = context_options(conditions)
                context = await _recording_context(browser, folder, run_id, options)
                filmed_from = time.monotonic()  # step times are kept against the recording's start
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
                    if before.text():
                        observation += "\n" + defang(before.text())
                    drain_signals()
                    hints = _pending_hints(run_id)
                    today = utcnow().date()
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
                                if bid not in bug_ids:
                                    bug_ids.append(bid)
                                reported.add(key)
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
                    failed_before, page_errors_before = len(signals.failed), len(signals.page_errors)
                    acted_at = time.monotonic()
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
                    if picked:
                        entry["detail"] = picked + ("; " + entry["detail"] if entry["detail"] else "")
                    if a.kind in SUBMITS and entry["status"] == "ok":
                        for f in before.fields:  # a value counts as tried once it has been submitted
                            tried_values.setdefault(f["label"], set()).add(f["value"])
                    # A submit's answer can take a while (a document check, a slow API): wait for what
                    # it sent to come back, up to 10 s, so the answer is read, not "Checking…".
                    await signals.settle(timeout_ms=10_000 if a.kind in SUBMITS else 3_000)
                    if not page.is_closed() and not in_app(page.url, origins):
                        left = page.url
                        await _back_into_app(page, entry["url"], signals)
                        entry["detail"] = (entry["detail"] + "; " if entry["detail"] else "") + \
                            f"that left the app ({origin(left)}), so the browser went back"
                    broke = crashes(signals.failed[failed_before:], signals.page_errors[page_errors_before:])
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
                            rule_text = next((v for k, v in rules.items() if k.upper() == rule_id), "")
                            entry["judged"] = verdict
                            entry["judged_why"] = why
                            entry["judged_means"] = j.answer_means
                            quote = j.answer_quote.strip() or said[0]
                            note = f"Sent: {sent_text(before)[:400]}. The app said: \"{said[0][:160]}\"."
                            if verdict == "breaks":
                                note += f" Breaks {rule_id} (\"{rule_text[:160]}\"): {why}. {j.because}".rstrip()
                                key = ("rule", rule_id)
                                if key not in reported and not broke:  # a crash is drafted below, once
                                    must = "refused" if j.rule_expects == "refuse" else "accepted"
                                    problem = schemas.ExploreProblem(
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
                                        if bid not in bug_ids:
                                            bug_ids.append(bid)
                                        reported.add(key)
                                        entry["status"] = "drafted"
                                        _seen(entry, new)
                                        if g := generalize_hint(before, problem.title):
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
                    for crash in broke:
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
                            if not breaks or ("rule", rid) in reported:
                                continue
                            should, got = ("refused", "accepted") if expects == "refuse" else ("accepted", "refused")
                            await keep(schemas.ExploreProblem(
                                title=f"{kind[:1].upper() + kind[1:]} is {got} although {rid} says it is {should}"[:200],
                                severity="high" if expects == "refuse" else "medium",
                                expected=f"Per {rid} (\"{rtext[:300]}\"), {first} is {should}."[:600],
                                actual=f"With {sent_text(before)[:300]}, the app answered: \"{said[0][:200]}\"."[:600],
                                symptom=schemas.Symptom(kind="text_visible", value=said[0][:300],
                                                        explanation=f"Breaks {rid}: {rtext}"[:600])),
                                ("rule", rid), f". Breaks {rid}: {rtext}")
                            entry["judged"] = "breaks"
                            break

                    if submitted and (said or broke):
                        # What the app did: from its own words first (no AI to misread it), else the check's reading.
                        outcome = outcome_of(said, broke)
                        if outcome == "other" and entry.get("judged_means") in ("accepted", "refused", "error"):
                            outcome = entry["judged_means"]
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
                        submits.append((values, outcome, entry["n"]))
                        if outcome == "accepted":
                            accepted_values += [v for v in kept_values(before) if v not in accepted_values]
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
                    # Slow answers: a tester notices waiting.
                    took = time.monotonic() - acted_at
                    if is_submit and took > 5:
                        _note(session_id, "issue", f"Slow: after {entry['action']} the app took {took:.1f} s to answer.")
                    history.append(entry)
                    stuck = stuck + 1 if entry["status"] in ("refused", "failed", "not verified") else 0
                    _save(run_id, steps=list(history), bug_ids=list(bug_ids))
                    if tour_due:
                        # An accepted submit: walk the flow the ways users do, not only forward (Mirmera's
                        # own steps, no AI, outside the budget).
                        form_url, answer_url, sent_form, to_here = tour_due
                        tour_due = None
                        tour = {"n": len(history) + 1, "thought": "Mirmera's flow tour after an accepted submit: open the "
                                "answer page directly, refresh it, go back, and send the form twice quickly.",
                                "action": "flow tour", "kind": "tour", "status": "ok", "detail": "", "url": answer_url,
                                "by": "Mirmera", "t": round(time.monotonic() - filmed_from, 2)}
                        try:
                            tour["detail"] = await flow_tour(page, context, signals, form_url, answer_url, sent_form, to_here,
                                                             rules_of(ctx), lambda prob, key, on: keep(prob, key, on=(*on, tour)))
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
                                if not is_tried(f, v, tried_values)]
                        _note(session_id, "setup", f"Test ideas tried: {done} of {total}."
                              + (f" Not tried: {'; '.join(left[:8])}" + ("…" if len(left) > 8 else "") if left else ""))
            finally:
                video = await _keep_recording(context, folder, run_id)
                await browser.close()
            if CONFIRM_RUNS and bug_ids and final.get("status") != "cancelled":
                # Before a tester reads a draft: does it happen again from its steps alone, in a
                # browser that saw nothing before? (No AI.)
                confirm = {"n": len(history) + 1, "thought": f"Mirmera replays each new draft's steps {CONFIRM_RUNS} times in "
                           "a fresh browser, to tell a steady bug from a one-off.", "action": "confirm the drafts",
                           "kind": "confirm", "status": "ok", "detail": "", "url": start_url, "by": "Mirmera"}
                try:
                    async def signed_in(fresh_page):  # each fresh browser signs in first, as the run did
                        from app.sign_in import sign_in
                        ok, how = await sign_in(fresh_page, sign_in_cfg, start_url)
                        if not ok:
                            raise RuntimeError(f"couldn't sign in: {how}")
                    confirm["detail"] = await confirm_drafts(pw, bug_ids, session_id, headless, executable_path,
                                                             prepare=signed_in if sign_in_cfg else None)
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
            _wrap_up(session_id, final, history, bug_ids)
            _save(run_id, steps=list(history), bug_ids=list(bug_ids), finished_at=utcnow(), video=video, **final)
        finally:
            ACTIVE.discard(run_id)
            LIVE.pop(run_id, None)  # from here on, its steps' frames show what it did


def _wrap_up(session_id: str, final: dict, history: list[dict], bug_ids: list[str]) -> None:
    """Pauses the session with the agent's summary as a note. A tester reviews it and ends it,
    which drafts the debrief from the agent's notes: the debrief is the tester's."""
    tried = sum(1 for h in history if h["kind"] not in ("note", "problem", "done", "invalid"))
    text = (f"Mirmera agent: {final.get('summary') or final.get('detail') or 'stopped'} "
            f"({tried} actions, {len(bug_ids)} bug draft{'s' if len(bug_ids) != 1 else ''} to review.)")
    with SessionLocal() as db:
        db.add(models.Note(session_id=session_id, tag="setup", content=text[:4000]))
        s = db.get(models.Session, session_id)
        if s and not s.ended_at and not s.paused_at:
            s.paused_at = utcnow()  # the clock stops; testing time is the agent's own
        db.commit()
