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
RUN_TIMEOUT_S = 900
MAX_BAD_REPLIES = 3

ACTIVE: set[str] = set()  # run ids being worked on in this process
VIEWPORT = {"width": 1280, "height": 800}  # fixed, so a target's box can be drawn on any frame


@dataclass
class Live:
    """What the agent's browser shows right now, for watching it work."""
    seq: int = 0
    frame: bytes | None = None
    url: str = ""


LIVE: dict[str, Live] = {}  # run id -> its latest frame, while it runs


async def _start_screencast(page, live: Live) -> None:
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
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 60,
                                              "maxWidth": VIEWPORT["width"], "maxHeight": VIEWPORT["height"]})
    except Exception as err:  # watching is a nicety: the run goes on without it
        logger.info("No live view for this run: %s", _first_line(err))


async def _recording_context(browser, folder: Path, run_id: str):
    """A browser context that films itself (a WebM video, written when the context closes)."""
    try:
        return await browser.new_context(viewport=VIEWPORT, record_video_dir=str(folder / f"video-{run_id[:8]}"),
                                         record_video_size=VIEWPORT)
    except Exception as err:  # e.g. Playwright's ffmpeg isn't installed: explore without a recording
        logger.info("No recording for this run: %s", _first_line(err))
        return await browser.new_context(viewport=VIEWPORT)


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
  for (const el of document.querySelectorAll('input, select, textarea')) {
    const type = (el.type || el.tagName).toLowerCase()
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type) || !visible(el)) continue
    let value
    if (type === 'checkbox' || type === 'radio') value = el.checked ? 'checked' : 'not checked'
    else if (type === 'password') value = el.value ? '(filled)' : ''
    else if (el.tagName === 'SELECT') value = el.selectedOptions[0] ? el.selectedOptions[0].text : ''
    else value = el.value
    fields.push({ label: labelOf(el), type, value, required: el.required })
  }
  const messages = [...document.querySelectorAll('[role=status], [role=alert], [aria-live]')]
    .filter(visible).map((el) => el.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean)
  return { fields, messages }
}"""


@dataclass
class FormState:
    fields: list[dict] = field(default_factory=list)  # label, type, value, required
    messages: list[str] = field(default_factory=list)  # what the page says in its status and alert areas

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
        return FormState(fields=data.get("fields") or [], messages=data.get("messages") or [])
    except Exception:  # navigating, or a page without a DOM to read
        return FormState()


def pointless(a: schemas.ExploreAction, url: str, before: FormState, tried: dict[tuple[str, str, str], int]) -> str | None:
    """Why a step would learn nothing, or None. A small model loops: it submits the same form
    again and again, or fills a field with the value it already holds. Such a step isn't run."""
    if a.kind == "fill" and a.locator:
        current = before.value_of(a.locator.value or a.locator.name)
        if current is not None and current == a.value:
            return (f'that field already holds "{a.value}". Change a different field, or change this one to a '
                    "different value, then submit")
    if a.kind in ("click", "press"):
        key = (describe(a), url, before.values())
        if key in tried:
            return (f"you did exactly this at step {tried[key]} on this page with the same field values, and it can't show "
                    "anything new. Change one field first (keep the others valid), or try another idea")
    return None


def _user_message(ctx: CharterContext, history: list[dict], observation: str, steps_left: int) -> str:
    lines = [f"{h['n']}. {h['action']} -> {h['status']}{': ' + h['detail'] if h['detail'] else ''}"
             for h in history[-HISTORY_LIMIT:]]
    charter = {"mission": ctx.mission, "test_ideas": ctx.test_ideas, "oracles": ctx.oracles, "risks": ctx.risks}
    if ctx.criteria:
        charter["acceptance_criteria"] = [f"AC{i}: {c}" for i, c in enumerate(ctx.criteria, 1)]
    if ctx.story:
        charter["story"] = ctx.story
    return (
        f"<charter>\n{defang(json.dumps(charter, indent=2, ensure_ascii=False))}\n</charter>\n\n"
        f"<history>\n{chr(10).join(lines) or '(no steps yet)'}\n</history>\n\n"
        f"<page>\n{observation}\n</page>\n\n"
        f"Steps left: {steps_left}. Reply with the next single step."
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


async def judge(provider: LLMProvider, ctx: CharterContext, before: FormState, said: list[str], url: str
                ) -> schemas.LLMExploreJudgement | None:
    """Does what the app just answered break one of the story's rules? A separate, focused
    question: a small model that steers the browser often walks past a wrong answer."""
    rules = [f"AC{i}: {c}" for i, c in enumerate(ctx.criteria, 1)] + \
            [f"{r['ref']}: {r['title']} ({r.get('failure_mode') or ''})" for r in ctx.risks]
    user = (f"Today: {utcnow().date().isoformat()}\n\n<rules>\n{defang(chr(10).join(rules))}\n</rules>\n\n"
            f"<sent>\n{defang(sent_text(before)) or '(no form)'}\n</sent>\n\n"
            f"<answer>\nPage: {defang(url)}\n" + "\n".join(f"Says: {defang(m[:300])}" for m in said) + "\n</answer>")
    try:
        return await complete_json(provider, JUDGE_PROMPT, user, schemas.LLMExploreJudgement, task="explore_judge", max_tokens=500)
    except (LLMOutputError, ProviderError) as err:
        logger.info("Couldn't check an answer: %s", _first_line(err))
        return None


def _draft_bug(session_id: str, charter_id: str, p: schemas.ExploreProblem, evidence: str, steps: list[dict],
               page_url: str, screenshot: Path | None, provider: LLMProvider) -> str:
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
            expected=p.expected, actual=p.actual, steps_to_reproduce=[s["action"]["summary"] if s["type"] == "action"
                                                                     else f"open {s['url']}" for s in (edited or [])],
            reproduction_trail=trail, edited_steps=edited,
            environment={"browser": "Chromium (headless)", "url": page_url, "found_by": TESTER},
        )
        db.add(bug)
        db.flush()
        if screenshot and screenshot.is_file():
            db.add(models.Evidence(session_id=session_id, bug_id=bug.id, kind="screenshot",
                                   summary=f"What the agent saw: {p.title}"[:300], url=page_url, file_path=str(screenshot)))
        db.add(models.Note(session_id=session_id, tag="bug", content=f"{p.title}: {p.actual}"[:4000]))
        db.commit()
        return bug.id


# ---------- the run ----------

async def run_agent(run_id: str, ctx: CharterContext, provider: LLMProvider, *, session_id: str, charter_id: str,
                    start_url: str, origins: set[str], max_steps: int, headless: bool, executable_path: str | None,
                    folder: Path) -> None:
    from playwright.async_api import async_playwright

    ACTIVE.add(run_id)
    history: list[dict] = []
    bug_ids: list[str] = []
    reported: set[tuple[str, str]] = set()
    steps: list[dict] = [{"type": "goto", "url": start_url}]
    started = time.monotonic()
    final: dict = {}
    video: str | None = None  # the run's recording, kept in the session's folder

    def now() -> datetime:
        return utcnow()

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
            context = None
            try:
                context = await _recording_context(browser, folder, run_id)
                filmed_from = time.monotonic()  # step times are kept against the recording's start
                page = await context.new_page()
                live = LIVE[run_id] = Live()
                await _start_screencast(page, live)
                signals = Signals()
                signals.attach(page)
                seen_signals: dict[str, int] = {}
                seen: dict[str, int] = {}
                tried: dict[tuple[str, str, str], int] = {}  # (click, page, field values before it) -> step
                await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
                await signals.settle()
                _record(session_id, [Signal("navigation", f"Opened {start_url}", start_url, now())])
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

                for n in range(1, max_steps + 1):
                    if _cancelled(run_id):
                        final = {"status": "cancelled", "detail": "Stopped by a tester."}
                        break
                    if time.monotonic() - started > RUN_TIMEOUT_S:
                        final = {"status": "finished", "detail": f"Stopped: the run took longer than {RUN_TIMEOUT_S // 60} minutes."}
                        break
                    observation = await observe(page, signals, seen)
                    before = await form_state(page)
                    if before.text():
                        observation += "\n" + defang(before.text())
                    drain_signals()
                    try:
                        step = await complete_json(provider, PROMPT, _user_message(ctx, history, observation, max_steps - n + 1),
                                                   schemas.LLMExploreStep, task="explore_step", max_tokens=900)
                    except LLMOutputError as err:
                        bad_replies += 1
                        reason = str(err).split("Details: ", 1)[-1].splitlines()[:3]
                        history.append({"n": len(history) + 1, "thought": "", "action": "(unusable reply)", "kind": "invalid",
                                        "status": "failed", "detail": "; ".join(reason), "url": page.url})
                        _save(run_id, steps=list(history))
                        if bad_replies >= MAX_BAD_REPLIES:
                            raise
                        continue
                    bad_replies = 0
                    a = step.action
                    entry = {"n": len(history) + 1, "thought": step.thought, "action": describe(a), "kind": a.kind,
                             "status": "ok", "detail": "", "url": page.url}
                    entry["t"] = round(time.monotonic() - filmed_from, 2)  # where this step is in the recording
                    if a.kind != "done":
                        entry |= await _step_frame(page, folder, run_id, entry["n"], a)

                    if a.kind == "done":
                        history.append(entry)
                        final = {"status": "finished", "summary": a.summary, "detail": "The agent finished the charter."}
                        break
                    if a.kind == "note":
                        _note(session_id, "test", a.note)
                        history.append(entry)
                        _save(run_id, steps=list(history))
                        continue
                    if a.kind == "problem":
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
                                bug_ids.append(_draft_bug(session_id, charter_id, a.problem, detail, steps, page.url, shot, provider))
                                reported.add(key)
                                entry["status"], entry["detail"] = "drafted", detail
                        history.append(entry)
                        _save(run_id, steps=list(history), bug_ids=list(bug_ids))
                        continue

                    if why := pointless(a, page.url, before, tried):
                        entry["status"], entry["detail"] = "refused", f"not run: {why}"
                        history.append(entry)
                        _save(run_id, steps=list(history))
                        continue
                    if a.kind in ("click", "press"):
                        tried[(describe(a), page.url, before.values())] = entry["n"]
                    browser_action = a.as_browser_action()
                    try:
                        entry["status"], entry["detail"] = await act(page, browser_action, origins)
                    except Exception as err:
                        entry["status"], entry["detail"] = "failed", _first_line(err)
                    await signals.settle()
                    if not page.is_closed() and not in_app(page.url, origins):
                        left = page.url
                        await _back_into_app(page, entry["url"], signals)
                        entry["detail"] = (entry["detail"] + "; " if entry["detail"] else "") + \
                            f"that left the app ({origin(left)}), so the browser went back"
                    said: list[str] = []  # what the app answered this step
                    if entry["status"] == "ok" and not page.is_closed():
                        after = await form_state(page)
                        said = [m for m in after.messages if m not in before.messages]
                        if said:  # what the app answered: kept in the history, so the AI can compare tries
                            entry["detail"] = "the page now says " + "; ".join(f'"{m[:160]}"' for m in said[:3])
                        elif a.kind in ("click", "press") and after.messages:  # a submit answered the same as before
                            said = after.messages
                            entry["detail"] = "the page still says " + "; ".join(f'"{m[:160]}"' for m in said[:3])
                    if entry["status"] == "ok":
                        if a.kind == "goto":
                            steps.append({"type": "goto", "url": a.url})
                            _record(session_id, [Signal("navigation", f"Opened {a.url}", a.url, now())])
                        elif rec := recorded_action(a):
                            steps.append({"type": "action", "action": rec, "url": entry["url"]})
                            _record(session_id, [Signal("action", describe_action(rec), entry["url"], now(), rec)])
                    if said and ctx.has_rules() and a.kind in ("click", "press"):  # a submit, and the app answered
                        j = await judge(provider, ctx, before, said, page.url)
                        if j:
                            entry["judged"] = j.verdict
                            note = f"Sent: {sent_text(before)[:400]}. The app said: \"{said[0][:160]}\"."
                            if j.verdict == "breaks":
                                note += f" Breaks \"{j.criterion}\": {j.why}"
                                key = ("rule", (j.criterion or "").lower())
                                if key not in reported:
                                    problem = schemas.ExploreProblem(
                                        title=j.title, expected=j.expected, actual=j.actual, severity=j.severity,
                                        symptom=schemas.Symptom(kind="text_visible", value=said[0][:300],
                                                                explanation=f"Breaks: {j.criterion}"[:600]))
                                    ok, evidence = await verify(page, problem.symptom, signals)
                                    if ok:
                                        shot = folder / f"explore-{run_id[:8]}-{len(bug_ids) + 1}.png"
                                        try:
                                            await page.screenshot(path=str(shot))
                                        except Exception:
                                            shot = None
                                        bug_ids.append(_draft_bug(session_id, charter_id, problem,
                                                                  f"{evidence}. Breaks: {j.criterion}", steps, page.url, shot, provider))
                                        reported.add(key)
                                        entry["status"] = "drafted"
                                entry["detail"] += f'; breaks "{(j.criterion or "")[:120]}"'
                            elif j.verdict == "consistent":
                                note += f" As the rules expect. {j.why}".rstrip()
                            _note(session_id, "test", note)
                    history.append(entry)
                    _save(run_id, steps=list(history), bug_ids=list(bug_ids))
                else:
                    final = {"status": "finished", "detail": f"Used all {max_steps} steps."}
                drain_signals()
            finally:
                video = await _keep_recording(context, folder, run_id)
                await browser.close()
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
