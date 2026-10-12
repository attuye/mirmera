"""The interruptions tour: what happens to a user's work when something else happens meanwhile.

Two interruptions with a clear oracle (no AI):
- Signed out halfway. The form is filled, then the session ends (cookies and storage cleared,
  as when it expires or the user signs out in another tab), then the form is sent. Wrong: the
  server accepting a write with no session at all (anyone could), the page claiming success
  while the server refused, or the page saying nothing.
- The same record in two tabs. On an edit form (its fields come filled from the server), the
  first tab changes one field and saves, then the second tab (still showing the old values)
  changes another field and saves. Wrong: the first change gone afterwards, with no warning
  (a lost update: the second save wrote back everything it had loaded).
"""
import random
import re
from urllib.parse import urlsplit

WRITES = ("POST", "PUT", "PATCH", "DELETE")
SUCCESS = re.compile(r"\b(saved|updated|success|thank|done|created|sent|submitted|placed|booked|confirmed)\b", re.I)
WARNED = re.compile(r"\b(sign(?:ed)? ?in|log(?:ged)? ?in|session|expired|unauthori[sz]ed|forbidden|went wrong|error|failed|"
                    r"try again|changed by|modified|conflict|someone else|reload|out of date|newer)\b", re.I)


def _marker(prefix: str) -> str:
    return prefix + "".join(random.choice("bcdfghjklmnpqrstvwxz") for _ in range(4))


async def _send(page, signals, submit: str) -> tuple[list[str], list[tuple[str, str, int]], bool]:
    """Clicks submit; (what the page newly says, the app's own writes with their status, moved?)."""
    from app.agent.explore import form_state
    home = f"{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}"
    writes: list[tuple[str, str, int]] = []

    def seen(r):
        req = r.request
        if req.method in WRITES and r.url.startswith(home):
            writes.append((req.method, urlsplit(r.url).path or "/", r.status))
    before = await form_state(page)
    page.on("response", seen)
    try:
        await page.get_by_role("button", name=submit, exact=True).first.click(timeout=5_000)
        await signals.settle(timeout_ms=8_000)
        await page.wait_for_timeout(800)
    finally:
        page.remove_listener("response", seen)
    after = await form_state(page)
    new = [m for m in after.messages if m not in before.messages] or [l for l in after.lines if l not in before.lines][:4]
    return new, writes, after.url.split("#")[0] != before.url.split("#")[0]


async def signed_out_halfway(page, context, signals, form_url: str, sent, to_here, keep, *, fill, on_sign_in) -> tuple[str, int]:
    from app import schemas
    from app.agent.explore import crashes
    await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
    await signals.settle()
    rec = to_here[:1] + [{"type": "goto", "url": form_url}] + await fill(page)
    state = await context.storage_state()
    await context.clear_cookies()
    try:
        await page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear() } catch (e) {} }")
    except Exception:
        pass
    e0 = len(signals.page_errors)
    new, writes, moved = await _send(page, signals, sent.submit)
    rec.append({"type": "action", "url": form_url,
                "action": {"action": "click", "locator": {"method": "role", "role": "button", "name": sent.submit}}})
    text = " ".join(new)
    accepted = [w for w in writes if 200 <= w[2] < 300]
    refused = [w for w in writes if w[2] in (401, 403, 419, 440)]
    problems = list(crashes([], signals.page_errors[e0:]))
    fault = f"Sign out in another tab (or let the session expire) after filling the form, then press {sent.submit}"
    if on_sign_in(page.url):
        what = "sent to sign in"
    elif accepted and not refused:
        w = accepted[0]
        claim = next((n for n in new if SUCCESS.search(n)), new[0] if new else None)
        problems.append(schemas.ExploreProblem(
            title=f"{w[0]} {w[1]} is accepted without a session (signed out, it still answered {w[2]})"[:200], severity="high",
            expected="Once the session has ended, the server refuses changes (401) and the page asks the user to sign in.",
            actual=(f"With the cookies and storage cleared, {w[0]} {w[1]} answered {w[2]}"
                    + (f' and the page said "{claim[:150]}"' if claim else "") + ": anyone can make this change."),
            symptom=schemas.Symptom(kind="text_visible", value=claim[:300], explanation="Accepted while signed out")
            if claim else schemas.Symptom(kind="text_absent", value="sign in", explanation="Accepted while signed out")))
        what = f"{w[0]} {w[1]} accepted without a session"
    elif refused and SUCCESS.search(text) and not WARNED.search(text):
        claim = next(n for n in new if SUCCESS.search(n))
        problems.append(schemas.ExploreProblem(
            title=f'Says "{claim[:60]}" although the session had ended'[:200], severity="high",
            expected="When the server refuses because the session ended, the page says so and asks the user to sign in.",
            actual=f"The server answered {refused[0][2]} to {refused[0][0]} {refused[0][1]}, and the page said \"{claim[:150]}\": "
                   "the user thinks it was saved.",
            symptom=schemas.Symptom(kind="text_visible", value=claim[:300], explanation="Success shown after a refusal")))
        what = "claimed success"
    elif refused and not moved and not WARNED.search(text):
        problems.append(schemas.ExploreProblem(
            title="No message when the session has ended", severity="medium",
            expected="When the server refuses because the session ended, the page says so and asks the user to sign in.",
            actual=f"The server answered {refused[0][2]}; nothing on the page said so, and what was typed may be lost.",
            symptom=schemas.Symptom(kind="text_absent", value="sign in", explanation="Silent after a refusal")))
        what = "said nothing"
    else:
        what = "said so" if new else "nothing sent"
    found = 0
    for p in problems:
        p.fault = fault
        p.same_as = f"interrupt:signed-out:{urlsplit(form_url).path}:{p.title[:60]}"
        if await keep(p, ("signed-out", p.title), (page, signals, list(rec))):
            found += 1
    # Back to the run's own session, for what comes next.
    await context.add_cookies(state["cookies"])
    for o in state.get("origins", []):
        if o.get("localStorage"):
            await page.goto(o["origin"], wait_until="domcontentloaded", timeout=30_000)
            await page.evaluate("(items) => items.forEach((i) => localStorage.setItem(i.name, i.value))", o["localStorage"])
    return f"signed out halfway: {what}", found


async def two_tabs(page, context, signals, form_url: str, sent, to_here, keep) -> tuple[str, int]:
    from app import schemas
    from app.agent.explore import form_state, set_field
    await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
    await signals.settle()
    loaded = await form_state(page)
    editable = [f for f in loaded.fields if f["type"] in ("text", "textarea") and f["value"]]
    if len(editable) < 2:
        return "", 0  # not an edit form with two filled fields: nothing to lose
    first, second = editable[0], editable[1]
    other = await context.new_page()
    try:
        await other.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
        await signals.settle()
        mark_a, mark_b = _marker("Tab1"), _marker("Tab2")
        a_value, b_value = f"{first['value']} {mark_a}", f"{second['value']} {mark_b}"
        await set_field(page, first, "fill", a_value)
        said_a, _, _ = await _send(page, signals, sent.submit)
        other_form = await form_state(other)
        second_there = next((f for f in other_form.fields if f["label"] == second["label"]), None)
        if not second_there:
            return "", 0
        await set_field(other, second_there, "fill", b_value)
        from app.regression.replay import Signals
        other_signals = Signals()
        other_signals.attach(other)
        said_b, _, _ = await _send(other, other_signals, sent.submit)
    finally:
        await other.close()
    if WARNED.search(" ".join(said_b)):
        return "two tabs: the second save was warned", 0
    await page.goto(form_url, wait_until="domcontentloaded", timeout=30_000)
    await signals.settle()
    now = {f["label"]: f["value"] for f in (await form_state(page)).fields}
    if mark_a in now.get(first["label"], "") or mark_b not in now.get(second["label"], ""):
        return "two tabs: both changes kept", 0
    rec = to_here[:1] + [{"type": "goto", "url": form_url}]
    prob = schemas.ExploreProblem(
        title=f'Saving in a second tab undoes the change to "{first["label"]}" (a lost update)'[:200], severity="high",
        expected=f'Both changes are kept, or the second save is warned that "{first["label"]}" changed meanwhile.',
        actual=(f'Tab 1 changed "{first["label"]}" to "{a_value[:80]}" and saved. Tab 2, opened before that, changed '
                f'"{second["label"]}" and saved. Now "{first["label"]}" is "{now.get(first["label"], "")[:80]}" again: '
                "tab 1's change is gone, with no warning.")[:600],
        symptom=schemas.Symptom(kind="text_absent", value=mark_a, explanation="The first tab's change is gone"))
    prob.fault = (f'Open the form in two tabs. In the first, change "{first["label"]}" and press {sent.submit}; then in the '
                  f'second, change "{second["label"]}" and press {sent.submit}')
    prob.same_as = f"interrupt:two-tabs:{urlsplit(form_url).path}"
    found = 1 if await keep(prob, ("two-tabs",), (page, signals, rec)) else 0
    return "two tabs: the first change was lost", found


async def interruptions_tour(page, context, signals, form_url: str, sent, to_here, keep, *, fill, signed_in: bool,
                             on_sign_in) -> tuple[str, int]:
    """Returns (what it did, drafts)."""
    if not sent.submit:
        return "", 0
    done, found = [], 0
    if signed_in:
        what, n = await signed_out_halfway(page, context, signals, form_url, sent, to_here, keep, fill=fill, on_sign_in=on_sign_in)
        done.append(what)
        found += n
    what, n = await two_tabs(page, context, signals, form_url, sent, to_here, keep)
    if what:
        done.append(what)
        found += n
    return ("interruptions (" + "; ".join(done) + ")") if done else "", found
