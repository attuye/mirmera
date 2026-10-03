"""Isolate a bug: replay its steps once as they are (the baseline must show the
bug), then once per variation, each changing ONE recorded value: another option
of a dropdown, a blank or plain value in a text field, a past or future date,
a box left unticked. Each variation either makes the bug go away, leaves it, or
is inconclusive (the request the bug shows on was never sent, e.g. the form
refused the change). What remains is a precise statement of what the bug
depends on: "fails only with Passport; the name and phone don't matter".

No AI is involved: the variations come from the recording and the page.
Runs on the runner's background loop, in its own headless browser."""
import copy
import logging
import re
from urllib.parse import urlsplit

from app import models
from app.database import SessionLocal
from app.regression.replay import Signals, _first_line, _locator, run_step
from app.runner import describe_action, describe_locator
from app.timeutil import utcnow

logger = logging.getLogger(__name__)
ACTIVE: set[str] = set()  # run ids being worked on in this process
MAX_VARIANTS = 30
MAX_OPTIONS = 6  # other options tried per dropdown
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------- planning the variations ----------

def _plain_value(label: str, value: str) -> str | None:
    """A plain stand-in for a value that may be what triggers the bug."""
    if any(ord(c) > 127 for c in value):  # non-Latin script, emoji, accents
        return "Abebe Kebede" if "name" in label.lower() else "Test 123"
    if len(value) > 40:
        return value[:10]
    return None


def plan_variants(steps: list[dict], options: dict[int, list[str]]) -> list[dict]:
    """[{"step": index, "field", "change", "replace": new step | None (skip it)}]"""
    out: list[dict] = []
    for i, step in enumerate(steps):
        if step["type"] != "action":
            continue
        a = step["action"]
        field = describe_locator(a.get("locator") or {})
        kind = a.get("action")

        def with_value(v: str) -> dict:
            s = copy.deepcopy(step)
            s["action"]["value"] = v
            s["action"]["summary"] = describe_action(s["action"])
            return s

        if kind == "select":
            current = a.get("value") or ""
            for opt in [o for o in options.get(i, []) if o.strip() and o != current][:MAX_OPTIONS]:
                out.append({"step": i, "field": field, "change": f'"{opt}" instead of "{current}"', "replace": with_value(opt)})
        elif kind == "fill" and not a.get("masked"):
            value = a.get("value") or ""
            if DATE.match(value):
                for alt, word in (("2020-01-01", "a past date"), ("2035-12-31", "a far-future date")):
                    if alt != value:
                        out.append({"step": i, "field": field, "change": f'{word} ({alt}) instead of "{value}"',
                                    "replace": with_value(alt)})
                continue
            plain = _plain_value(field, value)
            if plain:
                out.append({"step": i, "field": field, "change": f'"{plain}" instead of "{value}"', "replace": with_value(plain)})
            if value:
                out.append({"step": i, "field": field, "change": f'left empty instead of "{value}"', "replace": with_value("")})
        elif kind in ("check", "uncheck"):
            out.append({"step": i, "field": field, "change": "left unticked" if kind == "check" else "left ticked",
                        "replace": None})
    return out[:MAX_VARIANTS]


# ---------- one attempt ----------

async def _attempt(browser, steps: list[dict], symptom: dict, plan_base: str, base_url: str,
                   collect_options: bool = False) -> tuple[str, str, dict[int, list[str]]]:
    """Runs the steps once. Returns (result, detail, dropdown options seen):
    result is "bug" (the symptom showed), "gone", "inconclusive" or "error"."""
    page = await (await browser.new_context()).new_page()
    signals = Signals()
    signals.attach(page)
    options: dict[int, list[str]] = {}
    try:
        for i, step in enumerate(steps):
            if collect_options and step["type"] == "action" and step["action"].get("action") == "select":
                try:
                    options[i] = await _locator(page, step["action"]["locator"]).locator("option").all_inner_texts()
                except Exception:
                    options[i] = []
            try:
                skipped = await run_step(page, step, plan_base, base_url)
            except Exception as err:
                return "error", f"step {i + 1} could not run: {_first_line(err)}", options
            if skipped:
                return "error", f"step {i + 1} was skipped: {skipped}", options
        await signals.settle(timeout_ms=5_000)
        if symptom["kind"] == "failed_request":
            path = symptom["value"]
            hits = [f for f in signals.failed if path in f]
            if hits:
                return "bug", hits[0], options
            if not any(path in urlsplit(u).path for u in signals.requested):
                return "inconclusive", f"{path} was never requested, so this says nothing about the bug", options
            return "gone", f"{path} answered without an error", options
        try:
            visible = await page.get_by_text(symptom["value"]).first.is_visible(timeout=2_000)
        except Exception:
            visible = False
        if visible:
            return "bug", f'"{symptom["value"]}" is on the page', options
        return "gone", f'"{symptom["value"]}" did not appear', options
    finally:
        await page.context.close()


# ---------- the run ----------

def _save(run_id: str, **fields) -> None:
    with SessionLocal() as db:
        run = db.get(models.IsolationRun, run_id)
        for k, v in fields.items():
            setattr(run, k, v)
        db.commit()


def _cancelled(run_id: str) -> bool:
    with SessionLocal() as db:
        run = db.get(models.IsolationRun, run_id)
        return bool(run and run.cancel_requested)


def summarise(variants: list[dict]) -> dict:
    """Fields the bug depends on (a change made it go away), fields that didn't matter
    (every change left it), and fields whose changes said nothing."""
    by_field: dict[str, list[dict]] = {}
    for v in variants:
        by_field.setdefault(v["field"], []).append(v)
    depends, irrelevant, unclear = [], [], []
    for field, vs in by_field.items():
        gone = [v["change"] for v in vs if v["result"] == "gone"]
        if gone:
            depends.append({"field": field, "gone_with": gone})
        elif any(v["result"] == "bug" for v in vs):
            irrelevant.append(field)
        else:
            unclear.append(field)
    return {"depends_on": depends, "irrelevant": irrelevant, "inconclusive": unclear}


async def run_isolation(run_id: str, steps: list[dict], symptom: dict, *, base_url: str, plan_base: str,
                        headless: bool, executable_path: str | None) -> None:
    from playwright.async_api import async_playwright

    ACTIVE.add(run_id)
    variants: list[dict] = []
    final: dict = {}
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless, executable_path=executable_path or None)
            try:
                result, detail, options = await _attempt(browser, steps, symptom, plan_base, base_url, collect_options=True)
                baseline = {"result": result, "detail": detail}
                if result != "bug":
                    final = {"status": "error" if result == "error" else "not_reproduced", "baseline": baseline,
                             "detail": ("The steps did not run as recorded, so there is nothing to compare against: "
                                        if result == "error" else
                                        "Replaying the steps as they are did not show the bug, so changes can't be compared: ")
                                       + detail + ". Check the steps (Steps to the bug) and what shows the bug."}
                    return
                plan = plan_variants(steps, options)
                _save(run_id, baseline=baseline, total=len(plan))
                for n, v in enumerate(plan, 1):
                    if _cancelled(run_id):
                        final = {"status": "cancelled", "detail": "Cancelled by the tester."}
                        break
                    changed = [s for j, s in enumerate(steps) if j != v["step"]] if v["replace"] is None else \
                              [v["replace"] if j == v["step"] else s for j, s in enumerate(steps)]
                    r, d, _ = await _attempt(browser, changed, symptom, plan_base, base_url)
                    variants.append({"n": n, "step": v["step"] + 1, "field": v["field"], "change": v["change"],
                                     "result": r, "detail": d})
                    _save(run_id, variants=list(variants))
                else:
                    final = {"status": "done"}
                    if not plan:
                        final["detail"] = ("Nothing to vary: the steps have no dropdowns, text fields, dates or "
                                           "boxes to change. Add or edit steps to try your own variations.")
            finally:
                await browser.close()
    except Exception as err:
        logger.exception("Isolation run %s failed", run_id)
        final = {"status": "error", "detail": f"The run failed: {_first_line(err)}"}
    finally:
        ACTIVE.discard(run_id)
        _save(run_id, variants=list(variants), summary=summarise(variants) if variants else None,
              finished_at=utcnow(), **final)
