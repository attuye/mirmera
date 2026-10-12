"""Compare two versions or environments: the same journey, step by step, on both.

A tester who gets a new build asks "what changed?". Mirmera takes a session's journey (what the
tester or the agent did) and does it again on two addresses side by side, say production and
staging, or yesterday's build and today's. After each step it looks at both pages the same way
(where they are, their title and headings, what they say, their fields and buttons, failed
requests and page errors) and lists what differs. No AI: a difference is something both
browsers saw. A difference can become a bug draft, with the other environment as its oracle.
"""
import asyncio
import re
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.orm import Session as DBSession

from app import models
from app.regression.plan import origin
from app.regression.replay import Signals, _first_line, run_step
from app.regression.steps import MAX_STEPS, _action_step

SNAPSHOT_JS = """() => {
  const text = (e) => (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim()
  const seen = (e) => !!(e.offsetWidth || e.offsetHeight || e.getClientRects().length)
  const label = (el) => {
    const id = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
    return text(id || el.closest('label') || { innerText: el.getAttribute('aria-label') || el.name || el.placeholder || '' })
  }
  return {
    title: document.title,
    headings: [...document.querySelectorAll('h1, h2, h3')].filter(seen).map(text).filter(Boolean).slice(0, 12),
    messages: [...document.querySelectorAll('[role=status], [role=alert], [aria-live]')].filter(seen).map(text).filter(Boolean).slice(0, 6),
    fields: [...document.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(seen).map(label).filter(Boolean).slice(0, 30),
    buttons: [...document.querySelectorAll('button, [role=button], input[type=submit]')].filter(seen)
      .map((b) => text(b) || b.value || '').filter(Boolean).slice(0, 20),
    links: [...document.querySelectorAll('a[href]')].filter(seen).map(text).filter(Boolean).slice(0, 30),
  }
}"""


def journey_of(db: DBSession, session: models.Session) -> list[dict]:
    """What was done in the session, as replayable steps: the agent's own steps when the agent
    explored it, else the recorded browser actions from the first page opened."""
    run = (db.query(models.ExplorationRun).filter(models.ExplorationRun.session_id == session.id)
           .order_by(models.ExplorationRun.created_at.desc()).first())
    if run and run.journey:
        return run.journey[:MAX_STEPS]
    ev = (db.query(models.Evidence).filter(models.Evidence.session_id == session.id,
                                           models.Evidence.kind.in_(("navigation", "action")))
          .order_by(models.Evidence.captured_at).all())
    first = next((e.url for e in ev if e.kind == "navigation" and e.url), None)
    actions = [_action_step(e) for e in ev if e.kind == "action" and e.data]
    if not first and actions:
        first = actions[0].get("url")
    return ([{"type": "goto", "url": first}] if first else []) + actions[:MAX_STEPS - 1]


def _norm(s: str) -> str:
    """Text as compared: ids, counts, times and amounts that differ run to run don't count."""
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", s or "")).strip().lower()


def _where(url: str) -> str:
    p = urlsplit(url or "")
    return (p.path or "/") + (f"?{p.query}" if p.query else "")


def differences(a: dict, b: dict, names: tuple[str, str]) -> list[str]:
    """What differs between the two snapshots of one step, in words."""
    na, nb = names
    out = []
    if a["status"] != b["status"]:
        for name, s in ((na, a), (nb, b)):
            if s["status"] != "ok":
                out.append(f"The step {s['status']} on {name}: {s['detail']}" if s["detail"] else f"The step {s['status']} on {name}")
    if a.get("path") is not None and b.get("path") is not None and _norm(a["path"]) != _norm(b["path"]):
        out.append(f"{na} is on {a['path']}, {nb} on {b['path']}")
    if _norm(a.get("title", "")) != _norm(b.get("title", "")):
        out.append(f'The title is "{a.get("title", "")}" on {na}, "{b.get("title", "")}" on {nb}')
    for key, what in (("messages", "says"), ("headings", "has the heading"), ("fields", "has the field"),
                      ("buttons", "has the button"), ("links", "has the link")):
        sa, sb = {_norm(x): x for x in a.get(key, [])}, {_norm(x): x for x in b.get(key, [])}
        for k in sa.keys() - sb.keys():
            out.append(f'Only {na} {what} "{sa[k][:120]}"')
        for k in sb.keys() - sa.keys():
            out.append(f'Only {nb} {what} "{sb[k][:120]}"')
    for key, what in (("failed", "a request failed"), ("page_errors", "the page threw")):
        sa, sb = {_norm(_request(x)): x for x in a.get(key, [])}, {_norm(_request(x)): x for x in b.get(key, [])}
        for k in sa.keys() - sb.keys():
            out.append(f"On {na} only, {what}: {_request(sa[k])[:200]}")
        for k in sb.keys() - sa.keys():
            out.append(f"On {nb} only, {what}: {_request(sb[k])[:200]}")
    return out


RANK = ("failed on", "threw", "request failed", " says ", " is on ", "title", "field", "button", "heading", "link")


def headline(diffs: list[str], wrong: str) -> str:
    """The difference a bug title leads with: what the wrong environment did, the most telling first."""
    mine = [d for d in diffs if wrong in d] or diffs
    d = min(mine, key=lambda x: next((i for i, k in enumerate(RANK) if k in x), len(RANK)))
    for old, new in ((f"On {wrong} only, a request failed: ", ""), (f"On {wrong} only, the page threw: ", "the page throws: "),
                     (f"Only {wrong} says ", "the page says "), (f"Only {wrong} has ", "the page has "),
                     (f"The step failed on {wrong}", "the step fails"), (f"The step skipped on {wrong}", "the step is skipped")):
        if d.startswith(old):
            return new + d[len(old):]
    return d


def _request(s: str) -> str:
    """'POST http://host:1/api/x -> 500' -> 'POST /api/x -> 500': the address differs by environment."""
    m = re.match(r"(\w+) (\S+) -> (.+)$", s or "")
    return f"{m.group(1)} {urlsplit(m.group(2)).path or '/'} -> {m.group(3)}" if m else (s or "")


async def _snapshot(page, signals: Signals, since: dict, status: str, detail: str) -> dict:
    await signals.settle(timeout_ms=5_000)
    try:
        seen = await page.evaluate(SNAPSHOT_JS)
    except Exception:
        seen = {"title": "", "headings": [], "messages": [], "fields": [], "buttons": [], "links": []}
    out = {"status": status, "detail": detail, "path": _where(page.url), **seen,
           "failed": signals.failed[since.get("failed", 0):], "page_errors": signals.page_errors[since.get("page_errors", 0):]}
    since["failed"], since["page_errors"] = len(signals.failed), len(signals.page_errors)
    return out


async def compare(steps: list[dict], envs: list[dict], *, recorded_base: str | None, folder: Path, key: str,
                  executable_path: str | None = None, headless: bool = True, sign_in_cfg: dict | None = None) -> dict:
    """Does the journey on both environments, a step at a time, side by side.
    envs: [{"name", "url"}] × 2. Returns {"steps": [{"n", "description", "a", "b", "differences", "shots"}],
    "summary"}. Screenshots of each step that differs go into folder."""
    from playwright.async_api import async_playwright
    from app.regression.plan import describe
    from app.sign_in import sign_in
    folder.mkdir(parents=True, exist_ok=True)
    names = (envs[0]["name"], envs[1]["name"])
    results = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
        try:
            sides = []
            for env in envs:
                page = await (await browser.new_context(viewport={"width": 1280, "height": 800})).new_page()
                signals = Signals()
                signals.attach(page)
                sides.append({"env": env, "page": page, "signals": signals, "since": {}, "stopped": False})
            if sign_in_cfg:
                async def sign(side):
                    ok, how = await sign_in(side["page"], sign_in_cfg, side["env"]["url"], side["signals"])
                    if not ok:
                        raise RuntimeError(f"couldn't sign in on {side['env']['name']}: {how}")
                await asyncio.gather(*(sign(s) for s in sides))

            async def one(side, step) -> dict:
                if side["stopped"]:
                    return {"status": "not run", "detail": "an earlier step failed", "path": None}
                try:
                    skipped = await run_step(side["page"], step, recorded_base, side["env"]["url"])
                    status, detail = ("skipped", skipped) if skipped else ("ok", "")
                except Exception as err:
                    status, detail = "failed", _first_line(err)
                    side["stopped"] = True
                return await _snapshot(side["page"], side["signals"], side["since"], status, detail)

            before: set[str] = set()  # what already differed after the step before: said once, where it starts
            for n, step in enumerate(steps, 1):
                a, b = await asyncio.gather(one(sides[0], step), one(sides[1], step))
                every = differences(a, b, names)
                diff = [d for d in every if d not in before]
                before = set(every)
                shots = []
                if diff:
                    for side, tag in zip(sides, "ab"):
                        if side["page"].is_closed():
                            continue
                        path = folder / f"compare-{key[:8]}-{n}{tag}.jpg"
                        try:
                            await side["page"].screenshot(path=str(path), type="jpeg", quality=60)
                            shots.append(path.name)
                        except Exception:
                            pass
                results.append({"n": n, "description": describe(step) if step["type"] == "action" else f"open {_where(step['url'])}",
                                "a": a, "b": b, "differences": diff, "shots": shots})
                if sides[0]["stopped"] and sides[1]["stopped"]:
                    break
        finally:
            await browser.close()
    differing = [r for r in results if r["differences"]]
    summary = (f"The same {len(results)} steps on {names[0]} and {names[1]}: "
               + (f"{len(differing)} step{'s differ' if len(differing) != 1 else ' differs'} (step "
                  + ", ".join(str(r['n']) for r in differing[:12]) + ")." if differing else "no difference seen."))
    return {"steps": results, "summary": summary}


def recorded_base_of(steps: list[dict]) -> str | None:
    first = next((s.get("url") for s in steps if s.get("url")), None)
    return origin(first) if first else None
