"""Reproduction agent: an AI drives a headless browser, one step at a time, to
reproduce a bug, and Mirmera checks the result itself.

Each step the model sees the bug report, the tester's recorded actions as
hints, its own history, and the current page (URL, title, the page's
accessibility tree, new errors and failed requests). It answers with ONE
action from a fixed set. Guard rails, all in code:

  - it stays inside the app: a goto elsewhere is refused, and if a click leaves
    the app's origin the browser is sent back;
  - it never sees secrets: it can only name a MIRMERA_SECRET_* variable to
    type, and the value comes from the backend's environment;
  - its claim "reproduced" is checked by Mirmera (the failing request really
    happened, the text really is on the page...); an unchecked claim is
    refused and costs a step;
  - it has a step budget and can be cancelled.

When the bug reproduces, the steps that worked become a plan, which is
replayed several times in fresh browsers to measure how reliably it
reproduces ("2 of 3": intermittent). The plan can become a regression test.

Runs on the runner's background event loop and writes its progress to the
database after every step, so the tester can follow along.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app import models, schemas
from app.ai.base import LLMProvider, ProviderError
from app.ai.llm_json import LLMOutputError, complete_json
from app.database import SessionLocal
from app.evidence_text import _defang as defang
from app.regression.plan import origin
from app.regression.replay import Signals, _first_line, _locator, run_step
from app.timeutil import utcnow

MAX_BAD_REPLIES = 3  # unusable AI replies in a row before the run stops
logger = logging.getLogger(__name__)

PROMPT = (Path(__file__).resolve().parents[1] / "ai" / "prompts" / "reproduce_step.md").read_text(encoding="utf-8")
PROMPT_VERSION = "reproduce-step-v1"
SNAPSHOT_LIMIT = 6000  # characters of the accessibility tree per step
HISTORY_LIMIT = 15  # most recent steps shown to the model
ACTION_TIMEOUT = 5_000
RUN_TIMEOUT_S = 600

ACTIVE: set[str] = set()  # run ids being worked on in this process


@dataclass
class BugContext:
    report: dict  # title, kind, steps, expected, actual, oracle
    hints: list[dict] = field(default_factory=list)  # recorded actions (action dicts)
    pages: list[str] = field(default_factory=list)
    signals: str = ""  # what the recorded browser saw before the bug


def _save(run_id: str, **fields) -> None:
    with SessionLocal() as db:
        run = db.get(models.ReproductionRun, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        db.commit()


def _cancelled(run_id: str) -> bool:
    with SessionLocal() as db:
        run = db.get(models.ReproductionRun, run_id)
        return bool(run and run.cancel_requested)


def in_app(url: str, origins: set[str]) -> bool:
    return url.startswith("about:") or origin(url) in origins


def describe(a: schemas.AgentAction) -> str:
    loc = a.locator.model_dump(exclude_none=True) if a.locator else {}
    target = (f'{loc.get("role")} "{loc.get("name", "")}"' if loc.get("method") == "role"
              else f'{loc.get("method")} "{loc.get("value", "")}"') if loc else ""
    return {
        "goto": f"open {a.url}", "click": f"click {target}", "fill": f'fill {target} = "{a.value}"',
        "fill_secret": f"fill {target} with {a.secret}", "select": f'select "{a.value}" in {target}',
        "check": f"check {target}", "uncheck": f"uncheck {target}", "press": f"press {a.key} in {target}",
        "wait": "wait a second", "reproduced": f"reproduced: {a.symptom.kind if a.symptom else ''} "
                                               f"\"{a.symptom.value if a.symptom else ''}\"",
        "give_up": f"give up: {a.reason}",
    }[a.kind]


def plan_step(a: schemas.AgentAction) -> dict | None:
    """The step as a resolved regression-plan step, for the reruns and tests."""
    if a.kind == "goto":
        return {"type": "goto", "url": a.url, "why": ""}
    loc = a.locator.model_dump(exclude_none=True) if a.locator else None
    act = {"click": {"action": "click"}, "fill": {"action": "fill", "value": a.value},
           "fill_secret": {"action": "fill", "masked": True, "secret": a.secret},
           "select": {"action": "select", "value": a.value}, "check": {"action": "check"},
           "uncheck": {"action": "uncheck"}, "press": {"action": "press", "key": a.key}}.get(a.kind)
    if act is None:
        return None
    return {"type": "action", "action": {**act, "locator": loc, "summary": describe(a)}, "why": ""}


# ---------- seeing ----------

async def observe(page, signals: Signals, seen: dict[str, int]) -> str:
    """URL, title, new errors since the last look, and the accessibility tree."""
    try:
        tree = await page.locator("body").aria_snapshot(timeout=5_000)
    except Exception:
        tree = "(the page structure could not be read)"
    if len(tree) > SNAPSHOT_LIMIT:
        tree = tree[:SNAPSHOT_LIMIT] + "\n… (cut)"
    new = []
    for name, items in (("failed request", signals.failed), ("console error", signals.console_errors),
                        ("page error", signals.page_errors)):
        new += [f"[{name}] {x}" for x in items[seen.get(name, 0):]]
        seen[name] = len(items)
    try:  # a navigation still finishing (e.g. after going back) destroys the page's context
        title = await page.title() if not page.is_closed() else ""
    except Exception:
        title = "(still loading)"
    return defang(
        f"URL: {page.url}\nTitle: {title}\n"
        f"New since the last step:\n{chr(10).join(new) or '(nothing)'}\n"
        f"Page structure (accessibility tree):\n{tree}"
    )


# ---------- checking a claim ----------

async def verify(page, symptom: schemas.Symptom | dict, signals: Signals) -> tuple[bool, str]:
    """Checks the symptom against what really happened. Never trusts the claim."""
    s = symptom if isinstance(symptom, dict) else symptom.model_dump()
    kind, value = s["kind"], s["value"]
    await signals.settle()  # a request the last click sent may still be on its way back
    if kind == "failed_request":
        hits = [x for x in signals.failed if value in x]
        return (True, hits[0]) if hits else (False, f"no failed request matching '{value}' was seen")
    if kind in ("console_error", "page_error"):
        pool = signals.console_errors if kind == "console_error" else signals.page_errors
        hits = [x for x in pool if value in x]
        return (True, hits[0]) if hits else (False, f"no {kind.replace('_', ' ')} containing '{value}' was seen")
    if kind == "url":
        return (value in page.url, f"URL is {page.url}")
    try:
        visible = await page.get_by_text(value).first.is_visible(timeout=2_000)
    except Exception:
        visible = False
    if kind == "text_visible":
        return (True, f'"{value}" is on the page') if visible else (False, f'"{value}" is not visible on the page')
    return (True, f'"{value}" is missing from the page') if not visible else (False, f'"{value}" is on the page')


# ---------- doing ----------

async def act(page, a: schemas.AgentAction, origins: set[str]) -> tuple[str, str]:
    """Performs one action. Returns (status, detail): ok | failed | refused."""
    if a.kind == "goto":
        if not in_app(a.url, origins):
            return "refused", f"{a.url} is outside the app ({', '.join(sorted(origins))}); the agent stays inside it"
        await page.goto(a.url, wait_until="domcontentloaded", timeout=30_000)
        return "ok", ""
    if a.kind == "wait":
        await page.wait_for_timeout(1_000)
        return "ok", ""
    if a.kind == "fill_secret":
        if a.secret not in os.environ:
            return "refused", f"{a.secret} is not set in the backend's environment"
        await _locator(page, a.locator.model_dump(exclude_none=True)).fill(os.environ[a.secret], timeout=ACTION_TIMEOUT)
        return "ok", ""
    target = _locator(page, a.locator.model_dump(exclude_none=True))
    if a.kind == "click":
        await target.click(timeout=ACTION_TIMEOUT)
    elif a.kind == "fill":
        await target.fill(a.value, timeout=ACTION_TIMEOUT)
    elif a.kind == "select":
        await target.select_option(a.value, timeout=ACTION_TIMEOUT)
    elif a.kind == "check":
        await target.check(timeout=ACTION_TIMEOUT)
    elif a.kind == "uncheck":
        await target.uncheck(timeout=ACTION_TIMEOUT)
    elif a.kind == "press":
        await target.press(a.key, timeout=ACTION_TIMEOUT)
    return "ok", ""


async def _back_into_app(page, last_url: str, signals: Signals) -> None:
    """Goes back after the page left the app; if going back fails, reopens the last page inside it."""
    try:
        await page.go_back(timeout=10_000)
    except Exception:
        try:
            await page.goto(last_url, wait_until="domcontentloaded", timeout=30_000)
        except Exception:
            logger.warning("Could not return to %s after leaving the app", last_url)
    await signals.settle()


def _user_message(ctx: BugContext, history: list[dict], observation: str, steps_left: int) -> str:
    lines = [f"{h['n']}. {h['action']} -> {h['status']}{': ' + h['detail'] if h['detail'] else ''}"
             for h in history[-HISTORY_LIMIT:]]
    return (
        f"<bug>\n{json.dumps(ctx.report, indent=2, ensure_ascii=False)}\n</bug>\n\n"
        f"<recorded>\nActions the tester recorded before logging the bug (hints, in order):\n"
        f"{defang(json.dumps(ctx.hints, ensure_ascii=False)) if ctx.hints else '(none recorded)'}\n"
        f"Pages visited: {', '.join(ctx.pages) or '(none)'}\n"
        f"What the page reported then:\n{ctx.signals or '(nothing recorded)'}\n</recorded>\n\n"
        f"<history>\n{chr(10).join(lines) or '(no steps yet)'}\n</history>\n\n"
        f"<page>\n{observation}\n</page>\n\n"
        f"Steps left: {steps_left}. Reply with the next single step."
    )


async def _reruns(plan: dict, symptom: dict, runs: int, headless: bool, executable_path: str | None, pw) -> dict:
    """Replays the found steps in fresh browsers and checks the symptom each time."""
    results = []
    browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
    try:
        for i in range(runs):
            page = await (await browser.new_context()).new_page()
            signals = Signals()
            signals.attach(page)
            try:
                for step in plan["steps"]:
                    skipped = await run_step(page, step, plan.get("recorded_base"), plan.get("base_url"))
                    if skipped:
                        raise RuntimeError(f"step skipped: {skipped}")
                ok, detail = await verify(page, symptom, signals)  # verify waits for the page to settle
                results.append({"run": i + 1, "reproduced": ok, "detail": detail})
            except Exception as err:
                results.append({"run": i + 1, "reproduced": False, "detail": f"could not run: {_first_line(err)}"})
            finally:
                await page.context.close()
    finally:
        await browser.close()
    return {"runs": runs, "reproduced": sum(r["reproduced"] for r in results), "results": results}


async def _replay_known(page, signals: Signals, steps: list[dict], symptom: dict | None, plan_base: str,
                       base_url: str, history: list[dict], run_id: str) -> tuple[list[dict], dict | None]:
    """Replays the steps to the bug Mirmera already has (edited by the tester, picked from
    the recording, or the recorded actions), then checks what shows the bug. Returns the
    steps that ran, and the verified symptom when the bug showed. No AI involved."""
    from app.regression.steps import describe_step
    ran: list[dict] = []
    for step in steps:
        entry = {"n": len(history) + 1, "thought": "Replaying the steps to the bug (no AI)", "action": describe_step(step),
                 "kind": "replay", "status": "ok", "detail": "", "url": page.url}
        try:
            skipped = await run_step(page, step, plan_base, base_url)
        except Exception as err:
            skipped = _first_line(err)
            entry["status"] = "failed"
        if skipped:
            entry["status"], entry["detail"] = entry["status"] if entry["status"] == "failed" else "skipped", \
                f"{skipped}; the AI takes over from here"
            history.append(entry)
            _save(run_id, steps=list(history))
            return ran, None
        history.append(entry)
        ran.append(step)
        _save(run_id, steps=list(history))
    if not symptom:
        return ran, None
    ok, detail = await verify(page, symptom, signals)
    shown = (f"a failed request to {symptom['value']}" if symptom["kind"] == "failed_request" else f'"{symptom["value"]}" on the page')
    history.append({"n": len(history) + 1, "thought": "Checking whether the bug shows after replaying the steps",
                    "action": f"check for {shown}", "kind": "reproduced", "url": page.url,
                    "status": "verified" if ok else "not verified",
                    "detail": detail if ok else f"{detail}; the AI takes over from here"})
    _save(run_id, steps=list(history))
    if not ok:
        return ran, None
    return ran, {**symptom, "explanation": "Replaying the steps to the bug showed it; no AI step was needed.", "evidence": detail}


async def run_agent(run_id: str, ctx: BugContext, provider: LLMProvider, *, start_url: str, origins: set[str],
                    max_steps: int, runs: int, headless: bool, executable_path: str | None,
                    screenshot_path: Path, replay_steps: list[dict] | None = None,
                    replay_symptom: dict | None = None) -> None:
    from playwright.async_api import async_playwright

    ACTIVE.add(run_id)
    history: list[dict] = []
    plan_steps: list[dict] = [{"type": "goto", "url": start_url, "why": "Open the page where the bug starts"}]
    started = time.monotonic()
    final: dict = {}
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
            try:
                page = await (await browser.new_context()).new_page()
                signals = Signals()
                signals.attach(page)
                seen: dict[str, int] = {}
                bad_replies = 0  # unusable AI replies in a row
                ran: list[dict] = []
                if replay_steps:  # known steps first: often the bug shows without any AI step
                    ran, found = await _replay_known(page, signals, replay_steps, replay_symptom, origin(replay_steps[0]["url"]),
                                                     origin(start_url), history, run_id)
                    if ran:
                        plan_steps = list(ran)
                    if found:
                        final = {"status": "reproduced", "symptom": found}
                if not ran:
                    await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)

                for n in (range(1, max_steps + 1) if not final else ()):
                    if _cancelled(run_id):
                        final = {"status": "cancelled", "detail": "Cancelled by the tester."}
                        break
                    if time.monotonic() - started > RUN_TIMEOUT_S:
                        final = {"status": "not_reproduced", "detail": "Stopped: the run took longer than 10 minutes."}
                        break
                    observation = await observe(page, signals, seen)
                    try:
                        step = await complete_json(provider, PROMPT, _user_message(ctx, history, observation, max_steps - n + 1),
                                                   schemas.LLMAgentStep, task="reproduce_step", max_tokens=800)
                    except LLMOutputError as err:
                        # A smaller model got one step's format wrong: record it as a failed step the
                        # agent sees in its history, and let it try again, up to a limit.
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

                    if a.kind == "give_up":
                        entry["status"] = "stopped"
                        history.append(entry)
                        final = {"status": "not_reproduced", "detail": f"The agent gave up: {a.reason}"}
                        break
                    if a.kind == "reproduced":
                        ok, detail = await verify(page, a.symptom, signals)
                        entry["status"], entry["detail"] = ("verified", detail) if ok else ("not verified", detail)
                        history.append(entry)
                        if ok:
                            final = {"status": "reproduced", "symptom": {**a.symptom.model_dump(), "evidence": detail}}
                            break
                        _save(run_id, steps=list(history))
                        continue
                    try:
                        entry["status"], entry["detail"] = await act(page, a, origins)
                    except Exception as err:
                        entry["status"], entry["detail"] = "failed", _first_line(err)
                    await signals.settle()  # the agent sees what its action caused
                    if not page.is_closed() and not in_app(page.url, origins):
                        left = page.url
                        await _back_into_app(page, entry["url"], signals)
                        entry["detail"] = (entry["detail"] + "; " if entry["detail"] else "") + \
                            f"that left the app ({origin(left)}), so the browser went back"
                    if entry["status"] == "ok" and (ps := plan_step(a)):
                        plan_steps.append(ps)
                    history.append(entry)
                    _save(run_id, steps=list(history))
                else:
                    if not final:
                        final = {"status": "not_reproduced", "detail": f"Ran out of steps ({max_steps}) without reproducing it."}

                try:
                    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                    await page.screenshot(path=str(screenshot_path))
                    final["screenshot_path"] = str(screenshot_path)
                except Exception:
                    pass
            finally:
                await browser.close()

            if final.get("status") == "reproduced":
                plan = {"title": ctx.report.get("title", "reproduction"), "base_url": origin(start_url),
                        "recorded_base": origin(replay_steps[0]["url"]) if replay_steps else None,
                        "steps": plan_steps, "assertions": [], "assumptions": []}
                final["plan"] = plan
                _save(run_id, steps=list(history), detail=f"Reproduced. Rerunning the steps {runs} time(s)…")
                rr = await _reruns(plan, final["symptom"], runs, headless, executable_path, pw)
                final["reproducibility"] = rr
                k = rr["reproduced"]
                final["detail"] = (f"Reproduced, and {k} of {runs} reruns reproduced it again"
                                   + (" (intermittent)." if 0 < k < runs else "." if k == runs else
                                      ": the found steps alone do not bring it back; it may need more conditions."))
    except ProviderError as err:
        final = {"status": "error", "detail": f"The AI could not continue: {err}"}
    except Exception as err:
        logger.exception("Reproduction run %s failed", run_id)
        final = {"status": "error", "detail": f"The run failed: {_first_line(err)}"}
    finally:
        ACTIVE.discard(run_id)
        _save(run_id, steps=list(history), finished_at=utcnow(), **final)
